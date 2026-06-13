from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


LOCAL_DASHBOARD_TOKEN_FILE = Path(
    os.getenv("LOCAL_DASHBOARD_TOKEN_FILE", "/opt/loreholm/local-dashboard.token")
)
LOCAL_SYNC_TOKEN_FILE = Path(
    os.getenv("LOCAL_SYNC_TOKEN_FILE", "/opt/loreholm/local-sync.token")
)
LOCAL_DASHBOARD_REGISTRY_FILE = Path(
    os.getenv("LOCAL_DASHBOARD_REGISTRY_FILE", "/opt/loreholm/databases.json")
)
LOCAL_DASHBOARD_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
LOCAL_DASHBOARD_SESSION_COOKIE = os.getenv(
    "LOCAL_DASHBOARD_SESSION_COOKIE",
    "loreholm_local_dashboard_session",
)
LOCAL_DASHBOARD_SESSION_TTL_SECONDS = int(
    os.getenv("LOCAL_DASHBOARD_SESSION_TTL_SECONDS", "3600")
)
LOCAL_DASHBOARD_SESSION_COOKIE_SECURE = (
    os.getenv("LOCAL_DASHBOARD_SESSION_COOKIE_SECURE", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)
LOCAL_DASHBOARD_TAILSCALE_CONTAINER = (
    os.getenv("LOCAL_DASHBOARD_TAILSCALE_CONTAINER", "loreholm-tailscale").strip()
    or "loreholm-tailscale"
)
# Single ArcadeDB server per install. All per-database work is
# addressed through this host/port; the per-container port range, the image
# override, and the Memgraph-era allocator are gone.
LOCAL_DASHBOARD_ARCADEDB_HOST = (
    os.getenv("LOCAL_DASHBOARD_ARCADEDB_HOST", "loreholm-arcadedb").strip()
    or "loreholm-arcadedb"
)
LOCAL_DASHBOARD_ARCADEDB_PORT = int(os.getenv("LOCAL_DASHBOARD_ARCADEDB_PORT", "2480"))
LOCAL_DASHBOARD_BIFROST_URL = (
    os.getenv("LOCAL_DASHBOARD_BIFROST_URL", "http://loreholm-bifrost-proxy:8080").strip()
    or "http://loreholm-bifrost-proxy:8080"
)
LOCAL_DASHBOARD_BIFROST_TIMEOUT_SECONDS = float(
    os.getenv("LOCAL_DASHBOARD_BIFROST_TIMEOUT_SECONDS", "8.0")
)
LOCAL_DASHBOARD_PROVIDER_DISCOVERY_TIMEOUT_SECONDS = float(
    os.getenv("LOCAL_DASHBOARD_PROVIDER_DISCOVERY_TIMEOUT_SECONDS", "12.0")
)
LOCAL_DASHBOARD_BIFROST_CONFIG_FILE = Path(
    os.getenv("LOCAL_DASHBOARD_BIFROST_CONFIG_FILE", "/opt/loreholm/chat-bifrost-config.json")
)
LOCAL_DASHBOARD_BIFROST_CONTAINER = os.getenv(
    "LOCAL_DASHBOARD_BIFROST_CONTAINER", "loreholm-bifrost-proxy"
).strip()
LOCAL_DASHBOARD_WIZARD_MODEL = os.getenv("LOCAL_DASHBOARD_WIZARD_MODEL", "").strip()
# Active embedding model on the dashboard side. `harrier-270m` is the primary
# (640-dim, MTEB-v2 ~66.5); `minilm` is the documented fallback (384-dim,
# lower quality, much cheaper on low-end CPUs). Switching after a database is
# populated requires re-embedding everything — this is a per-install choice,
# not a runtime auto-selection. Recorded alongside dimensions in
# `databases.json` so the ArcadeDB index config always matches the active
# encoder.
DASHBOARD_EMBEDDING_MODEL = (
    os.getenv("DASHBOARD_EMBEDDING_MODEL", "harrier-270m").strip().lower()
    or "harrier-270m"
)
# Root password used by the dashboard to talk to the shared ArcadeDB server.
# Under the single-server architecture the installer generates this once and
# mounts it as a file into both the arcadedb container and the dashboard;
# `_load_arcadedb_root_password()` reads it on demand.
LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE = Path(
    os.getenv(
        "LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE",
        "/opt/arcadedb/root-password",
    )
)


def _load_arcadedb_root_password() -> str:
    try:
        if LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE.is_file():
            password = LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE.read_text(
                encoding="utf-8"
            ).strip()
            if password:
                return password
    except OSError:
        pass
    raise RuntimeError(
        "ArcadeDB root password is not configured. Expected "
        f"{LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE} to exist and be readable."
    )
# Staging-vertex TTL surfaced in dashboard logs (Phase 3.6). Pending Staging
# rows older than this are flagged as a sign the reconciler is backed up.
STAGING_MAX_AGE_SECONDS = int(os.getenv("STAGING_MAX_AGE_SECONDS", "3600"))
# Reconciler cadence (Phase 4.2). Tunable per-database via the dashboard UI
# (Phase 7) — these env values set the process-wide default.
RECONCILER_POLL_INTERVAL_SECONDS = float(
    os.getenv("RECONCILER_POLL_INTERVAL_SECONDS", "5")
)
RECONCILER_BATCH_SIZE = int(os.getenv("RECONCILER_BATCH_SIZE", "20"))
# Cosine-distance thresholds for the three-band reconciler decision
# (Phase 4.3). Defaults are starting values — the dashboard UI allows
# per-database overrides.
RECONCILER_MERGE_THRESHOLD = float(
    os.getenv("RECONCILER_MERGE_THRESHOLD", "0.15")
)
RECONCILER_REVIEW_THRESHOLD = float(
    os.getenv("RECONCILER_REVIEW_THRESHOLD", "0.30")
)
# Embedding blend weight applied when merging a Staging payload into an
# existing Entity (Phase 4.4). 0.1 means 90% old / 10% new — enough drift to
# track phrasing changes without letting a single weird proposal steer the
# centroid off.
RECONCILER_MERGE_EMBED_BLEND = float(
    os.getenv("RECONCILER_MERGE_EMBED_BLEND", "0.1")
)
LOCAL_API_KEY_FILE = Path(
    os.getenv("LOCAL_API_KEY_FILE", "/opt/loreholm/local-api.token")
)
LOCAL_DASHBOARD_KEYS_FILE = Path(
    os.getenv("LOCAL_DASHBOARD_KEYS_FILE", "/opt/loreholm/dashboard-api-keys.json")
)
LOCAL_DASHBOARD_CREDENTIALS_FILE = Path(
    os.getenv("LOCAL_DASHBOARD_CREDENTIALS_FILE", "/opt/loreholm/dashboard-credentials.json")
)
LOCAL_DASHBOARD_PREFERENCES_FILE = Path(
    os.getenv("LOCAL_DASHBOARD_PREFERENCES_FILE", "/opt/loreholm/dashboard-preferences.json")
)
LOCAL_DASHBOARD_POLICIES_FILE = Path(
    os.getenv("LOCAL_DASHBOARD_POLICIES_FILE", "/opt/loreholm/policies.json")
)
LOCAL_DASHBOARD_CHAT_DB_FILE = Path(
    os.getenv("LOCAL_DASHBOARD_CHAT_DB_FILE", "/opt/loreholm/chat.db")
)
POLICY_RATE_LIMIT_PER_MINUTE = int(os.getenv("POLICY_RATE_LIMIT_PER_MINUTE", "600"))
POLICY_RATE_LIMIT_BURST = int(os.getenv("POLICY_RATE_LIMIT_BURST", "120"))
# Dev-loop escape hatch: when enabled, a deterministic session is pre-seeded and
# a /dev/login endpoint sets the cookie so uvicorn --reload iteration doesn't
# require going through the handshake + account setup wizard. Only honoured when
# explicitly opted in via env — never enabled by default, never in the prod image.
LOCAL_DASHBOARD_DEV_MODE = (
    os.getenv("LOCAL_DASHBOARD_DEV_MODE", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)
LOCAL_DASHBOARD_DEV_SESSION_ID = (
    os.getenv("LOCAL_DASHBOARD_DEV_SESSION_ID", "").strip() or "dev-session"
)

_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROPERTY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DATABASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_MUTATION_RE = re.compile(
    r"\b(create|merge|delete|detach|set|drop|remove)\b", re.IGNORECASE
)
# Schema-DDL statements that must run outside an explicit transaction.
_SCHEMA_DDL_RE = re.compile(
    r"^\s*(create|drop)\s+"
    r"(index|constraint|edge\s+index|point\s+index|text\s+index|vector\s+index)"
    r"\b",
    re.IGNORECASE,
)
# Strips leading whitespace, `// line comments`, and `/* block comments */`
# so a query like `// create index for id lookups\nCREATE INDEX ON :Person(id)`
# still trips the DDL detector. Cypher's comment syntax is the same as C++ —
# without this the DDL fast-path silently falls through to the transactional
# branch and the statement dies with "schema modification not allowed in
# multicommand transactions".
_CYPHER_LEADING_NOISE_RE = re.compile(
    r"(?:\s+|//[^\n]*\n?|/\*.*?\*/)+",
    re.DOTALL,
)

# Per-database authored schema. Empty by default on new databases; the legacy
# global entity-type vocabulary is fully deprecated. Write operations that land
# against an empty schema are rejected at the MCP validator layer — the wizard
# walks the user through authoring an initial schema.
DEFAULT_SCHEMA: dict[str, Any] = {
    "entity_types": [],
    "relationship_types": [],
    "entity_type_aliases": {},
    "relationship_type_aliases": {},
}

# Fields on a database registry record that contribute to the profile_hash.
# Intentional exclude list: runtime/liveness fields (`status`, `last_seen_at`,
# `updated_at`, `created_at`) are NOT included because they tick without
# representing a state change from the cloud's perspective.
_PROFILE_HASH_FIELDS = (
    "database_id",
    "name",
    "host",
    "port",
    "sslmode",
    "username",
    "password",
    "profile_id",
    "schema",
    "tool_manifest",
    "reconciler",
)

_LOOPBACK_HOST_ALIASES = {"", "localhost", "127.0.0.1", "::1"}
