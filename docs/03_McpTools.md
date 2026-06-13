# MCP Tools Reference (3 min read)

All interactions with loreholm happen through explicit MCP tool calls. These are the **only supported interface**.

## Authentication

### BYODB Mode (Default)
Requires Auth0 JWT token. The API connects to your personal database via Tailscale.

```
Authorization: Bearer <auth0-jwt-token>
```

### Legacy Mode
Uses simple bearer token defined by `AUTH_TOKEN` environment variable.

```
Authorization: Bearer <auth-token>
# or
X-Auth-Token: <auth-token>
```

## Design Principles

All tools are:
- **Explicit** - No hidden behavior
- **Deterministic** - Same input → same effect
- **Inspectable** - Results can be audited
- **Composable** - Do one thing well
- **Safe to retry** - Idempotent where possible

## Tool Endpoints

All tools are available at `POST /mcp/{tool_name}`:

```
POST /mcp/loreholm_upsert_entities
POST /mcp/loreholm_write_memory
POST /mcp/loreholm_link_entities
POST /mcp/loreholm_delete_entities
POST /mcp/loreholm_search
POST /mcp/loreholm_search_similar_entities
POST /mcp/loreholm_context
POST /mcp/loreholm_recent
POST /mcp/loreholm_stats
```

## Write Tools

### loreholm_upsert_entities

Create or update canonical entities.

**Purpose:** Establish entities before linking memories.

**Input:**
```json
{
  "entities": [
    {
      "name": "PostgreSQL",
      "type": "Tool",
      "aliases": ["postgres", "pg"],
      "merge_into": "a1b2c3d4"
    }
  ]
}
```

**`merge_into` (optional):** When the LLM has already identified an existing
entity that the proposal refers to (typically by calling
`loreholm_search_similar_entities` first), pass its `entity_id` here. The
reconciler uses this as a strong hint: if the proposal's embedding is within
the review distance of the requested target it will be merged into that
entity even if some other entity is technically closer; if the distance
exceeds the review threshold the row is parked as `needs_review` so the
hint can be reviewed in the dashboard. Passing a stale or unknown id is
safe — the reconciler falls through to the normal vector-nearest decision.

**Output:**
```json
{
  "entities": [
    {
      "entity_id": "a1b2c3d4",
      "name": "PostgreSQL",
      "type": "Tool",
      "aliases": ["postgres", "pg"],
      "created": true
    }
  ]
}
```

**Notes:**
- Idempotent: Returns existing entity if name/alias matches
- Aliases normalized for matching
- Type input is case-insensitive but normalized to canonical title case

### loreholm_write_memory

Store a memory with automatic embedding generation.

**Purpose:** Record observations from conversations.

**Input:**
```json
{
  "text": "PostgreSQL supports JSONB for flexible schema storage",
  "confidence": 0.95,
  "about_entity_ids": ["a1b2c3d4"],
  "tags": ["technical", "database"],
  "source_ref": {
    "conversation_id": "conv_123",
    "message_ids": ["msg_456"],
    "platform": "chatgpt",
    "messages": [
      {
        "id": "msg_456",
        "role": "user",
        "text": "PostgreSQL JSONB capabilities?",
        "timestamp": "2026-01-30T12:33:10Z"
      }
    ]
  }
}
```

**Output:**
```json
{
  "memory_id": "mem_789",
  "timestamp": "2026-01-30T12:34:56Z",
  "linked_entities": ["a1b2c3d4"]
}
```

**Notes:**
- Embedding automatically generated via `embeddings.text()`
- Memory immediately indexed for vector search
- Confidence indicates how certain the information is
- Use stable host IDs for `conversation_id` and `message_ids` to improve provenance consistency

### loreholm_link_entities

Create relationships between entities.

**Purpose:** Capture how entities relate.

**Input:**
```json
{
  "from_entity_id": "a1b2c3d4",
  "to_entity_id": "e5f6g7h8",
  "relationship": "uses",
  "confidence": 0.9,
  "reason": "Discussed in conversation about tech stack"
}
```

**Output:**
```json
{
  "edge_id": "edge_999",
  "from_entity_id": "a1b2c3d4",
  "to_entity_id": "e5f6g7h8"
}
```

### loreholm_delete_entities

Delete entities by ID.

**Purpose:** Remove incorrect, stale, or test entities from the graph.

**Input:**
```json
{
  "entity_ids": ["a1b2c3d4", "e5f6g7h8"]
}
```

**Output:**
```json
{
  "deleted_entity_ids": ["a1b2c3d4"],
  "not_found_entity_ids": ["e5f6g7h8"],
  "deleted_count": 1
}
```

**Notes:**
- Uses `DETACH DELETE` semantics (relationships to the entity are removed)
- Safe to retry: missing IDs are returned in `not_found_entity_ids`

## Read Tools

### loreholm_search

**Vector-first semantic search** - finds memories by meaning, not keywords.

**Purpose:** Find relevant memories using semantic similarity.

**Input:**
```json
{
  "query": "database performance optimization",
  "top_k": 10,
  "entity_types": ["tool", "concept"],
  "tags": ["technical"],
  "since": "2026-01-01T00:00:00Z"
}
```

**Output:**
```json
{
  "results": [
    {
      "memory_id": "mem_789",
      "text": "PostgreSQL supports JSONB...",
      "confidence": 0.95,
      "timestamp": "2026-01-30T12:34:56Z",
      "entity_refs": [
        {"entity_id": "a1b2c3d4", "name": "PostgreSQL", "type": "tool"}
      ],
      "source_ref": {"conversation_id": "conv_123"}
    }
  ],
  "retrieval_mode": "vector",
  "diagnostics": {
    "vector_ok": true,
    "fallback_reason": null,
    "vector_candidates": 30,
    "lexical_candidates": 0,
    "final_count": 10,
    "query_terms": ["database", "performance"]
  }
}
```

**How it works:**
1. Generate embedding for query text
2. Run vector similarity search (cosine similarity)
3. Filter by entity_types/tags if specified
4. Return top-k results ranked by relevance

**Note:** No text matching - uses pure semantic similarity via embeddings.

### loreholm_search_similar_entities

Find existing entities that may already represent the thing you are about
to upsert.

**Purpose:** Let the calling LLM dedupe entities at write time using its
in-conversation context, instead of deferring every duplicate-detection
decision to the background reconciler. Call before
`loreholm_upsert_entities` whenever the proposal might already exist.

**Input:**
```json
{
  "query": "Apollo program",
  "top_k": 5,
  "type": "Project"
}
```

`type` is optional — leave it unset when you don't yet know which entity
type the proposal will land in.

**Output:**
```json
{
  "matches": [
    {
      "entity_id": "ent_42",
      "name": "Apollo Program",
      "type": "Project",
      "aliases": ["apollo"],
      "similarity": 0.91,
      "lexical_score": 1.0,
      "sources": ["vector", "lexical"]
    }
  ],
  "message": "Found 1 candidate entity. Pass entity_id as merge_into when upserting if this is the same thing.",
  "reconciler_lag_seconds": 0.4
}
```

**Notes:**
- Hybrid: vector-nearest (cosine via `vectorNeighbors('Entity[embedding]')`)
  union lexical (tokenized match against `name_norm` / `aliases_norm`).
  Lexical-only candidates carry a flat penalty so vector confidence wins
  ties.
- Read-only — does not stage anything.
- The `entity_id` field is the value to pass back as `merge_into` on
  `loreholm_upsert_entities`.

### loreholm_context

Get entity-centric context (memories + related entities).

**Purpose:** Retrieve everything related to specific entities.

**Input:**
```json
{
  "entity_ids": ["a1b2c3d4"],
  "depth": 2,
  "limit": 20
}
```

**Output:**
```json
{
  "memories": [
    {
      "memory_id": "mem_789",
      "text": "PostgreSQL supports JSONB...",
      "confidence": 0.95,
      "entity_refs": ["a1b2c3d4"]
    }
  ],
  "entities": [
    {
      "entity_id": "e5f6g7h8",
      "name": "JSON",
      "type": "concept"
    }
  ]
}
```

**Notes:**
- `depth`: How many relationship hops to traverse (1 or 2)
- Returns both memories and related entities

### loreholm_recent

Get recent memories, optionally filtered by time.

**Purpose:** See what was recently discussed.

**Input:**
```json
{
  "limit": 20,
  "since": "2026-01-30T00:00:00Z"
}
```

**Output:**
```json
{
  "memories": [
    {
      "memory_id": "mem_789",
      "text": "PostgreSQL supports JSONB...",
      "confidence": 0.95,
      "tags": ["technical", "database"],
      "timestamp": "2026-01-30T12:34:56Z",
      "entity_refs": [...]
    }
  ]
}
```

### loreholm_stats

Get database statistics.

**Purpose:** Overview of knowledge graph size.

**Input:** None

**Output:**
```json
{
  "entity_count": 42,
  "memory_count": 156,
  "top_entities": [
    {
      "entity_id": "a1b2c3d4",
      "name": "PostgreSQL",
      "type": "tool",
      "mem_count": 23
    }
  ]
}
```

## Authentication (Optional)

If `AUTH_TOKEN` environment variable is set, all MCP routes require authentication:

**Header Option 1:**
```
Authorization: Bearer your-token-here
```

**Header Option 2:**
```
X-Auth-Token: your-token-here
```

## Common Patterns

**1. Entity-first workflow:**
```
1. search_similar_entities - Check whether the entity already exists
2. upsert_entities - Create entities (pass merge_into when search found a match)
3. write_memory - Store observations about them
4. link_entities - Connect related entities
5. search - Find relevant memories later
```

**2. Conversational memory:**
```
1. write_memory - After each meaningful exchange
2. search - Before responding to retrieve context
3. recent - Review conversation history
```

**3. Knowledge exploration:**
```
1. context - Get everything about an entity
2. link_entities - Discover relationships
3. stats - See overall knowledge structure
```

## Next Steps

- See [04_VectorSearch.md](04_VectorSearch.md) for search implementation details
- See [06_ToolSchemas.md](06_ToolSchemas.md) for complete schema definitions
