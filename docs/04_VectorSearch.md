# Vector Search (3 min read)

loreholm uses **vector-first semantic search** backed by ArcadeDB's `LSM_VECTOR` HNSW index. Embeddings are generated on the local dashboard (not inside the graph engine) so the store stays decoupled from the model. A keyword fallback path exists only when vector retrieval is unavailable.

## How It Works

### 1. **Writing Memories**

When you call `loreholm_write_memory`, the write path lands a `Staging` vertex (not a committed `Memory`) and the reconciler materializes it in a later pass:

```cypher
-- Inside ArcadeDBStore.write_memory (simplified)
CREATE (s:Staging {
  staging_id: $staging_id,
  status: 'pending',
  source: 'write_memory',
  proposed_text: $text,
  embedding: {{embed:text}},  -- placeholder rewritten to a real vector
  proposed_confidence: $confidence,
  proposed_tags: $tags,
  ...
})
```

- The `{{embed:text}}` placeholder is rewritten to a concrete embedding parameter by the dashboard's `POST /api/sync/query` hook (`api/app/local_dashboard/db/embedding_hook.py`) before the query hits ArcadeDB.
- Embeddings come from `EmbeddingService` (`api/app/local_dashboard/ai/embeddings.py`), which loads one of two CPU models:
  - `harrier-270m` — `microsoft/harrier-oss-v1-270m`, 640-dim, primary
  - `minilm` — `sentence-transformers/all-MiniLM-L6-v2`, 384-dim, fallback
- The `Staging.embedding` HNSW index makes near-duplicate detection fast for the reconciler.
- The reconciler (`api/app/local_dashboard/reconciler.py`) promotes, merges, or flags `Staging` vertices based on cosine-distance thresholds and then materializes `Memory` / `Entity` / `Conversation` / `Message` vertices with their own indexed embeddings.

### 2. **Searching Memories**

When you call `loreholm_search`, ArcadeDB runs the HNSW lookup directly:

```cypher
-- Inside ArcadeDBStore.search (simplified)
CALL {
  WITH $query__embedding AS embedding
  CALL vectorNeighbors('Memory[embedding]', embedding, $limit) YIELD vertex, distance
  RETURN vertex AS node, (1.0 - distance) AS similarity, distance
}
```

**Process:**
1. Dashboard hook embeds the query text (same model as writes) and injects `$query__embedding`.
2. ArcadeDB's HNSW index returns the `$limit` nearest neighbors by cosine distance.
3. Additional `WHERE` clauses apply time / tag / entity-type filters on the candidates.
4. If vector retrieval is unavailable, loreholm falls back to lexical matching on `text` / `name`.

### 3. **Why Vector-First?**

**Traditional text search** requires exact keyword matches. The query "database speed" wouldn't find "PostgreSQL performance".

**Vector search** compares meaning: "database speed" matches "PostgreSQL performance" because their embeddings are close in cosine space.

## Example Search Flow

**Query:** "How do I optimize database queries?"

**Step 1 — Embedding generated** (dashboard-side, one forward pass):
```
[0.042, -0.013, 0.089, ..., 0.051]  // 640 dimensions (Harrier) or 384 (MiniLM)
```

**Step 2 — HNSW lookup** against `Memory[embedding]` in ArcadeDB.

**Step 3 — Ranked results:**
```json
[
  { "similarity": 0.89, "text": "PostgreSQL supports JSONB for flexible schema storage" },
  { "similarity": 0.84, "text": "Create indexes on frequently queried columns" },
  { "similarity": 0.79, "text": "Use EXPLAIN ANALYZE to identify slow queries" }
]
```

## Vector Index Configuration

Every ArcadeDB database created by the dashboard gets three `LSM_VECTOR` HNSW indexes at bootstrap (`api/app/local_dashboard/db/arcadedb_bootstrap.py`):

- `Entity.embedding`
- `Memory.embedding`
- `Staging.embedding`

Dimensions are pinned against `DASHBOARD_EMBEDDING_MODEL` at bootstrap. A dimensional mismatch between the configured model and the index fails loud rather than silently returning wrong neighbors.

**Key Parameters:**

- **dimension**: 640 for `harrier-270m`, 384 for `minilm`.
- **metric**: cosine distance (HNSW default).
- **scalar kind**: 32-bit float.

## Filtering After Vector Search

HNSW returns semantically similar candidates. `WHERE` clauses filter them before the rows leave ArcadeDB.

**Entity types:**
```json
{ "query": "database performance", "entity_types": ["tool", "concept"] }
```

**Tags:**
```json
{ "query": "recent discussions", "tags": ["technical", "meeting-notes"] }
```

**Time range:**
```json
{ "query": "project updates", "since": "2026-01-01T00:00:00Z" }
```

## Embedding Model Details

**Primary:** `microsoft/harrier-oss-v1-270m` (decoder-only, last-token pooling + L2 norm, 640-dim, ~MTEB-v2 66.5)

**Fallback:** `sentence-transformers/all-MiniLM-L6-v2` (384-dim, ~80 MB, fast)

Both models are baked into the dashboard image so first boot has no network dependency. Selection is via `DASHBOARD_EMBEDDING_MODEL` (`harrier-270m` or `minilm`).

## Performance Considerations

- **HNSW search** is O(log n) with bounded recall; typical lookup < 10 ms up to ~100 k memories.
- **Dashboard-side embedding** adds ~5–15 ms per query for MiniLM, ~40–80 ms for Harrier-270M (CPU).
- **Indexes update automatically** on insert; no manual reindex required.
- **Scaling to 100 k+ memories** is fine with HNSW; consider GPU acceleration only for batch re-embeds during migration.

## Troubleshooting

**Search returns no results:**
- Verify the HNSW index exists in ArcadeDB Studio (Schema → `Memory.embedding` → `LSM_VECTOR`).
- Check memories have embeddings: `MATCH (m:Memory) WHERE m.embedding IS NOT NULL RETURN count(m)`.
- Ensure `DASHBOARD_EMBEDDING_MODEL` matches the model the data was indexed with.

**Poor search quality:**
- Too few results? Increase `top_k`.
- Results not relevant? Check confidence scores and tag filters.
- Consider switching models — MiniLM is faster but less accurate than Harrier-270M on semantic tasks.

**Slow searches:**
- Dashboard-side embedding is the dominant cost on short queries. Switch to `minilm` if latency matters more than recall.
- HNSW recall trades off with speed — retune at scale if needed.

## Fallback Behavior

**Default path:** HNSW vector search against `Memory.embedding`.

**Fallback path:** if the embedding hook or vector lookup fails, loreholm falls back to token/phrase lexical retrieval on `Memory.text`.

### Fallback/Hybrid Flags (optional)

Non-secret runtime flags with safe defaults:

- `SEARCH_ENABLE_LEXICAL_FALLBACK=true`
- `SEARCH_ENABLE_HYBRID=false`
- `SEARCH_LEXICAL_MAX_TERMS=12`
- `SEARCH_LEXICAL_MIN_TERM_LEN=3`

The system still does **not** use regex matching or string-edit-distance search.

## Advanced: Direct Vector Queries

You can bypass the embedding hook and supply a vector directly:

```python
my_vector = [0.042, -0.013, ...]  # 640-dim Harrier or 384-dim MiniLM
results = store.vector_search(index="Memory[embedding]", query_vector=my_vector, limit=10)
```

Useful for custom embedding models, pre-computed query vectors, or hybrid retrieval experiments.

## Next Steps

- For API usage, see [03_McpTools.md](03_McpTools.md).
- For implementation details, see `api/app/services/arcadedb_store.py` and `api/app/local_dashboard/reconciler.py`.
- For Cypher queries, see [05_CypherQueries.md](05_CypherQueries.md).
