# ArcadeDB Setup (3 min read)

loreholm uses **ArcadeDB** (Apache 2.0) for graph storage and vector search. A single shared ArcadeDB server (the `loreholm-arcadedb` container) holds all of your databases; each one you create is a database inside that server, managed by the local dashboard.

## BYODB (Bring Your Own Database)

Users run their own ArcadeDB server locally. Databases are created and managed through the **local dashboard** (port 4466) or the AI-powered **setup wizard**.

### User Installation

```bash
# Get your install command from example.com/dashboard.html
curl -fsSL example.com/install.sh | bash -s -- --key preauthkey-YOUR-KEY
```

This deploys five host-side containers: Tailscale (mesh networking), the shared ArcadeDB server, Bifrost (LLM gateway), the local dashboard (web UI + API + query proxy + embedding service + reconciler), and the `:8081` endpoint shim (the only Tailnet-facing container). ArcadeDB, Bifrost, and the dashboard all live on the default Docker bridge — not in the Tailscale netns. Databases are created on demand inside the shared server through the dashboard.

### Creating Databases

Databases are created from the local dashboard UI or via the wizard agent:

1. Open the local dashboard at `http://<local-ip>:4466`
2. Navigate to the **Databases** tab
3. Create a new database (assigns a `database_id` and authored schema)

Every database lives inside the one `loreholm-arcadedb` server, which persists all of them in the shared `loreholm-arcadedb-data` / `loreholm-arcadedb-log` volumes. There are no per-database containers and no per-database ports. The dashboard persists the registry in `databases.json` — one record per `database_id` with its `profile_id`, `profile_hash`, authored `schema`, and (when set) `backend`, `embedding_model`, and `embedding_dimension`.

On create, the dashboard (`api/app/local_dashboard/routes/databases.py`):
1. Calls `arcadedb_server.wait_for_server_ready(timeout_s=30)` against the shared host+port (`LOCAL_DASHBOARD_ARCADEDB_HOST` / `LOCAL_DASHBOARD_ARCADEDB_PORT`).
2. Calls `arcadedb_server.create_database(database_id)` (`POST /api/v1/server` → `CREATE DATABASE`); on any failure it raises `DATABASE_CREATE_FAILED` (502) and rolls back via `drop_database`.
3. Runs the idempotent bootstrap DDL (`api/app/local_dashboard/db/arcadedb_bootstrap.py`): declares `Entity` / `Memory` / `Staging` / `Conversation` / `Message` vertex types and `MENTIONS` / `RELATED_TO` / `ABOUT` / `HAS_MESSAGE` / `DERIVED_FROM` edge types, and builds `LSM_VECTOR` HNSW indexes on `Entity.embedding`, `Memory.embedding`, `Staging.embedding` at the embedding model's dimension (640 for Harrier-270M, 384 for MiniLM-L6-v2).

### Access Locally

- **Local Dashboard**: `http://localhost:4466`
- **ArcadeDB HTTP API**: `http://localhost:2480` (the shared server; from inside the Compose network it is `http://loreholm-arcadedb:2480`)
- **ArcadeDB Studio UI**: `http://localhost:2480/` (login with `root` + the password in `arcadedb-root.password`)

## Embeddings

Embeddings are generated on the dashboard side, not inside ArcadeDB, so the graph engine is decoupled from the model. See `api/app/local_dashboard/ai/embeddings.py` — a process-wide `EmbeddingService` lazy-loads one CPU model based on `DASHBOARD_EMBEDDING_MODEL`:

- `harrier-270m` (primary, 640-dim, `microsoft/harrier-oss-v1-270m`)
- `minilm` (fallback, 384-dim, `sentence-transformers/all-MiniLM-L6-v2`)

Queries that need an embedding carry `{{embed:<param_name>}}` placeholders in their Cypher. The dashboard's `POST /api/sync/query` proxy hook (`api/app/local_dashboard/db/embedding_hook.py`) rewrites those to `<param_name>__embedding` vector parameters on the way through. No model runs inside the ArcadeDB container.

## Configure API Connection

Relevant env vars (resolved once at process startup, in `api/app/local_dashboard/core/config.py`):

```bash
# Shared ArcadeDB server the dashboard talks to over the Docker bridge
LOCAL_DASHBOARD_ARCADEDB_HOST=loreholm-arcadedb
LOCAL_DASHBOARD_ARCADEDB_PORT=2480

# Root password file (read on demand); the compose mounts it read-only.
# The ArcadeDB container itself reads it via JAVA_OPTS=-Darcadedb.server.rootPasswordPath=…
LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE=/opt/loreholm/arcadedb-root.password

# Embedding model
DASHBOARD_EMBEDDING_MODEL=harrier-270m
```

## Verify a Database

After creating a database, inspect the shared server's HTTP API directly:

```bash
curl -u root:<password> http://localhost:2480/api/v1/ready
# {"status": "ok"}

curl -u root:<password> -H "Content-Type: application/json" \
  -d '{"language":"cypher","command":"MATCH (n) RETURN count(n) AS total"}' \
  http://localhost:2480/api/v1/command/<database_id>
```

You can also use **ArcadeDB Studio** at `http://localhost:2480/`.

## Vector Search

ArcadeDB's `LSM_VECTOR` HNSW indexes back vector search. The store layer (`api/app/services/arcadedb_store.py`) runs:

```cypher
CALL {
  WITH $embedding AS embedding
  WITH embedding
  CALL vectorNeighbors('Memory[embedding]', embedding, $limit) YIELD vertex, distance
  RETURN vertex AS node, (1.0 - distance) AS similarity, distance
}
```

Dimensions are pinned at bootstrap against `DASHBOARD_EMBEDDING_MODEL` so a mismatch fails loud rather than returning silently-wrong neighbors. See [04_VectorSearch.md](04_VectorSearch.md).

## Troubleshooting

**Database create fails:**
- The dashboard raises `DATABASE_CREATE_FAILED` (502) and rolls back via `DROP DATABASE` when `wait_for_server_ready` / `CREATE DATABASE` / bootstrap fails. If the shared server's root-password file is missing, calls fail with `ARCADEDB_NOT_CONFIGURED` (503). Check the server logs: `docker logs loreholm-arcadedb`.

**Vector queries return an empty result:**
- Verify the HNSW index exists. Open Studio → the database → **Schema**; `Entity.embedding`, `Memory.embedding`, `Staging.embedding` should all have an `LSM_VECTOR` index at the expected dimension.
- Check memories have embeddings: `MATCH (m:Memory) WHERE m.embedding IS NOT NULL RETURN count(m)`

**Wrong embedding dimension after changing model:**
- Dimensions are pinned at bootstrap. Changing `DASHBOARD_EMBEDDING_MODEL` on an existing database requires a re-embed migration so every `embedding` field is regenerated at the new dimension.

## Security Recommendations

1. **Network isolation**: the ArcadeDB server lives on the default Docker bridge and is never published on the Tailnet — only the `:8081` endpoint shim is Tailnet-facing. Don't expose `:2480` publicly.
2. **Root password**: the install script generates `arcadedb-root.password`; keep it readable only by the install owner.
3. **Firewall hook**: Every cloud-originated query passes through the local dashboard's `POST /api/sync/query` policy hook before reaching ArcadeDB (read-only enforcement, per-key rate limits, language guard, user policy rules).

## Next Steps

Once ArcadeDB is running:
1. See [03_McpTools.md](03_McpTools.md) for the MCP operations the cloud exposes.
2. See [04_VectorSearch.md](04_VectorSearch.md) for vector search behavior and the HNSW index.
3. See [05_CypherQueries.md](05_CypherQueries.md) for inspection queries.
