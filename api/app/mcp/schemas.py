from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class EntityInput(BaseModel):
    """User-supplied entity payload.

    The `type` field is an un-canonicalized string: the write path resolves
    it against the per-database authored schema (see
    `schema_resolver.build_entity_type_resolver`) at upsert time. Validation
    cannot happen here because pydantic field validators don't see the
    per-request database target.

    `merge_into` is an optional dedup hint: an existing `Entity.id` that the
    proposal refers to (typically obtained from
    `loreholm_search_similar_entities`). The reconciler honors the hint when
    the proposal embedding is within the per-database review threshold of
    that target; otherwise it parks the row as `needs_review`.
    """

    name: str
    type: str
    aliases: List[str] = Field(default_factory=list)
    merge_into: Optional[str] = None


class UpsertEntitiesRequest(BaseModel):
    entities: List[EntityInput]


class EntityOutput(BaseModel):
    entity_id: str
    name: str
    type: str
    aliases: List[str]
    created: bool


class UpsertEntitiesResponse(BaseModel):
    entities: List[EntityOutput] = Field(default_factory=list)
    # ArcadeDB backend: entities land in a staging queue first, so the
    # response surfaces the staged proposals + a human-readable message
    # telling the LLM the proposals aren't queryable yet.
    staged: Optional[List["StagedEntityProposal"]] = None
    message: Optional[str] = None
    # Phase 7.3 reconciler observability. `reconciler_lag_seconds` is the
    # age of the oldest still-pending Staging row (None when the queue is
    # empty). `needs_review_count` is the count of rows the reconciler has
    # parked for human review. Both give the LLM a fast signal that its
    # writes will take longer than usual to become queryable.
    reconciler_lag_seconds: Optional[float] = None
    needs_review_count: Optional[int] = None


class StagedEntityProposal(BaseModel):
    staging_id: str
    proposed_name: str
    proposed_type: str
    aliases: List[str] = Field(default_factory=list)
    status: str = "pending"
    # Echoed back when the caller passed `merge_into` on the corresponding
    # `EntityInput`. None when no hint was supplied.
    requested_merge_target_id: Optional[str] = None


class StagedMemoryResponse(BaseModel):
    staging_id: str
    status: str = "pending"
    linked_entity_proposals: List[str] = Field(default_factory=list)
    message: str


class StagedLinkResponse(BaseModel):
    staging_id: str
    status: str = "pending"
    from_entity_id: str
    to_entity_id: str
    message: str


class SourceMessage(BaseModel):
    id: str
    role: Optional[str] = None
    text: Optional[str] = None
    timestamp: Optional[str] = None


class SourceRef(BaseModel):
    conversation_id: str
    message_ids: List[str]
    platform: Optional[str] = None
    started_at: Optional[str] = None
    messages: List[SourceMessage] = Field(default_factory=list)


class WriteMemoryRequest(BaseModel):
    text: str
    about_entity_ids: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    tags: List[str] = Field(default_factory=list)
    source_ref: SourceRef


class WriteMemoryResponse(BaseModel):
    memory_id: str
    timestamp: str
    linked_entities: List[str]


class LinkEntitiesRequest(BaseModel):
    from_entity_id: str
    to_entity_id: str
    relationship: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class LinkEntitiesResponse(BaseModel):
    edge_id: str
    from_entity_id: str
    to_entity_id: str


class DeleteEntitiesRequest(BaseModel):
    entity_ids: List[str] = Field(min_length=1)


class DeleteEntitiesResponse(BaseModel):
    deleted_entity_ids: List[str]
    not_found_entity_ids: List[str]
    deleted_count: int


class DeleteMemoriesRequest(BaseModel):
    memory_ids: List[str] = Field(min_length=1)


class DeleteMemoriesResponse(BaseModel):
    deleted_memory_ids: List[str]
    not_found_memory_ids: List[str]
    deleted_count: int


class SearchFilters(BaseModel):
    entity_types: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    since: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1)
    filters: Optional[SearchFilters] = None


class EntityRef(BaseModel):
    entity_id: str
    name: str
    type: str


class SourceRefSummary(BaseModel):
    conversation_id: str


class SearchItem(BaseModel):
    memory_id: str
    text: str
    confidence: float
    timestamp: str
    entity_refs: List[EntityRef]
    source_ref: SourceRefSummary


class SearchDiagnostics(BaseModel):
    vector_ok: bool
    fallback_reason: Optional[str] = None
    vector_candidates: int
    lexical_candidates: int
    final_count: int
    query_terms: List[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    items: List[SearchItem]
    suggested_context_block: str
    retrieval_mode: Optional[str] = None
    diagnostics: Optional[SearchDiagnostics] = None


class SearchSimilarEntitiesRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1)
    # Optional. Reconciler-side dedup scopes by type, but this read-side
    # tool does not, because the LLM may not know the type yet.
    type: Optional[str] = None


class SimilarEntityMatch(BaseModel):
    entity_id: str
    name: str
    type: str
    aliases: List[str] = Field(default_factory=list)
    similarity: Optional[float] = None
    lexical_score: Optional[float] = None
    sources: List[str] = Field(default_factory=list)


class SearchSimilarEntitiesDiagnostics(BaseModel):
    vector_ok: bool
    vector_candidates: int
    lexical_candidates: int
    final_count: int
    query_terms: List[str] = Field(default_factory=list)


class SearchSimilarEntitiesResponse(BaseModel):
    matches: List[SimilarEntityMatch]
    message: str
    reconciler_lag_seconds: Optional[float] = None
    diagnostics: Optional[SearchSimilarEntitiesDiagnostics] = None


class ContextRequest(BaseModel):
    entity_ids: List[str]
    depth: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1)


class ContextMemory(BaseModel):
    memory_id: str
    text: str
    confidence: float
    entity_refs: List[str]


class ContextEntity(BaseModel):
    entity_id: str
    name: str
    type: str
    from_entity_id: Optional[str] = None
    relationship: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class ContextResponse(BaseModel):
    memories: List[ContextMemory]
    entities: List[ContextEntity]
    suggested_context_block: str


class RecentRequest(BaseModel):
    limit: int = Field(default=50, ge=1)
    since: Optional[str] = None


class RecentItem(BaseModel):
    memory_id: str
    text: str
    confidence: float
    tags: List[str]
    timestamp: str
    entity_refs: List[EntityRef]
    source_ref: SourceRefSummary


class RecentResponse(BaseModel):
    items: List[RecentItem]


class TopEntity(BaseModel):
    entity_id: str
    name: str
    type: str
    mem_count: int


class StatsResponse(BaseModel):
    entity_count: int
    memory_count: int
    top_entities: List[TopEntity]


# Tool Discovery and Execution Schemas
class ToolParameter(BaseModel):
    name: str
    type: str
    description: str
    required: bool = True
    enum: Optional[List[str]] = None
    default: Optional[str] = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: List[ToolParameter]


class ListToolsResponse(BaseModel):
    tools: List[ToolDefinition]


class ExecuteToolRequest(BaseModel):
    tool_name: str
    parameters: dict


class ExecuteToolResponse(BaseModel):
    tool_name: str
    success: bool
    result: dict
    error: Optional[str] = None
