# BYODB Architecture (3 min read)

BYODB (Bring Your Own Database) allows users to run their own ArcadeDB database locally while connecting to the loreholm cloud API.

## Overview

Instead of a shared cloud database, each user runs their own ArcadeDB server (a single shared container holding all of their databases) on their machine. This provides:

- **Data ownership**: Your memories stay on your machine
- **Privacy**: No data leaves your network except through explicit API calls
- **Control**: Stop the container anytime to disconnect

## How It Works

### 1. User Onboarding

1. User signs up at `loreholm.com` via the configured OIDC provider
2. Backend generates a **Headscale Pre-Auth Key** (one-time use)
3. User receives an install command (curl-to-bash)

### 2. Local Installation

The install script (`install.sh`) deploys five Docker containers. Only the
endpoint shim shares the Tailscale netns; everything else lives on the
default Compose bridge:

```yaml
services:
  loreholm-tailscale:          # Encrypted mesh networking (only Tailnet node identity)
    image: tailscale/tailscale:latest

  loreholm-arcadedb:           # Single shared ArcadeDB server — holds ALL databases
    image: arcadedata/arcadedb:26.3.1   # bridge only; :2480 not on Tailnet

  loreholm-bifrost-proxy:      # LLM provider gateway
    image: maximhq/bifrost:latest       # bridge only; :8080 not on Tailnet

  loreholm-local-dashboard:    # Web UI + API + query proxy + embedding + reconciler
    image: ghcr.io/loreholm/mcp-local-dashboard:latest
    ports: ["<lan-bind-host>:4466:4466"]  # LAN, not Tailnet

  loreholm-local-dashboard-endpoint:  # Tailnet ingress: reverse-proxies /api/sync/* + /api/chat/*
    image: python:3.12-alpine
    network_mode: service:tailscale       # the ONLY Tailnet-facing container, on :8081
```

The install script also creates these files in `~/.loreholm/`:

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Container orchestration |
| `local-dashboard.token` | Bootstrap token for first-time dashboard access |
| `local-sync.token` | Cloud-to-local sync bearer token |
| `local-api.token` | Agent API key for external integrations |
| `databases.json` | Database registry (initially empty) |
| `chat-bifrost-config.json` | LLM provider configuration (`{"providers": {}}`) |
| `dashboard-api-keys.json` | Dashboard API key store |
| `dashboard-credentials.json` | User account credentials (populated on setup) |
| `dashboard-preferences.json` | Dashboard/wizard UI preferences (e.g. favorite model) |
| `chat.db` | SQLite store for chat + wizard conversation history and usage |
| `arcadedb-root.password` | Root password for the shared ArcadeDB server (mounted read-only) |
| `local-dashboard/` | Endpoint-shim assets (`endpoint_server.py`, `local-dashboard.json`) |
| `manage-keys.sh` | CLI utility for API key management |

### 3. Dashboard Authentication

After installation, the local dashboard is accessible at `http://<local-ip>:4466`.

**First-time setup:**
1. Enter the **bootstrap token** shown in the install output (`POST /api/auth/handshake`)
2. Create a **username and password** (`POST /api/auth/setup`) — password is hashed with PBKDF2-SHA256 (260k iterations)
3. After account creation, log in with username/password (`POST /api/auth/login`)

The bootstrap token remains usable from CLI tools via the `X-Local-Token` header but no longer works for browser login after account setup.

### 4. AI Provider Configuration

The local dashboard uses **Bifrost** as an LLM provider gateway. Configure one or more providers from the dashboard's AI Models tab:

| Provider | Auth | Discovery |
|----------|------|-----------|
| OpenAI | API key | `api.openai.com/v1/models` |
| Anthropic | API key | Hardcoded model list |
| Google (Gemini) | API key | `generativelanguage.googleapis.com` |
| Groq | API key | `api.groq.com/openai/v1/models` |
| Ollama (Local) | Base URL only | Local `/v1/models` endpoint |

Provider configuration is stored in `chat-bifrost-config.json` and mounted into the Bifrost container.

### 5. Secure Connection

- **Tailscale Sidecar** connects to Headscale control plane
- **Pre-auth key** authenticates the node
- **ACLs** ensure only the cloud API can reach the machine's `:8081` endpoint shim
- **Endpoint shim** (`:8081`) is the only Tailnet-facing container; it reverse-proxies `/api/sync/*` and `/api/chat/*` to the dashboard over the bridge
- **ArcadeDB** (`:2480`) and **Bifrost** (`:8080`) live on the default Docker bridge and are never published on the Tailnet; the dashboard (`:4466`) is published to the LAN bind host only

### 6. API Access

When user makes MCP calls:

1. API validates JWT token (OIDC)
2. If request uses an API key with `db_ref`, API resolves the database target route
3. Looks up user's Tailscale IP via Headscale API when no host override is configured
4. Sends the query over the encrypted mesh to the machine's `:8081` endpoint shim, which proxies it to the local dashboard's `POST /api/sync/query`
5. The dashboard executes against the target database on the shared ArcadeDB server and returns the rows plus `profile_hash`

For multi-database users, several API keys can reference the same named database target.

### 7. Cloud ↔ Local Synchronization

The cloud API caches per-database metadata (graph schema, tool manifest,
routing info) in the Postgres `database_targets` row so that MCP requests
can run the canonicalization path without a round-trip to the local
dashboard for every cached field. That cache has to stay in sync with
what the local dashboard actually holds.

**Pull-only architecture.** The local dashboard never initiates outbound
HTTP to the cloud. All synchronization is cloud-pull over Tailscale, using
the shared bearer token in `local-sync.token`. This preserves the trust
boundary — a compromised local node cannot reach into the cloud API, and
end-user firewall policies that block outbound from BYODB nodes are
supported by default. See `docs/01_Architecture.md` for the overall
"pull-only local dashboard" principle.

**Query-proxy topology.** The cloud API does not speak HTTP to ArcadeDB
directly. Every MCP-tool-generated query is sent as a JSON payload to the
machine's `:8081` endpoint shim, which proxies it to the local dashboard's
`POST /api/sync/query` endpoint; the local dashboard is the sole client of
the shared ArcadeDB server's HTTP API (reached as `loreholm-arcadedb:2480`
over the Docker bridge), which is never published on the Tailnet, so it is
not reachable from the Tailnet at all. The proxy response envelope always
carries the current `profile_hash` alongside the query rows, which gives
the cloud a freshness signal on every request without any extra
round-trips.

This topology is load-bearing for synchronization: the hash on every
response is what lets the cloud detect schema edits lazily, without a
background poll loop.

#### Sync triggers

1. **On API key creation** (existing): the cloud calls
   `POST /api/sync/database-targets/resolve` on the local dashboard to pull
   routing and the profile object (`profile_id`, `profile_hash`,
   `schema`, `tool_manifest`), and upserts the result into the target
   row. This is the only mandatory sync and happens atomically with key
   issuance.
2. **On every query** (new): every MCP request routes through the query
   proxy, which returns `profile_hash` in the response envelope. The
   cloud compares that value to the cached `profile_hash` on the target
   row. On mismatch, the cloud pulls a fresh resolve, rewrites the
   target row, and retries the query once against the new cache.
3. **On cold start** (when `schema_json` is NULL): before the first
   query for a target can execute, the cloud performs a blocking resolve
   pull so that write-strict canonicalization has a vocabulary to check
   against.

There is no background poll loop and no Redis lease. Staleness
detection is a byproduct of serving queries; no traffic is spent on
idle targets.

#### Resolve-and-retry semantics

When the post-response hash comparison reports a mismatch, the flow is:

1. Call `POST /api/sync/database-targets/resolve` for the target.
2. Write the returned `schema_json`, `tool_manifest_json`, routing
   fields, and `profile_hash` into the `database_targets` row.
3. Re-execute the original query through the proxy using the refreshed
   cache. If the retry envelope's hash still differs (a third edit
   landed during the resolve), return the retry response anyway and
   log a warning — the cache is stale by one edit, which self-heals
   on the next request.

A single retry is enough for the common case (one schema edit during a
query). Unbounded retries would give a hot-editing user the ability to
livelock the request path.

#### Profile hash semantics

`profile_hash` is a **content-derived hash covering every
observable-state field of the database's registry entry**. The local
dashboard recomputes it on any registry write: schema edits, connection
info edits, credential rotations, tool manifest changes. Runtime-only
fields (`last_seen_at`, container `status`) are intentionally excluded
from the hash so heartbeats do not trigger sync.

Because the hash is derived from serialized content, it cannot drift
from the underlying data — there is no "forgot to bump" failure mode
that a manually maintained version counter would have. `profile_hash`
is the canonical staleness signal for the entire target row, and the
sync protocol uses a single equality check against it.

Earlier drafts of this design carried both a `profile_version INTEGER`
counter (for all state changes) and a narrower `schema_hash` (as a
defensive check against "forgot to bump the counter" bugs). A single
profile-scoped content hash replaces both: one signal, one source of
truth, no manual maintenance, no bug class to defend against.

#### Tool change propagation to MCP clients

The cloud's MCP server does **not** emit `notifications/tools/list_changed`
today; `mcp_server.py` advertises `{"listChanged": false}` and has no
persistent session registry. Tool changes (new entity types, renames) take
effect on the MCP client's next `tools/list` call — typically at the start
of a new session. Users who edit their schema mid-conversation should start
a new chat to pick up changes. Live in-session propagation is tracked as a
separate follow-up (requires implementing an SSE stream, a session
registry, and cross-instance pub/sub) and is intentionally out of scope for
the initial multi-schema rollout.

#### Failure modes

- **Local dashboard unreachable during a query**: the proxy call fails
  and the MCP request returns a structured upstream error. There is no
  cached direct-connect path to fall back to — the query proxy *is* the path.
  This is the same availability envelope as "dashboard down → no MCP"
  that already exists for any user-configured database.
- **Local dashboard unreachable during a resolve-retry**: the retry
  pull fails, the MCP request returns the resolve error. The cached
  target row is untouched; the next request retries the whole flow.
- **Local dashboard unreachable on cold start** (NULL `schema_json`):
  the blocking pull fails, the MCP request returns 502/503 (same as
  today's key-creation failure path via `LocalSyncError`). There is
  no cached row to fall back to.
- **Local dashboard returns a different `database_id`**: treated as a 404
  (database removed); future requests surface
  `LOCAL_SYNC_DATABASE_NOT_FOUND` until the dashboard is reconciled.
- **Firewall denial from the local dashboard**: the proxy response is
  a `POLICY_DENIED` envelope with `rule` + `reason`. The cloud maps
  this to a structured MCP tool error so the LLM can relay it to the
  user.

#### BYODB discovery and authored-schema endpoints

The sync endpoint above (`/api/sync/database-targets/resolve`) is
keyed by an already-known `database_id` and is the routing/credentials
hot path. Two additional
sync-authed endpoints on the local dashboard let the cloud API **discover**
what databases exist on a user's device and fetch the authored graph
schema without having to touch live ArcadeDB state:

1. **`GET /api/sync/databases`** — advertises every registered database on
   the device, online or offline. Returns a list of summaries sourced from
   the local `databases.json` registry:

   ```json
   {
     "databases": [
       {
         "database_id": "work-db",
         "name": "Work",
         "profile_id": "memory-default",
         "profile_hash": "sha256:…",
         "status": "online",
         "last_seen_at": "2026-04-11T12:00:00Z",
         "recovered_at": null,
         "recovery_status": null
       }
     ],
     "count": 1
   }
   ```

   No connection info (`host`, `port`, credentials) appears here — every
   database resolves to the one shared ArcadeDB server, so there is no
   per-database port to advertise.

   This is the *discovery* call. The cloud uses it when presenting the
   user with the list of databases available to bind an API key to (e.g.,
   the dashboard's "Database Target" picker in the API key create flow).
   The response is cached cloud-side in Redis under
   `sync:discovery:{user_id}` with a short TTL (default `30` seconds via
   `SYNC_DISCOVERY_CACHE_TTL_SECONDS`) to absorb UI bursts; the cached
   payload is invalidated when any target for that user is written via
   the resolve path.

2. **`GET /api/sync/databases/{database_id}/schema`** — returns the
   **authored** graph schema for one database, sourced from the
   per-database record in `databases.json` (not from live ArcadeDB
   introspection). This is the schema the MCP server composes tool enums
   and descriptions from:

   ```json
   {
     "database_id": "work-db",
     "profile_hash": "sha256:…",
     "schema": {
       "entity_types": [
         {"name": "Person",  "description": "A human individual"},
         {"name": "Project", "description": "A unit of work with an owner and a deadline"}
       ],
       "relationship_types": [
         {"name": "ATTENDED", "description": "Participation in a meeting"}
       ],
       "entity_type_aliases": {"Human": "Person"},
       "relationship_type_aliases": {"PARTICIPATED_IN": "ATTENDED"}
     }
   }
   ```

   The authored schema (entity/relationship types and alias maps) is
   nested under a single `schema` object, normalized by
   `_normalize_schema_block`.

   **Descriptions are first-class.** The MCP server surfaces them in tool
   parameter descriptions so the LLM has enough context to use the schema
   correctly; they are not optional metadata. The local dashboard's
   schema editor must require a description for every new entity and
   relationship type.

   **Relationship renames use the same soft-alias rules as entity type
   renames.** `relationship_type_aliases` is a flat `Old → New` map,
   cumulative (never chained: `A→B` then `B→C` must record `A→C` and
   `B→C` directly), append-only once a mapping is recorded, and resolved
   on the write path while the read path stays loose. See
   `docs/06_ToolSchemas.md` for the rationale.

Both endpoints use the same sync bearer token as the existing
`database-targets/*` routes. The discovery endpoint is intentionally
namespaced under `/api/sync/databases` (local device inventory) while
the routing/credentials hot path stays under
`/api/sync/database-targets` (cloud-side target registry concerns).

The authoritative copy of the authored schema lives on the local side
in `databases.json`. The cloud caches the last pulled schema in a new
`schema_json JSONB` column on `database_targets`, rehydrated whenever a
full resolve picks up a `profile_hash` change.

#### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SYNC_DISCOVERY_CACHE_TTL_SECONDS` | `30` | Redis TTL for `GET /api/sync/databases` responses |
| `LOCAL_SYNC_TIMEOUT_SECONDS` | `3.0` | HTTP timeout for full resolve calls (existing) |
| `LOCAL_PROXY_QUERY_TIMEOUT_SECONDS` | `10.0` | HTTP timeout for `POST /api/sync/query` proxy calls |

### 8. Multiple Databases On One Machine

A single machine can hold many isolated databases. They all live inside the
**one** `loreholm-arcadedb` server — there is no second container and no
per-database port. Create databases from the dashboard **Databases** tab or
the wizard; each one is a `CREATE DATABASE` against the shared server.

- `loreholm-tailscale` remains the only Tailnet node identity for the machine
- `loreholm-arcadedb` (`:2480`, on the bridge) hosts every database; isolation
  is per-database within the server, not per-container
- Each database is registered in `databases.json` by `database_id` with its
  own authored schema, `profile_id`, and `profile_hash`

Connection behavior from the cloud API:

- The API resolves the user's Tailnet IP (`100.x.x.x`) from Headscale
- Cloud queries go to the machine's `:8081` endpoint shim, which proxies to
  the local dashboard's `POST /api/sync/query`; the dashboard selects the
  specific database by `database_id` and runs it against the shared server
- No per-database `host`/`port` is stored or needed — routing is to the
  machine's current Tailnet IP

This gives per-database isolation without per-database containers or extra
Headscale nodes.

### 9. Database Setup Wizard

The local dashboard includes an AI-powered wizard agent for database setup and schema design. The wizard can:

- **List, inspect, and query** registered databases
- **Deploy new databases** (`CREATE DATABASE` on the shared server and bootstraps their vertex/edge types + HNSW indexes)
- **Start stopped databases** or **redeploy** the schema to fix configuration issues
- **Design schemas** by running Cypher queries (CREATE, MERGE, indexes, constraints)

The wizard uses the configured Bifrost provider for LLM inference. Destructive operations (`deploy_database`) require explicit user approval via the tool approval workflow.

## Local vs. Remote Access

| Component | Binding | You (Local) | API (Remote) |
| --- | --- | --- | --- |
| **Endpoint shim** | Tailscale netns, `:8081` | — | ✅ Tailnet ingress (sync + chat proxy) |
| **Local Dashboard** | Docker bridge, `:4466` published to LAN bind host | ✅ Web UI | ✅ Via the `:8081` shim only |
| **ArcadeDB** | Docker bridge, `:2480` | ✅ Via dashboard only | ❌ Not reachable |
| **Bifrost Proxy** | Docker bridge, `:8080` | ✅ Via dashboard | ❌ Not reachable |

ArcadeDB, Bifrost, and the dashboard all live on the default Docker bridge;
none is published on the Tailnet. The only Tailnet-facing container is the
`:8081` endpoint shim, which reverse-proxies `/api/sync/*` and `/api/chat/*`
to the dashboard. The dashboard is the sole HTTP client for the shared
ArcadeDB server's APIs (`POST /api/sync/query`). See §7 "Query-proxy
topology" for rationale.

## Quick Start

### For Users

```bash
# 1. Sign up at loreholm.com
# 2. Copy your install command from the dashboard
# 3. Run it:

curl -fsSL loreholm.com/install.sh | bash -s -- --key preauthkey-YOUR-KEY

# 4. Open the local dashboard at the URL shown in install output
# 5. Enter the bootstrap token
# 6. Create your account (username + password)
# 7. Configure an AI provider in the AI Models tab
# 8. Create your first database from the Databases tab or the wizard
```

### Verify Connection

```bash
# Check containers are running
docker ps

# Check Tailscale status
docker exec loreholm-tailscale tailscale status

# Access local dashboard
open http://localhost:4466
```

## Technology Stack

- **ArcadeDB** (Apache 2.0): Graph + HNSW vector index; one shared server container holding all databases
- **Dashboard-side `EmbeddingService`**: Harrier-270M (640-dim) primary / MiniLM-L6-v2 (384-dim) fallback
- **Bifrost**: LLM provider gateway (OpenAI, Anthropic, Google, Groq, Ollama)
- **Tailscale**: Encrypted mesh networking
- **Headscale**: Self-hosted Tailscale control plane
- **OIDC provider**: Cloud API authentication (any standard OIDC issuer)
- **Docker Compose**: Local orchestration

## Data Persistence

All database data lives in the shared ArcadeDB server's named Docker volumes — `loreholm-arcadedb-data` (databases) and `loreholm-arcadedb-log` (logs) — regardless of how many databases you create. Dropping a single database (`DROP DATABASE`) does not touch the volume's other databases.

Configuration and credentials are stored as files in `~/.loreholm/` and bind-mounted into containers:

```bash
# Back up all config
cp -r ~/.loreholm ~/.loreholm-backup

# Back up the ArcadeDB data volume (all databases on the shared server)
docker run --rm -v loreholm-arcadedb-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/arcadedb-backup.tar.gz -C /data .
```

## Updating

Run the update script to pull the latest container images while preserving all data, credentials, and configuration:

```bash
~/.loreholm/update.sh
```

The update script backs up `docker-compose.yml`, regenerates it with the latest service definitions, and restarts all containers.

## Uninstalling

```bash
# Stop and remove containers
cd ~/.loreholm
docker compose down

# Remove data volumes (optional)
docker volume ls | grep loreholm | awk '{print $2}' | xargs docker volume rm

# Remove install directory
rm -rf ~/.loreholm
```
