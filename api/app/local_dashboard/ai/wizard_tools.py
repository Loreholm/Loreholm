from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional

from fastapi import HTTPException

from .bifrost import (
    _bifrost_error_message,
    _bifrost_models,
    _bifrost_request,
    _extract_chat_content,
    _extract_chat_message_content,
    _extract_first_choice_message,
    _resolve_wizard_model_candidates,
)
from ..core.config import (
    DEFAULT_SCHEMA,
    LOCAL_DASHBOARD_ARCADEDB_HOST,
    LOCAL_DASHBOARD_ARCADEDB_PORT,
    _DATABASE_ID_RE,
)
from ..db.cypher import _require_readonly_query, _run_query, _safe_query, _wait_for_database_ready
from ..db import arcadedb_server
from ..db.arcadedb_bootstrap import bootstrap_database
from ..db.docker_ops import _http_exception_message
from ..db.graph import _database_status, _database_summary, _schema_payload
from ..db.registry import (
    _find_database,
    _load_registry,
    _registry_lock,
    _save_registry,
)
from ..db.schemas import _delete_authored_type, _normalize_schema_block, _upsert_authored_type

_wizard_abort_event = threading.Event()


def _now_iso() -> str:
    from ..core.auth import _now_iso as _auth_now_iso
    return _auth_now_iso()


_WIZARD_SYSTEM_PROMPT = (
    "You are the loreholm local onboarding agent. "
    "Your job is to help the user design and set up their graph database. "
    "You have tools to: list databases, inspect health and both authored/live schema, "
    "edit the authored schema (upsert/delete entity and relationship types), "
    "run any Cypher query (including writes), start/redeploy databases, and deploy a new database. "
    "Be action-oriented: do things rather than asking for permission. "
    "When something isn't working, troubleshoot and fix it yourself — try multiple approaches before asking the user. "
    "Keep replies under 150 words. "
    "Only ask the user a question when you genuinely need information you cannot determine on your own "
    "(e.g. what data domain they want to model). "
    "IMPORTANT tool selection rules: "
    "- deploy_database: ONLY use this to create a brand new database that does not exist yet. "
    "- Schema design: use upsert_entity_type and upsert_relationship_type to populate the authored schema. "
    "This is what the write path validates against, so this is the CORRECT way to define a schema. "
    "NEVER create dummy nodes or edges in the database just to make labels/relationships show up in the live schema — "
    "the live schema is inferred from real data, and the user does not want placeholder entities cluttering their graph. "
    "- run_query: Use this for real data writes (CREATE/MERGE/SET/DELETE), optional indexes/constraints, "
    "and arbitrary Cypher — not for defining what types exist. "
    "After deployment, immediately call the upsert_entity_type / upsert_relationship_type tools to author the schema "
    "the user described, without waiting for confirmation. Every entity/relationship type must have a one-sentence description. "
    "If a connection fails, check sslmode settings and redeploy with sslmode 'disable' if needed. "
    "Do not ask the user to confirm starting or redeploying a database — just do it."
)


def _wizard_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_databases",
                "description": "List registered local databases and current online/offline status.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_database_status",
                "description": "Get health counters and status for a specific database.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_database_schema",
                "description": (
                    "Get both the authored schema (entity_types, relationship_types, "
                    "aliases — what the write path validates against) and the live "
                    "schema inferred from existing ArcadeDB nodes/edges (labels, "
                    "relationships, properties)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_readonly_query",
                "description": "Run a readonly Cypher query and return rows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string"},
                        "cypher": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["cypher"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "upsert_entity_type",
                "description": (
                    "THE correct way to define an entity type. Add or update an "
                    "authored entity type on a database's schema. This edits the "
                    "authored schema stored in the local dashboard registry (NOT "
                    "graph data) — it is what the write path validates against, "
                    "so this is the ONLY legitimate way to declare that a type "
                    "exists. Do NOT use run_query CREATE (n:Label) to 'define' "
                    "types — that creates placeholder graph nodes the user does "
                    "not want. Idempotent on name (case-insensitive)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string"},
                        "name": {
                            "type": "string",
                            "description": "Title-cased type name, e.g. 'Person', 'Sleep Event'.",
                        },
                        "description": {
                            "type": "string",
                            "description": "One-sentence description of what this type represents. Required.",
                        },
                    },
                    "required": ["name", "description"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "upsert_relationship_type",
                "description": (
                    "THE correct way to define a relationship type. Same "
                    "authored-schema write path as upsert_entity_type. Do NOT "
                    "use run_query CREATE ()-[:REL]->() just to make a "
                    "relationship show up — that creates junk edges. Use this "
                    "tool instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string"},
                        "name": {
                            "type": "string",
                            "description": "Title-cased relationship name, e.g. 'Has Event', 'Logged On'.",
                        },
                        "description": {
                            "type": "string",
                            "description": "One-sentence description. Required.",
                        },
                    },
                    "required": ["name", "description"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_entity_type",
                "description": (
                    "Remove an authored entity type from a database's schema. "
                    "Existing graph nodes with that label are NOT deleted — only "
                    "the authored schema entry is removed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_relationship_type",
                "description": (
                    "Remove an authored relationship type from a database's "
                    "schema. Existing graph edges of that type are NOT deleted."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_query",
                "description": (
                    "Run arbitrary Cypher on a database. Use this for REAL data "
                    "writes (CREATE/MERGE/SET/DELETE of actual user data the "
                    "user wants in the graph), for index/constraint DDL, and "
                    "for free-form read queries that need more flexibility than "
                    "run_readonly_query. DO NOT use this to define entity or "
                    "relationship types — the authored schema is edited through "
                    "upsert_entity_type / upsert_relationship_type, not through "
                    "CREATE (n:Label) placeholders. DO NOT seed dummy nodes just "
                    "to make labels show up in the live schema."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string"},
                        "cypher": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["cypher"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "start_database",
                "description": (
                    "Bring a database back online by re-checking the shared "
                    "ArcadeDB server is up and the database is accepting queries. "
                    "Blocks until ArcadeDB accepts queries (up to ~30s) and returns "
                    "`ready: true` when the database is safe to query."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "redeploy_database",
                "description": (
                    "Re-ensure a database exists on the shared ArcadeDB server and "
                    "re-apply the bootstrap DDL (vector indexes, system types). "
                    "Existing data is preserved. Use this when you suspect the "
                    "database's schema drifted from what the dashboard expects, "
                    "or to flip the registered sslmode. "
                    "Blocks until ArcadeDB accepts queries (up to ~30s) and returns "
                    "`ready: true` when the database is safe to query."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string"},
                        "sslmode": {
                            "type": "string",
                            "enum": ["disable", "require"],
                            "description": (
                                "Override the SSL mode for this database connection. "
                                "Use 'disable' if you get 'failed to initialize secure connection' errors."
                            ),
                        },
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "deploy_database",
                "description": (
                    "Create a new database on the shared ArcadeDB server and register it. "
                    "Only call this after the user has explicitly confirmed they want to create a database. "
                    "This call blocks until ArcadeDB accepts queries (up to ~30s) and returns "
                    "`ready: true` when the database is safe to query. If `ready: false` is returned, do "
                    "NOT run queries against it — surface the `warning` to the user instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "database_id": {
                            "type": "string",
                            "description": "Lowercase alphanumeric slug, e.g. 'my-graph'.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Human-readable display name.",
                        },
                        "sslmode": {
                            "type": "string",
                            "enum": ["disable", "require"],
                            "description": "SSL mode for the database connection. Use 'disable' unless the user explicitly requests SSL. Defaults to 'disable'.",
                        },
                    },
                    "required": ["database_id", "name"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str):
        return {}
    text = raw_arguments.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_tool_database_id(
    arguments: dict[str, Any],
    preferred_database_id: Optional[str],
) -> str:
    value = str(arguments.get("database_id") or preferred_database_id or "").strip()
    if not value:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "WIZARD_DATABASE_REQUIRED",
                    "message": "A database_id is required for this tool call.",
                }
            },
        )
    return value


def _wizard_tool_result(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    preferred_database_id: Optional[str],
) -> dict[str, Any]:
    from ..db.registry import _resolve_arcadedb_host

    registry = _load_registry()

    if tool_name == "list_databases":
        rows = [_database_summary(record) for record in registry.get("databases", [])]
        return {"count": len(rows), "databases": rows}

    if tool_name == "get_database_status":
        database_id = _resolve_tool_database_id(arguments, preferred_database_id)
        record = _find_database(registry, database_id)
        _, node_rows = _safe_query(record, "MATCH (n) RETURN count(n) AS node_count;")
        _, edge_rows = _safe_query(record, "MATCH ()-[r]->() RETURN count(r) AS edge_count;")
        return {
            "database_id": database_id,
            "status": _database_status(record),
            "host": _resolve_arcadedb_host(record),
            "port": LOCAL_DASHBOARD_ARCADEDB_PORT,
            "node_count": int(node_rows[0][0]) if node_rows else 0,
            "edge_count": int(edge_rows[0][0]) if edge_rows else 0,
        }

    if tool_name == "get_database_schema":
        database_id = _resolve_tool_database_id(arguments, preferred_database_id)
        record = _find_database(registry, database_id)
        return {
            "database_id": database_id,
            "schema": _schema_payload(record),
            "authored_schema": _normalize_schema_block(record.get("schema")),
        }

    if tool_name == "run_readonly_query":
        database_id = _resolve_tool_database_id(arguments, preferred_database_id)
        cypher = str(arguments.get("cypher", "")).strip()
        params = arguments.get("params")
        parsed_params = params if isinstance(params, dict) else {}
        _require_readonly_query(cypher)
        record = _find_database(registry, database_id)
        columns, rows = _run_query(record, cypher, parsed_params)
        truncated = len(rows) > 50
        safe_rows = rows[:50]
        return {
            "database_id": database_id,
            "columns": columns,
            "rows": safe_rows,
            "row_count": len(rows),
            "truncated_to": 50 if truncated else len(rows),
            "truncated": truncated,
        }

    if tool_name == "run_query":
        database_id = _resolve_tool_database_id(arguments, preferred_database_id)
        cypher = str(arguments.get("cypher", "")).strip()
        params = arguments.get("params")
        parsed_params = params if isinstance(params, dict) else {}
        if not cypher:
            return {"error": {"code": "CYPHER_REQUIRED", "message": "cypher is required."}}
        record = _find_database(registry, database_id)
        columns, rows = _run_query(record, cypher, parsed_params)
        truncated = len(rows) > 50
        return {
            "database_id": database_id,
            "columns": columns,
            "rows": rows[:50],
            "row_count": len(rows),
            "truncated": truncated,
        }

    if tool_name == "start_database":
        database_id = _resolve_tool_database_id(arguments, preferred_database_id)
        record = _find_database(registry, database_id)
        try:
            arcadedb_server.wait_for_server_ready(timeout_s=30.0)
        except RuntimeError as exc:
            return {"error": {"code": "START_FAILED", "message": str(exc)}}
        ready, waited_s, last_error = _wait_for_database_ready(record)
        result: dict[str, Any] = {
            "database_id": database_id,
            "started": True,
            "ready": ready,
            "waited_seconds": round(waited_s, 2),
        }
        if not ready:
            result["warning"] = (
                f"ArcadeDB did not start accepting queries within "
                f"{round(waited_s, 1)}s. Last error: {last_error or 'unknown'}. "
                "Do NOT run queries against this database yet — wait and retry "
                "start_database, or call get_database_status to re-check."
            )
        return result

    if tool_name == "redeploy_database":
        database_id = _resolve_tool_database_id(arguments, preferred_database_id)
        record = _find_database(registry, database_id)
        new_sslmode = str(arguments.get("sslmode", "")).strip().lower() or None
        try:
            arcadedb_server.wait_for_server_ready(timeout_s=30.0)
            arcadedb_server.create_database(database_id)
            bootstrap_database(
                host=LOCAL_DASHBOARD_ARCADEDB_HOST,
                port=LOCAL_DASHBOARD_ARCADEDB_PORT,
                database_id=database_id,
            )
        except RuntimeError as exc:
            return {"error": {"code": "REDEPLOY_FAILED", "message": str(exc)}}
        if new_sslmode in ("disable", "require"):
            with _registry_lock:
                reg = _load_registry()
                for r in reg.get("databases", []):
                    if r.get("database_id") == database_id:
                        r["sslmode"] = new_sslmode
                        r["updated_at"] = _now_iso()
                        break
                _save_registry(reg)
        effective_sslmode = new_sslmode if new_sslmode in ("disable", "require") else record.get("sslmode", "disable")
        refreshed_record = _find_database(_load_registry(), database_id)
        ready, waited_s, last_error = _wait_for_database_ready(refreshed_record)
        result = {
            "redeployed": True,
            "database_id": database_id,
            "host": LOCAL_DASHBOARD_ARCADEDB_HOST,
            "port": LOCAL_DASHBOARD_ARCADEDB_PORT,
            "sslmode": effective_sslmode,
            "ready": ready,
            "waited_seconds": round(waited_s, 2),
            "note": "Database re-ensured on the shared ArcadeDB server and bootstrap DDL re-applied. Existing data is preserved.",
        }
        if not ready:
            result["warning"] = (
                f"ArcadeDB did not start accepting queries within "
                f"{round(waited_s, 1)}s. Last error: {last_error or 'unknown'}. "
                "Do NOT run queries against this database yet — call "
                "get_database_status to re-check or redeploy_database again."
            )
        return result

    if tool_name in ("upsert_entity_type", "upsert_relationship_type"):
        database_id = _resolve_tool_database_id(arguments, preferred_database_id)
        kind = "entity" if tool_name == "upsert_entity_type" else "relationship"
        name = str(arguments.get("name", "") or "").strip()
        description = str(arguments.get("description", "") or "").strip()
        try:
            with _registry_lock:
                reg = _load_registry()
                record = _find_database(reg, database_id)
                result = _upsert_authored_type(
                    record,
                    kind=kind,
                    name=name,
                    description=description,
                )
                _save_registry(reg)
        except HTTPException as exc:
            return {"error": {"message": _http_exception_message(exc)}}
        return {
            "database_id": database_id,
            "kind": kind,
            ("entity_type" if kind == "entity" else "relationship_type"): result,
            "authored_schema": _normalize_schema_block(record.get("schema")),
        }

    if tool_name in ("delete_entity_type", "delete_relationship_type"):
        database_id = _resolve_tool_database_id(arguments, preferred_database_id)
        kind = "entity" if tool_name == "delete_entity_type" else "relationship"
        name = str(arguments.get("name", "") or "").strip()
        if not name:
            return {"error": {"code": "INVALID_NAME", "message": "name is required."}}
        try:
            with _registry_lock:
                reg = _load_registry()
                record = _find_database(reg, database_id)
                removed = _delete_authored_type(record, kind=kind, name=name)
                if removed:
                    _save_registry(reg)
        except HTTPException as exc:
            return {"error": {"message": _http_exception_message(exc)}}
        if not removed:
            return {
                "error": {
                    "code": "TYPE_NOT_FOUND",
                    "message": f"{kind.capitalize()} type '{name}' not found.",
                }
            }
        return {
            "database_id": database_id,
            "kind": kind,
            "deleted": name,
            "authored_schema": _normalize_schema_block(record.get("schema")),
        }

    if tool_name == "deploy_database":
        database_id = str(arguments.get("database_id", "")).strip().lower()
        name = str(arguments.get("name", "")).strip()
        sslmode = str(arguments.get("sslmode", "disable")).strip().lower()
        if not database_id or not _DATABASE_ID_RE.match(database_id):
            return {
                "error": {
                    "code": "INVALID_DATABASE_ID",
                    "message": "database_id must match ^[a-z0-9][a-z0-9_-]{0,99}$.",
                }
            }
        if not name:
            return {"error": {"code": "INVALID_DATABASE_NAME", "message": "name is required."}}
        with _registry_lock:
            reg = _load_registry()
            if any(r.get("database_id") == database_id for r in reg.get("databases", [])):
                return {
                    "error": {
                        "code": "DATABASE_ALREADY_EXISTS",
                        "message": f"database_id '{database_id}' already exists.",
                    }
                }
            try:
                arcadedb_server.wait_for_server_ready(timeout_s=30.0)
                arcadedb_server.create_database(database_id)
                bootstrap_meta = bootstrap_database(
                    host=LOCAL_DASHBOARD_ARCADEDB_HOST,
                    port=LOCAL_DASHBOARD_ARCADEDB_PORT,
                    database_id=database_id,
                )
            except RuntimeError as exc:
                return {
                    "error": {
                        "code": "DATABASE_CREATE_FAILED",
                        "message": str(exc),
                    }
                }
            now = _now_iso()
            record = {
                "database_id": database_id,
                "name": name,
                "profile_id": "memory-default",
                "profile_version": 1,
                "sslmode": "require" if sslmode == "require" else "disable",
                "schema": json.loads(json.dumps(DEFAULT_SCHEMA)),
                "tool_manifest": {},
                "backend": "arcadedb",
                "embedding_model": bootstrap_meta.get("embedding_model"),
                "embedding_dimension": bootstrap_meta.get("embedding_dimension"),
                "created_at": now,
                "updated_at": now,
            }
            reg.setdefault("databases", []).append(record)
            try:
                _save_registry(reg)
            except Exception as exc:
                try:
                    arcadedb_server.drop_database(database_id)
                except Exception:
                    pass
                return {
                    "error": {
                        "code": "REGISTRY_WRITE_FAILED",
                        "message": f"Failed to save registry: {exc}",
                    }
                }
        # Readiness is covered inside bootstrap_database (it runs DDL as part of
        # the create flow, which won't succeed until ArcadeDB accepts queries).
        response: dict[str, Any] = {
            "deployed": True,
            "database_id": database_id,
            "name": name,
            "host": LOCAL_DASHBOARD_ARCADEDB_HOST,
            "port": LOCAL_DASHBOARD_ARCADEDB_PORT,
            "ready": True,
            "waited_seconds": 0.0,
        }
        return response

    return {
        "error": {
            "code": "WIZARD_UNKNOWN_TOOL",
            "message": f"Unknown wizard tool '{tool_name}'.",
        }
    }


# Tools that require explicit user approval before execution
_TOOLS_REQUIRE_APPROVAL: frozenset[str] = frozenset({"deploy_database"})


def _bifrost_chat_completion_with_tools(
    messages: list[dict[str, str]],
    *,
    system_prompt: str,
    preferred_database_id: Optional[str],
    requested_model: Optional[str] = None,
    max_tokens: int = 700,
    temperature: float = 0.2,
    max_rounds: int = 8,
    conversation_state: Optional[list[dict[str, Any]]] = None,
    approved_tool_call_id: Optional[str] = None,
    denied_tool_call_id: Optional[str] = None,
) -> tuple[Optional[str], str, list[dict[str, Any]], Optional[dict[str, Any]]]:
    """Returns (content, model, tool_events, pending_approval).
    When pending_approval is not None, content is None and the caller should surface
    the approval request to the user before resuming."""
    models = _bifrost_models()
    candidate_models = _resolve_wizard_model_candidates(models, requested_model=requested_model)
    tools = _wizard_tool_definitions()
    last_http_error: Optional[HTTPException] = None

    for model in candidate_models:
        # Resume from serialised conversation state, or build fresh from messages
        if conversation_state is not None:
            conversation: list[dict[str, Any]] = list(conversation_state)
        else:
            conversation = [
                {"role": "system", "content": system_prompt},
                *messages,
            ]
        tool_events: list[dict[str, Any]] = []

        # When resuming after an approval/denial, inject results for all tool calls
        # in the last assistant message that haven't been answered yet.
        resuming = conversation_state is not None and (
            approved_tool_call_id is not None or denied_tool_call_id is not None
        )
        if resuming:
            last_assistant = next(
                (
                    m
                    for m in reversed(conversation)
                    if m.get("role") == "assistant" and m.get("tool_calls")
                ),
                None,
            )
            answered_ids = {
                m.get("tool_call_id")
                for m in conversation
                if m.get("role") == "tool"
            }
            if last_assistant:
                for tc in last_assistant.get("tool_calls", []) or []:
                    tc_id = str(tc.get("id", "")).strip() or "tool_call"
                    if tc_id in answered_ids:
                        continue  # already has a result
                    fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                    name = str(fn.get("name", "")).strip()
                    arguments = _parse_tool_arguments(fn.get("arguments"))

                    if name in _TOOLS_REQUIRE_APPROVAL:
                        if tc_id == approved_tool_call_id:
                            try:
                                result = _wizard_tool_result(name, arguments, preferred_database_id=preferred_database_id)
                            except HTTPException as exc:
                                result = {"error": {"message": _bifrost_error_message(exc, str(exc))}}
                            tool_events.append({"tool": name, "arguments": arguments, "ok": not isinstance(result.get("error"), dict)})
                        else:
                            result = {"error": {"code": "USER_DENIED", "message": "The user denied this action."}}
                    else:
                        if not name:
                            result = {"error": {"code": "WIZARD_INVALID_TOOL_CALL", "message": "Tool call missing function name."}}
                        else:
                            try:
                                result = _wizard_tool_result(name, arguments, preferred_database_id=preferred_database_id)
                            except HTTPException as exc:
                                result = {"error": {"message": _bifrost_error_message(exc, str(exc))}}
                            tool_events.append({"tool": name, "arguments": arguments, "ok": not isinstance(result.get("error"), dict)})

                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": name or "unknown",
                        "content": json.dumps(result),
                    })

        try:
            for _ in range(max(1, max_rounds)):
                if _wizard_abort_event.is_set():
                    _wizard_abort_event.clear()
                    return "(Stopped)", model, tool_events, None
                payload = _bifrost_request(
                    "/v1/chat/completions",
                    method="POST",
                    payload={
                        "model": model,
                        "messages": conversation,
                        "tools": tools,
                        "tool_choice": "auto",
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "drop_params": True,
                    },
                )
                message = _extract_first_choice_message(payload)
                content = _extract_chat_message_content(message)
                tool_calls = message.get("tool_calls")

                if isinstance(tool_calls, list) and tool_calls:
                    # Cap the number of tool calls we process so the assistant
                    # message and tool responses always stay in sync.
                    tool_calls = tool_calls[:6]

                    # Check before executing whether any tool requires approval
                    first_restricted = next(
                        (
                            (
                                str(tc.get("id", "")).strip() or "tool_call",
                                str((tc.get("function") or {}).get("name", "") or "").strip(),
                                _parse_tool_arguments((tc.get("function") or {}).get("arguments")),
                            )
                            for tc in tool_calls
                            if str((tc.get("function") or {}).get("name", "") or "").strip()
                            in _TOOLS_REQUIRE_APPROVAL
                        ),
                        None,
                    )

                    conversation.append({
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                    })

                    if first_restricted is not None:
                        tc_id, tc_name, tc_args = first_restricted
                        return None, model, tool_events, {
                            "tool_call_id": tc_id,
                            "tool_name": tc_name,
                            "arguments": tc_args,
                            "conversation_state": conversation,
                        }

                    # All tools are safe — execute them all
                    for tool_call in tool_calls:
                        tool_id = str(tool_call.get("id", "")).strip() or "tool_call"
                        fn = (
                            tool_call.get("function")
                            if isinstance(tool_call.get("function"), dict)
                            else {}
                        )
                        name = str(fn.get("name", "")).strip()
                        arguments = _parse_tool_arguments(fn.get("arguments"))
                        if not name:
                            result = {"error": {"code": "WIZARD_INVALID_TOOL_CALL", "message": "Tool call missing function name."}}
                        else:
                            try:
                                result = _wizard_tool_result(name, arguments, preferred_database_id=preferred_database_id)
                            except HTTPException as exc:
                                result = {"error": {"message": _bifrost_error_message(exc, str(exc))}}
                        tool_events.append({"tool": name or "unknown", "arguments": arguments, "ok": not isinstance(result.get("error"), dict)})
                        conversation.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "name": name or "unknown",
                            "content": json.dumps(result),
                        })
                    continue

                # No tool calls — return the text response (even if empty)
                return content or "", model, tool_events, None

            # Loop exhausted max_rounds — make one final call without tools
            # so the model summarizes what it found instead of a canned message.
            try:
                summary_payload = _bifrost_request(
                    "/v1/chat/completions",
                    method="POST",
                    payload={
                        "model": model,
                        "messages": conversation,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "drop_params": True,
                    },
                )
                summary_content = _extract_chat_content(summary_payload)
                if summary_content:
                    return summary_content, model, tool_events, None
            except HTTPException:
                pass
            return content or "", model, tool_events, None
        except HTTPException as exc:
            last_http_error = exc
            continue

    if last_http_error:
        detail = last_http_error.detail if isinstance(last_http_error.detail, dict) else {}
        error = detail.get("error", {}) if isinstance(detail, dict) else {}
        inner_msg = error.get("message", "") if isinstance(error, dict) else ""
        raise HTTPException(
            status_code=last_http_error.status_code,
            detail={
                "error": {
                    "code": error.get("code", "WIZARD_MODEL_ERROR") if isinstance(error, dict) else "WIZARD_MODEL_ERROR",
                    "message": inner_msg or f"All wizard model candidates failed. Last error: {last_http_error.detail}",
                }
            },
        )

    raise HTTPException(
        status_code=502,
        detail={
            "error": {
                "code": "WIZARD_BIFROST_EMPTY_RESPONSE",
                "message": "Wizard model returned no usable message after tool resolution.",
            }
        },
    )


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    raw = text.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.IGNORECASE | re.DOTALL)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        snippet = raw[start : end + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None
