"""
MCP-compliant JSON-RPC server implementation.

This module implements the official Model Context Protocol (MCP) specification
using JSON-RPC over HTTP with Server-Sent Events (SSE) for remote connections.

Protocol Specification: https://modelcontextprotocol.io/specification/2025-06-18/
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, AsyncIterator, Dict, Optional, Set, Tuple
from datetime import datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.mcp.schemas import (
    ContextRequest,
    DeleteEntitiesRequest,
    DeleteMemoriesRequest,
    EntityInput,
    LinkEntitiesRequest,
    RecentRequest,
    SearchRequest,
    SearchSimilarEntitiesRequest,
    UpsertEntitiesRequest,
    WriteMemoryRequest,
)
from app.services import StoreProtocol, get_user_store
from app.services.api_key_auth import validate_api_key
from app.services.database_targets import (
    get_database_target_for_routing,
    get_schema_for_target,
)
from app.services.graph_store_errors import (
    GraphStorePolicyDeniedError,
    GraphStoreUnavailableError,
)
from app.services.schema_resolver import (
    build_entity_type_resolver,
    entity_type_descriptions,
)
from app.services.staleness import (
    ensure_schema_cached,
    maybe_refresh_target_cache,
)
from app.services.utils import normalize


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-session "did the LLM search before upserting?" tracker
# ---------------------------------------------------------------------------
#
# We do not audit per-row in the DB;
# instead we keep a tiny in-memory cache keyed by (user_id, normalized_query)
# with a short TTL. When `loreholm_upsert_entities` runs, we ask "did we see
# a `search_similar_entities` for this entity name from the same user in the
# last few minutes?" and bump the adoption counter accordingly.
#
# This is best-effort telemetry — we are not gating writes on it.

_SEARCH_TRACK_TTL_SECONDS = 300.0  # 5 minutes
_search_tracker_lock = threading.Lock()
_search_tracker: Dict[Tuple[str, str], float] = {}


def _search_tracker_record(user_id: str, query: str) -> None:
    if not user_id or not query:
        return
    key = (user_id, normalize(query))
    now = time.monotonic()
    with _search_tracker_lock:
        _search_tracker[key] = now + _SEARCH_TRACK_TTL_SECONDS
        # Opportunistic eviction so the dict doesn't grow unbounded over a
        # long-running process. The TTL itself is enforced on read.
        if len(_search_tracker) > 4096:
            stale = [k for k, deadline in _search_tracker.items() if deadline < now]
            for k in stale:
                _search_tracker.pop(k, None)


def _search_tracker_check(user_id: str, name: str) -> bool:
    if not user_id or not name:
        return False
    key = (user_id, normalize(name))
    now = time.monotonic()
    with _search_tracker_lock:
        deadline = _search_tracker.get(key)
        if deadline is None:
            return False
        if deadline < now:
            _search_tracker.pop(key, None)
            return False
        return True

mcp_router = APIRouter(prefix="/mcp/v1")


# MCP Protocol Constants
MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "loreholm"
SERVER_VERSION = "1.0.0"
class MCPError:
    """Standard JSON-RPC error codes."""
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603


def create_jsonrpc_response(id: Any, result: Any = None, error: dict = None) -> dict:
    """Create a JSON-RPC 2.0 response."""
    response = {"jsonrpc": "2.0", "id": id}
    if error:
        response["error"] = error
    else:
        response["result"] = result
    return response


def create_jsonrpc_error(code: int, message: str, data: Any = None) -> dict:
    """Create a JSON-RPC 2.0 error object."""
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return error


async def authenticate_request(request: Request) -> dict:
    """Authenticate via API key and return auth context."""
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise ValueError("Missing X-API-Key header")
    
    payload = await validate_api_key(api_key)
    return {
        "sub": payload["sub"],
        "key_id": payload.get("kid"),
        "database_ref": payload.get("db_ref"),
    }


@mcp_router.post("/")
async def mcp_endpoint(request: Request):
    """
    Main MCP endpoint handling JSON-RPC requests.
    
    Supports:
    - POST: Send JSON-RPC requests (initialize, tools/list, tools/call)
    - GET: Open SSE stream for server-initiated messages
    
    Protocol: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
    """
    try:
        # Authenticate user
        auth_context = await authenticate_request(request)
        
        # Parse JSON-RPC request
        body = await request.json()
        jsonrpc_version = body.get("jsonrpc")
        method = body.get("method")
        params = body.get("params", {})
        request_id = body.get("id")
        
        # Validate JSON-RPC 2.0
        if jsonrpc_version != "2.0":
            return Response(
                content=json.dumps(create_jsonrpc_response(
                    request_id,
                    error=create_jsonrpc_error(
                        MCPError.INVALID_REQUEST,
                        "Invalid JSON-RPC version, expected '2.0'"
                    )
                )),
                media_type="application/json",
                status_code=400
            )
        
        # JSON-RPC notification methods do not expect a response body.
        # MCP clients commonly send notifications/initialized after initialize.
        if method and method.startswith("notifications/"):
            return Response(status_code=202)

        # Route to appropriate handler
        if method == "initialize":
            result = await handle_initialize(params, auth_context)
            return Response(
                content=json.dumps(create_jsonrpc_response(request_id, result=result)),
                media_type="application/json",
                headers={"Mcp-Session-Id": auth_context["sub"]}  # Use user_id as session ID
            )
        
        elif method == "tools/list":
            result = await handle_tools_list(params, auth_context)
            return Response(
                content=json.dumps(create_jsonrpc_response(request_id, result=result)),
                media_type="application/json"
            )
        
        elif method == "tools/call":
            result = await handle_tools_call(params, auth_context)
            return Response(
                content=json.dumps(create_jsonrpc_response(request_id, result=result)),
                media_type="application/json"
            )

        elif method == "resources/list":
            result = await handle_resources_list(params, auth_context)
            return Response(
                content=json.dumps(create_jsonrpc_response(request_id, result=result)),
                media_type="application/json"
            )

        elif method == "resources/templates/list":
            result = await handle_resource_templates_list(params, auth_context)
            return Response(
                content=json.dumps(create_jsonrpc_response(request_id, result=result)),
                media_type="application/json"
            )
        
        else:
            # Unknown JSON-RPC request method.
            return Response(
                content=json.dumps(create_jsonrpc_response(
                    request_id,
                    error=create_jsonrpc_error(
                        MCPError.METHOD_NOT_FOUND,
                        f"Method not found: {method}"
                    )
                )),
                media_type="application/json",
                # JSON-RPC errors are returned in the payload; keep HTTP transport successful.
                status_code=200
            )
    
    except ValueError as e:
        return Response(
            content=json.dumps(create_jsonrpc_response(
                None,
                error=create_jsonrpc_error(
                    MCPError.INVALID_PARAMS,
                    str(e)
                )
            )),
            media_type="application/json",
            status_code=401
        )
    except json.JSONDecodeError:
        return Response(
            content=json.dumps(create_jsonrpc_response(
                None,
                error=create_jsonrpc_error(
                    MCPError.PARSE_ERROR,
                    "Invalid JSON"
                )
            )),
            media_type="application/json",
            status_code=400
        )
    except Exception as e:
        logger.error(f"Internal error: {e}", exc_info=True)
        return Response(
            content=json.dumps(create_jsonrpc_response(
                request_id if 'request_id' in locals() else None,
                error=create_jsonrpc_error(
                    MCPError.INTERNAL_ERROR,
                    "Internal server error"
                )
            )),
            media_type="application/json",
            status_code=500
        )


async def handle_initialize(params: dict, auth_context: dict) -> dict:
    """
    Handle initialize request.
    
    Protocol: https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle
    """
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION
        },
        "capabilities": {
            "tools": {
                "listChanged": False  # We don't emit list_changed notifications yet
            },
            "resources": {
                "subscribe": False,
                "listChanged": False
            }
        }
    }


async def _load_target_schema(auth_context: dict) -> Optional[dict]:
    """Fetch the per-database `schema_json` for the authenticated request.

    Returns None when the API key isn't bound to a server-side database
    target (legacy inline routing) or when the target has never been
    hydrated. The caller is responsible for treating None as "no schema"
    per the Phase 4 tool composition rules.
    """
    target_id = auth_context.get("database_ref")
    if not target_id:
        return None
    try:
        return await get_schema_for_target(target_id)
    except Exception as exc:
        logger.warning(f"Failed to load schema for target {target_id}: {exc}")
        return None


def _compose_entity_type_parameter(
    schema: Optional[dict],
) -> tuple[dict, str]:
    """Build the `type` parameter shape for `loreholm_upsert_entities`
    and a bullet-list blurb for the tool description, based on the
    authored per-database entity types.

    Returns (parameter_schema, description_suffix).
    """
    if schema is None:
        return (
            {
                "type": "string",
                "description": (
                    "Entity type. No schema cached for this database yet; "
                    "the first call will trigger a sync. If your loreholm "
                    "local dashboard is offline, ask the user to bring it "
                    "online before writing."
                ),
            },
            (
                "\n\nNote: the per-database entity schema has not been "
                "synced yet; asking the user to bring their loreholm "
                "local dashboard online will unblock writes."
            ),
        )
    entities = entity_type_descriptions(schema)
    if not entities:
        return (
            {
                "type": "string",
                "enum": [],
                "description": (
                    "No entity types configured for this database. The user "
                    "must author at least one entity type in the local "
                    "dashboard schema editor before memories can be written."
                ),
            },
            (
                "\n\nNote: this database has no entity types configured yet. "
                "Ask the user to open their loreholm local dashboard and "
                "author at least one entity type before attempting to write."
            ),
        )
    names = [entity["name"] for entity in entities]
    bullets = "\n".join(
        f"- {e['name']}: {e['description']}" if e.get("description") else f"- {e['name']}"
        for e in entities
    )
    return (
        {
            "type": "string",
            "enum": names,
            "description": f"Entity type. One of: {' | '.join(names)}",
        },
        f"\n\nEntity types for this database:\n{bullets}",
    )


async def handle_tools_list(params: dict, auth_context: dict) -> dict:
    """
    Handle tools/list request.

    Protocol: https://modelcontextprotocol.io/specification/2025-06-18/server/tools

    Tool descriptions are composed per-database from the authored schema
    cached on `database_targets.schema_json`. There is no legacy global
    vocabulary anymore — callers whose target has no schema get a tool
    description that explicitly says "ask the user to configure one."
    """
    schema = await _load_target_schema(auth_context)
    type_param, type_description_suffix = _compose_entity_type_parameter(schema)
    upsert_description = (
        "Step 3a (write): create or update entities when new durable "
        "context appears. Default behavior is to persist durable context "
        "unless the conversation is marked no-memory/off-record. "
        "Before upserting, prefer calling `loreholm_search_similar_entities` "
        "with the proposed name — if it returns a strong match for the same "
        "thing, pass that `entity_id` as `merge_into` on the corresponding "
        "entity below so the reconciler folds the proposal into the existing "
        "entity instead of relying on background dedup. "
        "Proposals are queued for a reconciler that deduplicates against "
        "existing entities — you get back `staging_id`s, not final "
        "`entity_id`s. Use the returned `staging_id` if you need to reference "
        "the proposal in a follow-up `loreholm_link_entities` or "
        "`loreholm_write_memory` call made in the same turn; the reconciler "
        "resolves staging references during promotion."
        + type_description_suffix
    )
    tools = [
        {
            "name": "loreholm_upsert_entities",
            "description": upsert_description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entities": {
                        "type": "array",
                        "description": "List of entities to create/update",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Entity name"},
                                "type": type_param,
                                "aliases": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Alternative names"
                                },
                                "merge_into": {
                                    "type": "string",
                                    "description": (
                                        "Optional. Existing Entity.id obtained from "
                                        "`loreholm_search_similar_entities` that this "
                                        "proposal refers to. The reconciler honors the "
                                        "hint when the proposal embedding is within the "
                                        "per-database review threshold of that target; "
                                        "otherwise it parks the row as `needs_review`."
                                    )
                                }
                            },
                            "required": ["name", "type"]
                        }
                    }
                },
                "required": ["entities"]
            }
        },
        {
            "name": "loreholm_write_memory",
            "description": (
                "Step 3c (write): store durable memory after retrieval/traversal, "
                "with confidence and source metadata. Use by default for durable "
                "conversation context unless the turn is explicitly "
                "no-memory/off-record. Returns a `staging_id`; the reconciler "
                "promotes the memory once its `about_entity_ids` resolve against "
                "the graph (including staging_ids created in the same turn)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The memory text to store"},
                    "about_entity_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Entity IDs this memory is about"
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Confidence score"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Categorization tags"
                    },
                    "source_ref": {
                        "type": "object",
                        "properties": {
                            "conversation_id": {"type": "string"},
                            "message_ids": {"type": "array", "items": {"type": "string"}},
                            "platform": {"type": "string"},
                            "started_at": {"type": "string", "description": "ISO-8601 timestamp"},
                            "messages": {
                                "type": "array",
                                "description": "Optional message metadata keyed by message id",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "role": {"type": "string"},
                                        "text": {"type": "string"},
                                        "timestamp": {"type": "string", "description": "ISO-8601 timestamp"}
                                    },
                                    "required": ["id"]
                                }
                            }
                        },
                        "required": ["conversation_id", "message_ids"]
                    }
                },
                "required": ["text", "confidence", "source_ref"]
            }
        },
        {
            "name": "loreholm_link_entities",
            "description": (
                "Step 3b (write): create explicit relationships between entities "
                "when new links are stated and expected to remain durable for "
                "future context. Returns a `staging_id`; endpoints may be "
                "committed entity IDs or staging_ids from this turn, and the "
                "reconciler resolves both sides before creating the edge."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "from_entity_id": {"type": "string", "description": "Source entity ID"},
                    "to_entity_id": {"type": "string", "description": "Target entity ID"},
                    "relationship": {"type": "string", "description": "Relationship type (e.g., 'works_on', 'knows')"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "reason": {"type": "string", "description": "Explanation for this relationship"}
                },
                "required": ["from_entity_id", "to_entity_id", "relationship", "confidence", "reason"]
            }
        },
        {
            "name": "loreholm_delete_entities",
            "description": "Maintenance-only tool: delete incorrect/test entities by ID (detaches related edges).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entity_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Entity IDs to delete"
                    }
                },
                "required": ["entity_ids"]
            }
        },
        {
            "name": "loreholm_delete_memories",
            "description": "Maintenance-only tool: delete incorrect/test memories by ID (detaches related edges).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Memory IDs to delete"
                    }
                },
                "required": ["memory_ids"]
            }
        },
        {
            "name": "loreholm_search",
            "description": "Step 1 (retrieve first): search memories before answering or writing new data.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text"},
                    "top_k": {"type": "integer", "default": 10, "description": "Number of results"},
                    "filters": {
                        "type": "object",
                        "properties": {
                            "entity_types": {"type": "array", "items": {"type": "string"}},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "since": {"type": "string", "description": "ISO timestamp"}
                        }
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "loreholm_search_similar_entities",
            "description": (
                "Call before `loreholm_upsert_entities` when the proposal might "
                "already exist. Hybrid (vector + lexical) search over committed "
                "Entity vertices; returns ranked matches with `entity_id`, "
                "`name`, `type`, `aliases`, `similarity`, and `lexical_score`. "
                "If a match clearly refers to the same thing as your proposal, "
                "pass its `entity_id` back as `merge_into` on `loreholm_upsert_entities` "
                "so the reconciler folds the proposal into the existing entity "
                "instead of relying on background dedup. This tool is read-only "
                "— it stages nothing."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Candidate entity name or short phrase to dedupe against."
                    },
                    "top_k": {
                        "type": "integer",
                        "default": 5,
                        "description": "Number of candidate matches to return (default 5)."
                    },
                    "type": {
                        "type": "string",
                        "description": (
                            "Optional Entity type filter. Leave unset when you do "
                            "not yet know the type."
                        )
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "loreholm_context",
            "description": "Step 2 (traverse): after search, navigate related entities and memories for top entity IDs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entity_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Entity IDs to get context for"
                    },
                    "depth": {"type": "integer", "default": 1, "description": "Graph traversal depth"},
                    "limit": {"type": "integer", "default": 20, "description": "Max memories"}
                },
                "required": ["entity_ids"]
            }
        },
        {
            "name": "loreholm_recent",
            "description": "Get recently stored memories, optionally filtered by time",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 50, "description": "Max memories"},
                    "since": {"type": "string", "description": "ISO timestamp filter"}
                }
            }
        },
        {
            "name": "loreholm_stats",
            "description": "Get statistics about the knowledge graph (entity count, memory count, top entities)",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        }
    ]
    
    return {"tools": tools}


async def handle_tools_call(params: dict, auth_context: dict) -> dict:
    """
    Handle tools/call request.

    Protocol: https://modelcontextprotocol.io/specification/2025-06-18/server/tools

    Phase 5 wiring:
      - Snapshot the cached `database_targets` row once at the top so we know
        the currently-cached `profile_hash` / `database_id` / `schema_json`.
      - On cold-start writes (schema_json NULL), block on `ensure_schema_cached`
        to pull the authored schema from the user's local dashboard before
        validating the request against the allowed-type resolver.
      - After the tool runs, compare the observed `profile_hash` on the store
        to the cached value and fire off a background refresh on divergence.
    """
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    if not tool_name:
        raise ValueError("Missing tool name")

    user_id = auth_context["sub"]
    target_id = auth_context.get("database_ref")

    # Pre-fetch the routing record once; reused for schema, database_id, and
    # the post-call staleness compare. A failure here is non-fatal — we fall
    # back to the legacy inline-routing path without staleness detection.
    cached_target: Optional[dict] = None
    if target_id:
        try:
            cached_target = await get_database_target_for_routing(user_id, target_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to load database target %s for staleness check: %s",
                target_id,
                exc,
            )

    store: Optional[StoreProtocol] = None
    try:
        store = await get_user_store(
            user_id,
            target_id,
        )
        if store is None:
            raise RuntimeError(
                "Your database is not connected. Please ensure your node is online."
            )

        if tool_name == "loreholm_upsert_entities":
            entities = [EntityInput(**entity) for entity in arguments.get("entities", [])]
            req = UpsertEntitiesRequest(entities=entities)
            # Write path: must have a schema to canonicalize entity types.
            # Cold-start — schema_json is NULL — means we've never synced this
            # target. Block on a resolve pull before touching the graph so the
            # write can be validated against the authored vocabulary.
            schema: Optional[dict] = (
                cached_target.get("schema_json") if cached_target else None
            )
            if schema is None and target_id:
                hydrated = await ensure_schema_cached(user_id, target_id)
                if hydrated is not None:
                    cached_target = hydrated
                    schema = hydrated.get("schema_json")
            allowed = build_entity_type_resolver(schema)
            payloads: list[dict[str, object]] = []
            for entity in req.entities:
                dumped = entity.model_dump()
                # Stamp each
                # proposal with whether the same MCP user called
                # `search_similar_entities` for this name in the last
                # ~5 minutes. The flag rides through onto the Staging row
                # and the reconciler audit log so the local dashboard can
                # surface adoption without round-tripping cloud-side state.
                had_prior_search = _search_tracker_check(user_id, entity.name)
                dumped["had_prior_search"] = had_prior_search
                payloads.append(dumped)
            records = store.upsert_entities(
                payloads,
                allowed_entity_types=allowed,
            )
            # ArcadeDB returns a staging envelope `{staged, message,
            # reconciler_lag_seconds, needs_review_count}`. Legacy list
            # responses are still accepted for forward-compat.
            if isinstance(records, dict):
                envelope = records
            else:
                envelope = {"entities": records}
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(envelope, indent=2)
                    }
                ],
                "isError": False
            }

        elif tool_name == "loreholm_write_memory":
            req = WriteMemoryRequest(**arguments)
            record = store.write_memory(req.model_dump())
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(record, indent=2)
                    }
                ],
                "isError": False
            }

        elif tool_name == "loreholm_link_entities":
            req = LinkEntitiesRequest(**arguments)
            edge = store.link_entities(req.model_dump())
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(edge, indent=2)
                    }
                ],
                "isError": False
            }

        elif tool_name == "loreholm_delete_entities":
            req = DeleteEntitiesRequest(**arguments)
            result = store.delete_entities(req.entity_ids)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2)
                    }
                ],
                "isError": False
            }

        elif tool_name == "loreholm_delete_memories":
            req = DeleteMemoriesRequest(**arguments)
            result = store.delete_memories(req.memory_ids)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2)
                    }
                ],
                "isError": False
            }
        
        elif tool_name == "loreholm_search":
            req = SearchRequest(**arguments)
            search_result = store.search(
                query=req.query,
                top_k=req.top_k,
                entity_types=req.filters.entity_types if req.filters else None,
                tags=req.filters.tags if req.filters else None,
                since=req.filters.since if req.filters else None,
                include_meta=True,
            )
            if isinstance(search_result, dict):
                items = search_result.get("items", [])
                retrieval_mode = search_result.get("retrieval_mode")
                diagnostics = search_result.get("diagnostics")
            else:
                items = search_result
                retrieval_mode = None
                diagnostics = None
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "items": items,
                                "retrieval_mode": retrieval_mode,
                                "diagnostics": diagnostics,
                            },
                            indent=2,
                        )
                    }
                ],
                "isError": False
            }
        
        elif tool_name == "loreholm_search_similar_entities":
            req = SearchSimilarEntitiesRequest(**arguments)
            # Mark this query as "the LLM searched first" so a follow-up
            # `loreholm_upsert_entities` call from the same user with a
            # matching entity name is counted as adoption rather than a
            # blind upsert.
            _search_tracker_record(user_id, req.query)
            result = store.search_similar_entities(
                query=req.query,
                top_k=req.top_k,
                type=req.type,
                include_meta=True,
            )
            if isinstance(result, dict):
                items = result.get("items", [])
                diagnostics = result.get("diagnostics")
            else:
                items = result
                diagnostics = None
            count = len(items)
            if count == 0:
                message = (
                    "No similar entities found. Safe to call "
                    "`loreholm_upsert_entities` without `merge_into`."
                )
            else:
                message = (
                    f"Found {count} candidate entit{'y' if count == 1 else 'ies'}. "
                    "If one refers to the same thing as your proposal, pass its "
                    "`entity_id` as `merge_into` on `loreholm_upsert_entities`."
                )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "matches": items,
                                "message": message,
                                "diagnostics": diagnostics,
                            },
                            indent=2,
                        )
                    }
                ],
                "isError": False
            }

        elif tool_name == "loreholm_context":
            req = ContextRequest(**arguments)
            result = store.context(
                entity_ids=req.entity_ids,
                depth=req.depth,
                limit=req.limit,
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2)
                    }
                ],
                "isError": False
            }
        
        elif tool_name == "loreholm_recent":
            req = RecentRequest(**arguments) if arguments else RecentRequest()
            items = store.recent(
                limit=req.limit,
                since=req.since,
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"items": items}, indent=2)
                    }
                ],
                "isError": False
            }
        
        elif tool_name == "loreholm_stats":
            stats = store.stats()
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(stats, indent=2)
                    }
                ],
                "isError": False
            }
        
        else:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Unknown tool: {tool_name}"
                    }
                ],
                "isError": True
            }
    
    except ValidationError as e:
        logger.error(f"Tool validation error: {e}", exc_info=True)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Validation error: {e.errors()}"
                }
            ],
            "isError": True
        }
    except GraphStorePolicyDeniedError as e:
        # The user's local dashboard rejected the query under a policy
        # (read-only key, rate limit, etc.). Surface the reason to the LLM
        # so it can prompt the user to adjust access.
        logger.info(f"Tool blocked by local policy: {e.rule}: {e.reason}")
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Your local dashboard rejected this operation "
                        f"({e.rule}): {e.reason}. Ask the user to authorize "
                        "this action or adjust their local policies.json."
                    )
                }
            ],
            "isError": True
        }
    except GraphStoreUnavailableError as e:
        logger.warning(f"Local dashboard proxy unavailable: {e}")
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "The user's local database dashboard is not reachable. "
                        "Ask the user to check that their loreholm local "
                        f"dashboard is running. Details: {e}"
                    )
                }
            ],
            "isError": True
        }
    except Exception as e:
        logger.error(f"Tool execution error: {e}", exc_info=True)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: {str(e)}"
                }
            ],
            "isError": True
        }
    finally:
        # Phase 5: if the tool actually touched the store and observed a
        # different profile_hash than the one cached on database_targets,
        # fire off a background refresh so the *next* request sees the
        # updated schema / tool_manifest. Safe to run on error paths too —
        # the helper no-ops on missing inputs or when hashes match.
        if store is not None and cached_target is not None:
            try:
                maybe_refresh_target_cache(
                    user_id=user_id,
                    target_id=target_id,
                    database_id=cached_target.get("database_id"),
                    observed_hash=store.last_profile_hash,
                    cached_hash=cached_target.get("profile_hash"),
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Post-call staleness refresh trigger failed: %s", exc
                )


async def handle_resources_list(params: dict, auth_context: dict) -> dict:
    """
    Handle resources/list request.

    We currently expose no MCP resources, but support the method so clients
    that probe resources can connect without startup errors.
    """
    return {"resources": []}


async def handle_resource_templates_list(params: dict, auth_context: dict) -> dict:
    """
    Handle resources/templates/list request.

    We currently expose no MCP resource templates.
    """
    return {"resourceTemplates": []}
