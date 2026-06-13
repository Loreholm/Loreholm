from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Request

from app.mcp.schemas import (
    ContextRequest,
    ContextResponse,
    DeleteEntitiesRequest,
    DeleteEntitiesResponse,
    DeleteMemoriesRequest,
    DeleteMemoriesResponse,
    ExecuteToolRequest,
    ExecuteToolResponse,
    LinkEntitiesRequest,
    LinkEntitiesResponse,
    ListToolsResponse,
    RecentRequest,
    RecentResponse,
    SearchRequest,
    SearchResponse,
    SearchSimilarEntitiesRequest,
    SearchSimilarEntitiesResponse,
    StatsResponse,
    ToolDefinition,
    ToolParameter,
    UpsertEntitiesRequest,
    UpsertEntitiesResponse,
    WriteMemoryRequest,
    WriteMemoryResponse,
)
from app.services import StoreProtocol, get_user_store
from app.services.database_targets import get_schema_for_target
from app.services.schema_resolver import build_entity_type_resolver
from app.onboarding.router import get_current_user
from app.services.api_key_auth import validate_api_key


router = APIRouter(prefix="/mcp")


async def get_current_user_or_api_key(request: Request) -> dict:
    """Authenticate via API key or Auth0 JWT.
    
    Checks for API key first (X-API-Key header), then falls back to Auth0 JWT.
    This allows MCP clients to use simple API keys while the dashboard uses JWTs.
    
    Returns:
        dict with at least 'sub' (user ID) and optionally 'email'
    """
    # Check for API key first (preferred for MCP clients)
    api_key = request.headers.get("X-API-Key")
    if api_key:
        try:
            payload = await validate_api_key(api_key)
            return {
                "sub": payload["sub"],
                "email": payload.get("email", ""),
                "auth_method": "api_key",
                "key_id": payload.get("kid"),
                "database_ref": payload.get("db_ref"),
            }
        except ValueError as e:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": {
                        "code": "INVALID_API_KEY",
                        "message": str(e),
                    }
                },
            )
    
    # Fall back to Auth0 JWT
    user = await get_current_user(request)
    user["auth_method"] = "jwt"
    return user


async def get_user_database(request: Request) -> StoreProtocol:
    """Return the caller's BYODB store.

    Authenticates via API key or Auth0 JWT, resolves the user's personal
    database via the Tailnet, and returns a connected ArcadeDB store.
    """
    user = await get_current_user_or_api_key(request)
    user_id = user["sub"]

    store = await get_user_store(
        user_id,
        user.get("database_ref"),
    )
    if store is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "Your database is not connected. Please ensure your node is online.",
                }
            },
        )
    return store


def _context_block(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(f"- {line}" for line in lines)


@router.post(
    "/loreholm_upsert_entities",
    response_model=UpsertEntitiesResponse,
)
async def loreholm_upsert_entities(
    request: Request,
    payload: UpsertEntitiesRequest,
) -> UpsertEntitiesResponse:
    memory_store = await get_user_database(request)
    user = await get_current_user_or_api_key(request)
    target_id = user.get("database_ref")
    schema = await get_schema_for_target(target_id) if target_id else None
    allowed = build_entity_type_resolver(schema)
    try:
        records = memory_store.upsert_entities(
            [e.model_dump() for e in payload.entities],
            allowed_entity_types=allowed,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_ENTITY_TYPE",
                    "message": str(exc),
                }
            },
        ) from exc
    # ArcadeDB returns a dict envelope with `staged`, `message`, and
    # reconciler-observability fields.
    if isinstance(records, dict):
        return UpsertEntitiesResponse(**records)
    return UpsertEntitiesResponse(entities=records)


@router.post(
    "/loreholm_write_memory",
    response_model=WriteMemoryResponse,
)
async def loreholm_write_memory(
    request: Request,
    payload: WriteMemoryRequest,
) -> WriteMemoryResponse:
    memory_store = await get_user_database(request)
    try:
        record = memory_store.write_memory(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_ENTITY_ID",
                    "message": str(exc),
                }
            },
        ) from exc
    return WriteMemoryResponse(**record)


@router.post(
    "/loreholm_link_entities",
    response_model=LinkEntitiesResponse,
)
async def loreholm_link_entities(
    request: Request,
    payload: LinkEntitiesRequest,
) -> LinkEntitiesResponse:
    memory_store = await get_user_database(request)
    try:
        edge = memory_store.link_entities(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_ENTITY_ID",
                    "message": str(exc),
                }
            },
        ) from exc
    return LinkEntitiesResponse(**edge)


@router.post(
    "/loreholm_delete_entities",
    response_model=DeleteEntitiesResponse,
)
async def loreholm_delete_entities(
    request: Request,
    payload: DeleteEntitiesRequest,
) -> DeleteEntitiesResponse:
    memory_store = await get_user_database(request)
    result = memory_store.delete_entities(payload.entity_ids)
    return DeleteEntitiesResponse(**result)


@router.post(
    "/loreholm_delete_memories",
    response_model=DeleteMemoriesResponse,
)
async def loreholm_delete_memories(
    request: Request,
    payload: DeleteMemoriesRequest,
) -> DeleteMemoriesResponse:
    memory_store = await get_user_database(request)
    result = memory_store.delete_memories(payload.memory_ids)
    return DeleteMemoriesResponse(**result)


@router.post(
    "/loreholm_search",
    response_model=SearchResponse,
)
async def loreholm_search(
    request: Request,
    payload: SearchRequest,
) -> SearchResponse:
    memory_store = await get_user_database(request)
    filters = payload.filters
    search_result = memory_store.search(
        query=payload.query,
        top_k=payload.top_k,
        entity_types=filters.entity_types if filters else None,
        tags=filters.tags if filters else None,
        since=filters.since if filters else None,
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
    context_lines: list[str] = []
    for item in items:
        context_lines.append(f"{item['text']} (conf {item['confidence']:.2f})")
    return SearchResponse(
        items=items,
        suggested_context_block=_context_block(context_lines),
        retrieval_mode=retrieval_mode,
        diagnostics=diagnostics,
    )


@router.post(
    "/loreholm_search_similar_entities",
    response_model=SearchSimilarEntitiesResponse,
)
async def loreholm_search_similar_entities(
    request: Request,
    payload: SearchSimilarEntitiesRequest,
) -> SearchSimilarEntitiesResponse:
    memory_store = await get_user_database(request)
    result = memory_store.search_similar_entities(
        query=payload.query,
        top_k=payload.top_k,
        type=payload.type,
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
    return SearchSimilarEntitiesResponse(
        matches=items,
        message=message,
        diagnostics=diagnostics,
    )


@router.post(
    "/loreholm_context",
    response_model=ContextResponse,
)
async def loreholm_context(
    request: Request,
    payload: ContextRequest,
) -> ContextResponse:
    memory_store = await get_user_database(request)
    result = memory_store.context(
        entity_ids=payload.entity_ids, depth=payload.depth, limit=payload.limit
    )
    memory_items = result["memories"]
    entity_items = result["entities"]
    context_lines = [
        f"{memory['text']} (conf {memory['confidence']:.2f})"
        for memory in memory_items
    ]
    return ContextResponse(
        memories=memory_items,
        entities=entity_items,
        suggested_context_block=_context_block(context_lines),
    )


@router.post(
    "/loreholm_recent",
    response_model=RecentResponse,
)
async def loreholm_recent(
    request: Request,
    payload: RecentRequest,
) -> RecentResponse:
    memory_store = await get_user_database(request)
    items = memory_store.recent(limit=payload.limit, since=payload.since)
    return RecentResponse(items=items)


@router.post(
    "/loreholm_stats",
    response_model=StatsResponse,
)
async def loreholm_stats(
    request: Request,
) -> StatsResponse:
    memory_store = await get_user_database(request)
    stats = memory_store.stats()
    return StatsResponse(**stats)


@router.get(
    "/tools",
    response_model=ListToolsResponse,
)
async def list_tools(
    request: Request,
) -> ListToolsResponse:
    """List all available MCP tools with their schemas.
    
    Returns tool definitions in a format suitable for AI models to understand
    what functions they can call and what parameters each function requires.
    """
    # Authenticate without forcing a database connection.
    await get_current_user_or_api_key(request)
    
    tools = [
        ToolDefinition(
            name="loreholm_upsert_entities",
            description="Step 3a (write): create or update entities when new durable context appears. Default behavior is to persist durable context unless the conversation is marked no-memory/off-record.",
            parameters=[
                ToolParameter(
                    name="entities",
                    type="array",
                    description="List of entities to create/update. Each entity has: name (string), type (Person|Project|Tool|Concept|Place|Other; case-insensitive), aliases (array of strings)",
                    required=True
                )
            ]
        ),
        ToolDefinition(
            name="loreholm_write_memory",
            description="Step 3c (write): store durable memory after retrieval/traversal, with confidence and source metadata. Use by default for durable conversation context unless the turn is explicitly no-memory/off-record.",
            parameters=[
                ToolParameter(
                    name="text",
                    type="string",
                    description="The memory text to store",
                    required=True
                ),
                ToolParameter(
                    name="about_entity_ids",
                    type="array",
                    description="List of entity IDs this memory is about",
                    required=False
                ),
                ToolParameter(
                    name="confidence",
                    type="number",
                    description="Confidence score between 0.0 and 1.0",
                    required=True
                ),
                ToolParameter(
                    name="tags",
                    type="array",
                    description="Tags for categorizing the memory",
                    required=False
                ),
                ToolParameter(
                    name="source_ref",
                    type="object",
                    description="Source reference with conversation_id, message_ids, and optional platform/started_at/messages[{id,role,text,timestamp}] metadata",
                    required=True
                )
            ]
        ),
        ToolDefinition(
            name="loreholm_link_entities",
            description="Step 3b (write): create explicit relationships between entities when new links are stated and expected to remain durable for future context.",
            parameters=[
                ToolParameter(
                    name="from_entity_id",
                    type="string",
                    description="Source entity ID",
                    required=True
                ),
                ToolParameter(
                    name="to_entity_id",
                    type="string",
                    description="Target entity ID",
                    required=True
                ),
                ToolParameter(
                    name="relationship",
                    type="string",
                    description="Type of relationship (e.g., 'works_on', 'knows', 'uses')",
                    required=True
                ),
                ToolParameter(
                    name="confidence",
                    type="number",
                    description="Confidence score between 0.0 and 1.0",
                    required=True
                ),
                ToolParameter(
                    name="reason",
                    type="string",
                    description="Explanation for this relationship",
                    required=True
                )
            ]
        ),
        ToolDefinition(
            name="loreholm_delete_entities",
            description="Maintenance-only tool: delete incorrect/test entities by ID (detaches related edges).",
            parameters=[
                ToolParameter(
                    name="entity_ids",
                    type="array",
                    description="List of entity IDs to delete",
                    required=True
                )
            ]
        ),
        ToolDefinition(
            name="loreholm_delete_memories",
            description="Maintenance-only tool: delete incorrect/test memories by ID (detaches related edges).",
            parameters=[
                ToolParameter(
                    name="memory_ids",
                    type="array",
                    description="List of memory IDs to delete",
                    required=True
                )
            ]
        ),
        ToolDefinition(
            name="loreholm_search",
            description="Step 1 (retrieve first): search memories before answering or writing new data.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Search query text",
                    required=True
                ),
                ToolParameter(
                    name="top_k",
                    type="integer",
                    description="Number of results to return (default: 10)",
                    required=False,
                    default="10"
                ),
                ToolParameter(
                    name="filters",
                    type="object",
                    description="Optional filters: entity_types (array), tags (array), since (ISO timestamp)",
                    required=False
                )
            ]
        ),
        ToolDefinition(
            name="loreholm_search_similar_entities",
            description="Call before loreholm_upsert_entities when the proposal might already exist. Returns ranked Entity candidates so the LLM can pass entity_id back as merge_into.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Candidate entity name or short phrase to dedupe against",
                    required=True
                ),
                ToolParameter(
                    name="top_k",
                    type="integer",
                    description="Number of candidate matches (default: 5)",
                    required=False,
                    default="5"
                ),
                ToolParameter(
                    name="type",
                    type="string",
                    description="Optional Entity type filter",
                    required=False
                )
            ]
        ),
        ToolDefinition(
            name="loreholm_context",
            description="Step 2 (traverse): after search, navigate related entities and memories for top entity IDs.",
            parameters=[
                ToolParameter(
                    name="entity_ids",
                    type="array",
                    description="List of entity IDs to get context for",
                    required=True
                ),
                ToolParameter(
                    name="depth",
                    type="integer",
                    description="How many hops to traverse in the graph (default: 1)",
                    required=False,
                    default="1"
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum number of memories to return (default: 20)",
                    required=False,
                    default="20"
                )
            ]
        ),
        ToolDefinition(
            name="loreholm_recent",
            description="Get recently stored memories, optionally filtered by time",
            parameters=[
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum number of memories to return (default: 50)",
                    required=False,
                    default="50"
                ),
                ToolParameter(
                    name="since",
                    type="string",
                    description="ISO timestamp to filter memories after this time",
                    required=False
                )
            ]
        ),
        ToolDefinition(
            name="loreholm_stats",
            description="Get statistics about the knowledge graph (entity count, memory count, top entities)",
            parameters=[]
        )
    ]
    
    return ListToolsResponse(tools=tools)


@router.post(
    "/execute",
    response_model=ExecuteToolResponse,
)
async def execute_tool(
    request: Request,
    payload: ExecuteToolRequest,
) -> ExecuteToolResponse:
    """Execute a tool call from an AI model.
    
    Routes the tool call to the appropriate MCP endpoint and returns the result.
    This allows AI models to dynamically call any available MCP tool.
    """
    tool_name = payload.tool_name
    params = payload.parameters
    
    try:
        memory_store = await get_user_database(request)
        
        # Route to the appropriate tool handler
        if tool_name == "loreholm_upsert_entities":
            from app.mcp.schemas import UpsertEntitiesRequest, EntityInput
            entities = [EntityInput(**e) for e in params.get("entities", [])]
            req = UpsertEntitiesRequest(entities=entities)
            records = memory_store.upsert_entities([e.model_dump() for e in req.entities])
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=True,
                result={"entities": records}
            )
        
        elif tool_name == "loreholm_write_memory":
            from app.mcp.schemas import WriteMemoryRequest
            req = WriteMemoryRequest(**params)
            record = memory_store.write_memory(req.model_dump())
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=True,
                result=record
            )
        
        elif tool_name == "loreholm_link_entities":
            from app.mcp.schemas import LinkEntitiesRequest
            req = LinkEntitiesRequest(**params)
            edge = memory_store.link_entities(req.model_dump())
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=True,
                result=edge
            )

        elif tool_name == "loreholm_delete_entities":
            from app.mcp.schemas import DeleteEntitiesRequest
            req = DeleteEntitiesRequest(**params)
            result = memory_store.delete_entities(req.entity_ids)
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=True,
                result=result
            )

        elif tool_name == "loreholm_delete_memories":
            from app.mcp.schemas import DeleteMemoriesRequest
            req = DeleteMemoriesRequest(**params)
            result = memory_store.delete_memories(req.memory_ids)
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=True,
                result=result
            )
        
        elif tool_name == "loreholm_search":
            from app.mcp.schemas import SearchRequest, SearchFilters
            filters = params.get("filters")
            if filters:
                filters = SearchFilters(**filters)
            req = SearchRequest(
                query=params["query"],
                top_k=params.get("top_k", 10),
                filters=filters
            )
            search_result = memory_store.search(
                query=req.query,
                top_k=req.top_k,
                entity_types=filters.entity_types if filters else None,
                tags=filters.tags if filters else None,
                since=filters.since if filters else None,
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
            context_lines = [
                f"{item['text']} (conf {item['confidence']:.2f})"
                for item in items
            ]
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=True,
                result={
                    "items": items,
                    "suggested_context_block": _context_block(context_lines),
                    "retrieval_mode": retrieval_mode,
                    "diagnostics": diagnostics,
                }
            )
        
        elif tool_name == "loreholm_search_similar_entities":
            from app.mcp.schemas import SearchSimilarEntitiesRequest
            req = SearchSimilarEntitiesRequest(**params)
            result = memory_store.search_similar_entities(
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
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=True,
                result={
                    "matches": items,
                    "message": message,
                    "diagnostics": diagnostics,
                }
            )

        elif tool_name == "loreholm_context":
            from app.mcp.schemas import ContextRequest
            req = ContextRequest(**params)
            result = memory_store.context(
                entity_ids=req.entity_ids,
                depth=req.depth,
                limit=req.limit
            )
            memory_items = result["memories"]
            context_lines = [
                f"{memory['text']} (conf {memory['confidence']:.2f})"
                for memory in memory_items
            ]
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=True,
                result={
                    "memories": memory_items,
                    "entities": result["entities"],
                    "suggested_context_block": _context_block(context_lines)
                }
            )
        
        elif tool_name == "loreholm_recent":
            from app.mcp.schemas import RecentRequest
            req = RecentRequest(**params) if params else RecentRequest()
            items = memory_store.recent(limit=req.limit, since=req.since)
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=True,
                result={"items": items}
            )
        
        elif tool_name == "loreholm_stats":
            stats = memory_store.stats()
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=True,
                result=stats
            )
        
        else:
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=False,
                result={},
                error=f"Unknown tool: {tool_name}"
            )
    
    except ValueError as e:
        return ExecuteToolResponse(
            tool_name=tool_name,
            success=False,
            result={},
            error=str(e)
        )
    except Exception as e:
        return ExecuteToolResponse(
            tool_name=tool_name,
            success=False,
            result={},
            error=f"Execution error: {str(e)}"
        )
