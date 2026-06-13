#!/usr/bin/env bash
#
# Dev loop for the local dashboard.
#
# - Seeds .dev-state/ with the token / config / credential files the dashboard
#   expects (idempotent — re-run safely).
# - Brings up deploy/docker-compose.dev.yml (arcadedb + bifrost + netns
#   placeholder) so wizard, query, and chat features actually work.
# - Runs uvicorn on the host with --reload pointing at the source tree, so
#   edits to api/app/local_dashboard/ (static or Python) show up instantly.
# - Pre-seeds a dev session via LOCAL_DASHBOARD_DEV_MODE + /dev/login, so you
#   never have to go through the token handshake or account setup.
#
# Usage:
#   scripts/dev-local-dashboard.sh
# Then open http://127.0.0.1:4466/dev/login once to set the session cookie.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_STATE="$REPO_ROOT/.dev-state"
VENV_DIR="$REPO_ROOT/venv"
VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
VENV_UVICORN="$VENV_DIR/bin/uvicorn"
COMPOSE_FILE="$REPO_ROOT/deploy/docker-compose.dev.yml"

blue()  { printf "\033[0;34m[dev-dashboard]\033[0m %s\n" "$1"; }
green() { printf "\033[0;32m[\xE2\x9C\x93]\033[0m %s\n" "$1"; }
red()   { printf "\033[0;31m[\xE2\x9C\x97]\033[0m %s\n" "$1" >&2; }

# ---------- sanity checks ----------
if [[ ! -x "$VENV_PY" ]]; then
  red "Expected Python venv at $VENV_DIR — create it and install api/requirements-local-dashboard.txt first."
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  red "docker CLI not found on PATH."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  red "docker compose plugin not available."
  exit 1
fi

# ---------- install missing python deps into venv ----------
if ! "$VENV_PY" -c "import docker" >/dev/null 2>&1; then
  blue "Installing docker SDK into venv..."
  "$VENV_PIP" install --quiet docker
  green "docker SDK installed"
fi

# ---------- seed .dev-state/ ----------
mkdir -p "$DEV_STATE"

DEV_TOKEN_FILE="$DEV_STATE/local-dashboard.token"
DEV_SYNC_TOKEN_FILE="$DEV_STATE/local-sync.token"
DEV_API_KEY_FILE="$DEV_STATE/local-api.token"
DEV_REGISTRY_FILE="$DEV_STATE/databases.json"
DEV_KEYS_FILE="$DEV_STATE/dashboard-api-keys.json"
DEV_CREDS_FILE="$DEV_STATE/dashboard-credentials.json"
DEV_PREFS_FILE="$DEV_STATE/dashboard-preferences.json"
DEV_BIFROST_CONFIG="$DEV_STATE/chat-bifrost-config.json"
DEV_ARCADEDB_ROOT_PASSWORD_FILE="$DEV_STATE/arcadedb-root.password"

seed_random_token() {
  local path="$1"
  if [[ ! -s "$path" ]]; then
    "$VENV_PY" -c "import secrets; print(secrets.token_urlsafe(32))" > "$path"
    chmod 600 "$path"
  fi
}

seed_json() {
  local path="$1"
  local body="$2"
  if [[ ! -s "$path" ]]; then
    printf '%s\n' "$body" > "$path"
    chmod 600 "$path"
  fi
}

seed_random_token "$DEV_TOKEN_FILE"
seed_random_token "$DEV_SYNC_TOKEN_FILE"
seed_random_token "$DEV_API_KEY_FILE"
# The ArcadeDB container reads this file (mounted read-only at
# /opt/arcadedb/root-password) via -Darcadedb.server.rootPasswordPath.
# Generating it up front avoids the interactive first-run prompt.
seed_random_token "$DEV_ARCADEDB_ROOT_PASSWORD_FILE"

seed_json "$DEV_KEYS_FILE"       '{"version":1,"keys":[]}'
seed_json "$DEV_PREFS_FILE"      '{"version":1}'
seed_json "$DEV_BIFROST_CONFIG"  '{"providers":{}}'

# Pre-register the dev database so the dashboard lists it on first load. The
# matching container is defined in deploy/docker-compose.dev.yml.
if [[ ! -s "$DEV_REGISTRY_FILE" ]]; then
  "$VENV_PY" - <<'PY' > "$DEV_REGISTRY_FILE"
import json
from datetime import datetime, timezone
now = datetime.now(timezone.utc).isoformat()
print(json.dumps({
    "version": 1,
    "databases": [
        {
            "database_id": "dev",
            "name": "Dev",
            "profile_id": "memory-default",
            "profile_version": 1,
            "sslmode": "disable",
            "schema_hash": None,
            "tool_manifest": {},
            "created_at": now,
            "updated_at": now,
        }
    ],
}, indent=2))
PY
  chmod 600 "$DEV_REGISTRY_FILE"
fi

# Pre-seed credentials so _is_account_setup() reports True and nothing nudges
# us toward the first-run setup wizard. Matches main.py's pbkdf2 params.
if [[ ! -s "$DEV_CREDS_FILE" ]]; then
  "$VENV_PY" - <<'PY' > "$DEV_CREDS_FILE"
import hashlib, json, secrets
from datetime import datetime, timezone
salt = secrets.token_hex(32)
password_hash = hashlib.pbkdf2_hmac(
    "sha256", b"devpass", salt.encode("utf-8"), 260000
).hex()
print(json.dumps({
    "version": 1,
    "username": "dev",
    "password_hash": password_hash,
    "salt": salt,
    "created_at": datetime.now(timezone.utc).isoformat(),
}, indent=2))
PY
  chmod 600 "$DEV_CREDS_FILE"
fi

green "Dev state seeded at $DEV_STATE"

# ---------- dev stack ----------
blue "Bringing up dev containers (arcadedb + bifrost + netns placeholder)..."
docker compose -f "$COMPOSE_FILE" up -d
green "Dev containers up"

# ---------- env for uvicorn ----------
export LOCAL_DASHBOARD_DEV_MODE=1
export LOCAL_DASHBOARD_DEV_SESSION_ID="${LOCAL_DASHBOARD_DEV_SESSION_ID:-dev-session}"
export LOCAL_DASHBOARD_SESSION_COOKIE_SECURE=false
export LOCAL_DASHBOARD_SESSION_TTL_SECONDS=31536000

export LOCAL_DASHBOARD_TOKEN_FILE="$DEV_TOKEN_FILE"
export LOCAL_SYNC_TOKEN_FILE="$DEV_SYNC_TOKEN_FILE"
export LOCAL_API_KEY_FILE="$DEV_API_KEY_FILE"
export LOCAL_DASHBOARD_REGISTRY_FILE="$DEV_REGISTRY_FILE"
export LOCAL_DASHBOARD_KEYS_FILE="$DEV_KEYS_FILE"
export LOCAL_DASHBOARD_CREDENTIALS_FILE="$DEV_CREDS_FILE"
export LOCAL_DASHBOARD_PREFERENCES_FILE="$DEV_PREFS_FILE"
export LOCAL_DASHBOARD_BIFROST_CONFIG_FILE="$DEV_BIFROST_CONFIG"

export LOCAL_DASHBOARD_TAILSCALE_CONTAINER=loreholm-dev-tailscale
export LOCAL_DASHBOARD_BIFROST_CONTAINER=loreholm-dev-bifrost
export LOCAL_DASHBOARD_BIFROST_URL=http://127.0.0.1:8080
export LOCAL_DASHBOARD_ARCADEDB_HOST=127.0.0.1
export LOCAL_DASHBOARD_ARCADEDB_PORT=2480
export LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE="$DEV_ARCADEDB_ROOT_PASSWORD_FILE"

green "Dev stack ready"
blue ""
blue "  \xE2\x86\x92 Open http://127.0.0.1:4466/dev/login (once) to set the session cookie."
blue "  \xE2\x86\x92 Edits to api/app/local_dashboard/static/ — browser refresh."
blue "  \xE2\x86\x92 Edits to api/app/local_dashboard/*.py — uvicorn --reload handles it."
blue "  \xE2\x86\x92 Stop dev containers: docker compose -f deploy/docker-compose.dev.yml down"
blue ""

cd "$REPO_ROOT/api"
exec "$VENV_UVICORN" app.local_dashboard.main:app \
  --host 127.0.0.1 \
  --port 4466 \
  --reload \
  --reload-dir app/local_dashboard
