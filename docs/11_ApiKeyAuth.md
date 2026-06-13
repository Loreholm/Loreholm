# API Key Authentication

API keys allow MCP clients (like Claude Desktop, Cursor, or custom integrations) to authenticate with loreholm without requiring browser-based OAuth flows.

## Overview

API keys are self-contained PASETO tokens that encode:
- User identity (linked to your Auth0 account)
- Key name (for your reference)
- Expiration date
- Unique key ID (for revocation)
- Optional database target reference (`db_ref`) for multi-database routing

**Key benefits:**
- No refresh tokens to manage
- Works in headless/CLI environments
- Revocable at any time from the dashboard
- Cryptographically signed (tamper-proof)

## Creating API Keys

1. Log in to your dashboard at `loreholm.com/dashboard`
2. Scroll to the "API Keys" section
3. Click "Create API Key"
4. Enter a name (e.g., "Claude Desktop") and expiry period
5. (Optional) Expand **Database Target** and bind this key to a specific database
6. **Copy the key immediately** - it's only shown once!

### Multi-Database Routing

To switch databases by API key, create reusable database targets and bind keys to a target ID.
When a request includes `X-API-Key`, the API routes to the target referenced by that key.

If no database target is configured on a key, loreholm falls back to the user's default BYODB node.

### Database Target Management

Database targets are managed with dashboard-authenticated endpoints:

- `POST /database-targets`
- `GET /database-targets`
- `PATCH /database-targets/{target_id}`
- `DELETE /database-targets/{target_id}`

Each target is unique per user by name and can be shared by multiple API keys.

### Per-target profile and graph schema

Every row in `database_targets` carries a **profile** object describing
the cached, cloud-side snapshot of what the local dashboard considers
authoritative for that database. The row also stores a `database_id`
used to route queries through the local dashboard's query proxy.

Note: raw database connection fields (`host`, `port`, `username`,
`password`, `sslmode`) are **not** stored on the cloud side at all.
Under the query-proxy topology, all cloud traffic for a database flows
through the local dashboard's `POST /api/sync/query` endpoint, and the
dashboard is the sole ArcadeDB HTTP client. The only things the cloud
caches per target are the logical `database_id` and the profile metadata
(`schema_json`, `tool_manifest_json`, `profile_hash`) needed to assemble
the MCP tool surface.

| Column | Type | Purpose |
|---|---|---|
| `profile_id` | `TEXT` | Profile name (default `memory-default`) |
| `profile_hash` | `TEXT` | Content hash covering **every observable-state field** of the local dashboard's registry record for this database: the authored graph schema, the tool manifest, and the connection info block. Recomputed on every registry write; the sole staleness signal used by the sync protocol. Returned on every query-proxy response envelope; the cloud compares it to the cached value and triggers a resolve-and-retry on mismatch. Runtime fields (`last_seen_at`, container `status`) are excluded from the hash so heartbeats do not trigger sync. |
| `schema_json` | `JSONB` | Cached authored graph schema pulled from the local dashboard (entity types with descriptions, relationship types with descriptions, entity + relationship alias maps). Rehydrated on every full resolve when `profile_hash` changes. |
| `tool_manifest_json` | `JSONB` | MCP client-facing capability overlay — which tools this target exposes, per-key permissions, etc. |

**Why a single `profile_hash` and not a version counter plus a schema
hash?** Earlier drafts of this design carried both a
`profile_version INTEGER` counter and a `schema_hash` narrower hash, with
the schema hash acting as a defensive check against "forgot to bump the
counter" bugs. A single content-derived hash covering the whole profile
makes that entire bug class impossible — the hash is computed from the
serialized content, so it cannot drift from the underlying data. One
staleness signal, one source of truth, no manual counter maintenance.

**Graph schema vs. tool manifest.** These two concerns are deliberately
stored separately and play different roles:

- The **graph schema** (entity types, relationship types, aliases) describes
  the *shape of the data* in ArcadeDB. It is a property of the database
  itself — every API key pointing at the same database must see the same
  entity/relationship vocabulary, because they all write into the same
  graph. Graph schema is authored per-database in the local dashboard. Its
  authoritative form lives on the local side; the cloud caches it in
  `schema_json` and uses `profile_hash` to detect when the cache is stale.
- The **tool manifest** (`tool_manifest_json`) is a per-API-key capability
  overlay describing how the MCP behaves for the client using this
  particular key — which tools are exposed, read-only vs. read-write, etc.
  Two API keys pointing at the same database can legitimately have
  different tool manifests. This column is **reserved** for that purpose;
  it must not be used as a general-purpose schema blob.

At MCP connection time the server composes the final tool surface by
combining the base MCP tool set, the per-target tool manifest, and the
per-database graph schema (which populates entity-type enums in tool
parameter descriptions).

See `docs/06_ToolSchemas.md` for the read/write validation model and the
soft-alias rename strategy that make the graph schema forgiving to edit.
See `docs/07_BYODB.md` §7 for the cloud ↔ local sync flow that keeps these
target fields fresh.

### Instance-Per-Tenant Routing (Same User Machine)

For BYODB users running multiple ArcadeDB containers on one machine,
create one database target per instance, each referencing a distinct
`database_id` as registered in the local dashboard's `databases.json`.

- `work-db` → `database_id: "work-db"`
- `personal-db` → `database_id: "personal-db"`
- `archive-db` → `database_id: "archive-db"`

Routing no longer uses host/port from the cloud side. Every query is
sent to the user's local dashboard (resolved via the user's Tailscale
IP) over HTTPS, and the dashboard maps the `database_id` to the right
ArcadeDB container on its internal Docker bridge.

Example target creation:

```http
POST /database-targets
Authorization: Bearer <auth0-jwt>
Content-Type: application/json

{
  "name": "personal-db",
  "database_id": "personal-db"
}
```

Then bind the API key to that target:

```http
POST /api-keys
Authorization: Bearer <auth0-jwt>
Content-Type: application/json

{
  "name": "Claude Personal",
  "expires_days": 365,
  "database_target_id": "dt_personal..."
}
```

## Using API Keys

Add the `X-API-Key` header to your MCP requests:

```bash
curl -X POST https://api.loreholm.com/mcp/loreholm_search \
  -H "X-API-Key: v4.local.your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"query": "project status", "top_k": 5}'
```

### MCP Client Configuration

For Claude Desktop or similar MCP clients:

```json
{
  "mcpServers": {
    "loreholm-myproject": {
      "url": "https://api.loreholm.com/mcp",
      "headers": {
        "X-API-Key": "v4.local.your-api-key-here"
      }
    }
  }
}
```

The dashboard suggests a server name of `loreholm-{database_name}` so registering keys for multiple databases produces distinct entries. Use any name you like — only the URL and `X-API-Key` header matter to the server.

## Key Limits

- Maximum **10 API keys** per user
- Keys can expire in 30 days, 90 days, 1 year, or 2 years
- Expired and revoked keys don't count toward the limit

## Revoking Keys

1. Go to your dashboard
2. Find the key in the "API Keys" section
3. Click "Revoke"
4. Confirm the action

**Note:** Revoked keys stop working immediately. Any applications using the key will receive `401 Unauthorized` errors.

## Security Considerations

- **Treat API keys like passwords** - don't commit them to git or share them
- Use environment variables or secure secret managers
- Create separate keys for different applications
- Revoke keys you no longer need
- Set appropriate expiry times based on your use case

## How It Works (Technical Details)

API keys use [PASETO v4.local](https://github.com/paseto-standard/paseto-spec) tokens:

1. **Creation**: When you create a key, the server generates a PASETO token encrypted with a server-side secret
   - Keys store a database target reference (`db_ref`) when bound to a target
2. **Validation**: On each request, the server:
   - Decrypts and verifies the token signature
   - Checks the expiration timestamp
   - Checks if the key ID is in the revocation list (Redis)
3. **Routing**:
   - If `db_ref` is present, resolve target config from Postgres (`database_targets`) and extract the `database_id`
   - If no target reference is present, fall back to the user's default BYODB `database_id`
   - The query is then sent to the user's local dashboard query proxy (resolved via Tailscale IP), which handles the actual HTTP call to ArcadeDB
4. **Revocation**: Revoked key IDs are stored in Redis with TTL matching the key's original expiry

This approach allows:
- Fast cryptographic validation with minimal Redis lookups
- Horizontal scaling (any API server can validate)
- Fast revocation (Redis lookup only for revoked keys)
- Shared database targets so many keys can point to one route definition

## Environment Variables (Server)

The API requires these environment variables for API key functionality:

```bash
# 32-byte secret key, base64 encoded
# Generate with: python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
API_KEY_SIGNING_SECRET=your-base64-encoded-32-byte-secret

# Redis connection for revocation tracking
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=your-redis-password  # Optional if no auth
REDIS_DB=0                          # Optional, defaults to 0

# Postgres connection for reusable database targets
PG_SERVER=localhost
PG_PORT=5432
PG_DB=loreholm
PG_USER=loreholm
PG_PW=your-postgres-password
```

## API Endpoints

### Create API Key

```http
POST /api-keys
Authorization: Bearer <auth0-jwt>
Content-Type: application/json

{
  "name": "Claude Desktop",
  "expires_days": 365,
  "database_target_id": "dt_abc123..."
}
```

Response:
```json
{
  "api_key": "v4.local...",
  "key_id": "ak_abc123...",
  "name": "Claude Desktop",
  "created_at": "2026-01-31T12:00:00Z",
  "expires_at": "2027-01-31T12:00:00Z",
  "database": {
    "target_id": "dt_abc123...",
    "name": "work-db",
    "database_id": "work-db"
  }
}
```

### Create Database Target

```http
POST /database-targets
Authorization: Bearer <auth0-jwt>
Content-Type: application/json

{
  "name": "work-db"
}
```

Response:
```json
{
  "target_id": "dt_abc123...",
  "name": "work-db",
  "database_id": "work-db",
  "created_at": "2026-02-14T12:00:00Z",
  "updated_at": "2026-02-14T12:00:00Z"
}
```

### List API Keys

```http
GET /api-keys
Authorization: Bearer <auth0-jwt>
```

Response:
```json
{
  "keys": [
    {
      "key_id": "ak_abc123...",
      "name": "Claude Desktop",
      "created_at": "2026-01-31T12:00:00Z",
      "expires_at": "2027-01-31T12:00:00Z",
      "is_expired": false,
      "is_revoked": false,
      "is_active": true,
      "database": {
        "target_id": "dt_abc123...",
        "name": "work-db",
        "database_id": "work-db"
      }
    }
  ],
  "count": 1,
  "max_keys": 10
}
```

### Revoke API Key

```http
DELETE /api-keys/{key_id}
Authorization: Bearer <auth0-jwt>
```

Response:
```json
{
  "success": true,
  "key_id": "ak_abc123...",
  "message": "API key revoked successfully"
}
```
