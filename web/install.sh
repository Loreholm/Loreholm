#!/usr/bin/env bash
#
# loreholm BYODB Install Script (Linux/macOS)
# Usage: curl -fsSL loreholm.com/install.sh | bash -s -- --key <pre-auth-key>
# 
# For Windows, use: irm loreholm.com/install.ps1 | iex
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
HEADSCALE_URL="${HEADSCALE_URL:-https://loreholm.com:50443}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.loreholm}"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
CHAT_COMPOSE_FILE="$INSTALL_DIR/docker-compose.chat.yml"
LOCAL_DASHBOARD_DIR="$INSTALL_DIR/local-dashboard"
LOCAL_DASHBOARD_FILE="$LOCAL_DASHBOARD_DIR/local-dashboard.json"
LOCAL_DASHBOARD_ENDPOINT_PORT=8081
LOCAL_DASHBOARD_TOKEN_FILE="$INSTALL_DIR/local-dashboard.token"
LOCAL_SYNC_TOKEN_FILE="$INSTALL_DIR/local-sync.token"
LOCAL_API_KEY_FILE="$INSTALL_DIR/local-api.token"
DATABASE_REGISTRY_FILE="$INSTALL_DIR/databases.json"
CHAT_BIFROST_CONFIG_FILE="$INSTALL_DIR/chat-bifrost-config.json"
LOCAL_DASHBOARD_KEYS_FILE="$INSTALL_DIR/dashboard-api-keys.json"
LOCAL_DASHBOARD_CREDENTIALS_FILE="$INSTALL_DIR/dashboard-credentials.json"
LOCAL_DASHBOARD_API_KEYS_FILE="$INSTALL_DIR/dashboard-api-keys.json"
LOCAL_DASHBOARD_PREFERENCES_FILE="$INSTALL_DIR/dashboard-preferences.json"
LOCAL_DASHBOARD_CHAT_DB_FILE="$INSTALL_DIR/chat.db"
LOCAL_DASHBOARD_IMAGE="${LOCAL_DASHBOARD_IMAGE:-ghcr.io/loreholm/mcp-local-dashboard:latest}"
BIFROST_IMAGE="${BIFROST_IMAGE:-${MCP_API_IMAGE:-maximhq/bifrost:latest}}"
ARCADEDB_IMAGE="${ARCADEDB_IMAGE:-arcadedata/arcadedb:26.3.1}"
ARCADEDB_ROOT_PASSWORD_FILE="$INSTALL_DIR/arcadedb-root.password"
LOCAL_SYNC_SHARED_TOKEN="${LOCAL_SYNC_SHARED_TOKEN:-}"
# Set to "1" when the caller explicitly passed `--sync-token` so that the
# token generator knows to overwrite any pre-existing local-sync.token on
# disk. Re-running the install command to pick up a new per-user derived
# token relies on this — without it, `generate_local_sync_token` would
# early-return and silently keep the stale token.
LOCAL_SYNC_SHARED_TOKEN_EXPLICIT=""
LOCAL_DASHBOARD_NETWORK_ACCESS="${LOCAL_DASHBOARD_NETWORK_ACCESS:-}"

# Profile / embedding / memory selection. CLI flags win over env vars;
# both are recorded here so the generator knows which values were
# explicitly set versus inferred from host detection.
LOREHOLM_PROFILE="${LOREHOLM_PROFILE:-}"
LOREHOLM_EMBEDDING_MODEL="${LOREHOLM_EMBEDDING_MODEL:-}"
LOREHOLM_ARCADEDB_MEMORY="${LOREHOLM_ARCADEDB_MEMORY:-}"
LOREHOLM_PROFILE_EXPLICIT=""
LOREHOLM_EMBEDDING_MODEL_EXPLICIT=""
LOREHOLM_ARCADEDB_MEMORY_EXPLICIT=""
if [[ -n "$LOREHOLM_PROFILE" ]]; then LOREHOLM_PROFILE_EXPLICIT="1"; fi
if [[ -n "$LOREHOLM_EMBEDDING_MODEL" ]]; then LOREHOLM_EMBEDDING_MODEL_EXPLICIT="1"; fi
if [[ -n "$LOREHOLM_ARCADEDB_MEMORY" ]]; then LOREHOLM_ARCADEDB_MEMORY_EXPLICIT="1"; fi

SELECTED_PROFILE=""
SELECTED_EMBEDDING_MODEL=""
SELECTED_ARCADEDB_MEMORY=""
DETECTED_RAM_MB=""
DETECTED_ARCH=""

# Variables
PRE_AUTH_KEY=""
NODE_NAME=""
LOCAL_ADMIN_PORT=4466
LOCAL_ADMIN_BIND_HOST="0.0.0.0"
LOCAL_ADMIN_ACCESS="network"
LOCAL_ADMIN_DISPLAY_HOST=""
LOCAL_LAN_IP=""

log() {
    echo -e "${BLUE}[loreholm]${NC} $1"
}

success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

error() {
    echo -e "${RED}[✗]${NC} $1" >&2
    exit 1
}

show_banner() {
    echo -e "${BLUE}"
    cat << 'EOF'
                                __           ____  
   _________  _      ______  __/ /_______   / __ \____
  / ___/ __ \| | /| / / __ \/ ___/ //_/  / / / / __ /
 / /__/ /_/ /| |/ |/ / /_/ / /  / ,<    / /_/ / /_/ /
 \___/\____/ |__/|__/\____/_/  /_/|_|  /_____/\__,_/

    Bring Your Own Database - Memory for LLMs
EOF
    echo -e "${NC}"
}

usage() {
    cat << EOF
Usage: $0 --key <pre-auth-key> [options]

Required:
  --key <key>       Pre-authentication key from loreholm.com dashboard

Options:
  --name <name>             Custom node name (default: hostname)
  --headscale <url>         Headscale server URL (default: $HEADSCALE_URL)
  --sync-token <t>          Shared cloud->local sync token (optional, recommended)
  --dir <path>              Installation directory (default: $INSTALL_DIR)
  --profile <p>             Resource profile: small, default, generous (auto-detected from RAM)
  --embedding-model <m>     Embedding model: harrier-270m or minilm (auto-selected; arm64 defaults to minilm)
  --arcadedb-memory <opts>  JVM heap args for ArcadeDB (e.g. "-Xms800M -Xmx800M"); overrides profile
  -h, --help                Show this help message

Examples:
  $0 --key preauthkey-abc123
  $0 --key preauthkey-abc123 --name my-workstation

EOF
    exit 0
}

require_option_value() {
    if [[ $# -lt 2 ]]; then
        error "Option $1 requires a value"
    fi
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --key)
                require_option_value "$@"
                PRE_AUTH_KEY="$2"
                shift 2
                ;;
            --name)
                require_option_value "$@"
                NODE_NAME="$2"
                shift 2
                ;;
            --headscale)
                require_option_value "$@"
                HEADSCALE_URL="$2"
                shift 2
                ;;
            --dir)
                require_option_value "$@"
                INSTALL_DIR="$2"
                COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
                LOCAL_DASHBOARD_DIR="$INSTALL_DIR/local-dashboard"
                LOCAL_DASHBOARD_FILE="$LOCAL_DASHBOARD_DIR/local-dashboard.json"
                LOCAL_DASHBOARD_TOKEN_FILE="$INSTALL_DIR/local-dashboard.token"
                LOCAL_SYNC_TOKEN_FILE="$INSTALL_DIR/local-sync.token"
                DATABASE_REGISTRY_FILE="$INSTALL_DIR/databases.json"
                CHAT_BIFROST_CONFIG_FILE="$INSTALL_DIR/chat-bifrost-config.json"
                ARCADEDB_ROOT_PASSWORD_FILE="$INSTALL_DIR/arcadedb-root.password"
                shift 2
                ;;
            --sync-token)
                require_option_value "$@"
                LOCAL_SYNC_SHARED_TOKEN="$2"
                LOCAL_SYNC_SHARED_TOKEN_EXPLICIT="1"
                shift 2
                ;;
            --profile)
                require_option_value "$@"
                LOREHOLM_PROFILE="$2"
                LOREHOLM_PROFILE_EXPLICIT="1"
                shift 2
                ;;
            --embedding-model)
                require_option_value "$@"
                LOREHOLM_EMBEDDING_MODEL="$2"
                LOREHOLM_EMBEDDING_MODEL_EXPLICIT="1"
                shift 2
                ;;
            --arcadedb-memory)
                require_option_value "$@"
                LOREHOLM_ARCADEDB_MEMORY="$2"
                LOREHOLM_ARCADEDB_MEMORY_EXPLICIT="1"
                shift 2
                ;;
            -h|--help)
                usage
                ;;
            *)
                error "Unknown option: $1"
                ;;
        esac
    done

    if [[ -z "$PRE_AUTH_KEY" ]]; then
        if [[ -f "$COMPOSE_FILE" ]]; then
            error "Pre-auth key is required for first-time install. Existing installation detected at $INSTALL_DIR. Run: curl -fsSL https://loreholm.com/update.sh | bash"
        fi
        error "Pre-auth key is required. Use --key <key>"
    fi

    # Default node name to hostname
    if [[ -z "$NODE_NAME" ]]; then
        NODE_NAME=$(hostname 2>/dev/null || echo "loreholm-node")
    fi

    # Normalize node name so compose YAML cannot be corrupted by control chars.
    NODE_NAME=$(printf "%s" "$NODE_NAME" | tr -d '\r\n' | sed 's/[^A-Za-z0-9_.-]/-/g')
    if [[ -z "$NODE_NAME" ]]; then
        NODE_NAME="loreholm-node"
    fi
}

check_dependencies() {
    log "Checking dependencies..."
    
    # Check for Docker
    if ! command -v docker &> /dev/null; then
        error "Docker is not installed. Please install Docker first: https://docs.docker.com/get-docker/"
    fi
    
    # Check Docker is running
    if ! docker info &> /dev/null; then
        error "Docker daemon is not running. Please start Docker and try again."
    fi
    
    # Check for Docker Compose
    if docker compose version &> /dev/null; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        error "Docker Compose is not installed. Please install Docker Compose: https://docs.docker.com/compose/install/"
    fi
    
    success "Docker and Docker Compose are available"
}

# Detect total physical RAM (MB) and CPU architecture so select_profile
# can pick a resource tier. Writes into DETECTED_RAM_MB / DETECTED_ARCH.
detect_host_specs() {
    local ram_kb=""
    local ram_mb=""
    local arch=""

    if [[ -r /proc/meminfo ]]; then
        ram_kb=$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)
        if [[ -n "$ram_kb" ]]; then
            ram_mb=$(( ram_kb / 1024 ))
        fi
    fi
    if [[ -z "$ram_mb" ]] && command -v sysctl &>/dev/null; then
        # macOS: hw.memsize is bytes.
        local ram_bytes=""
        ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || true)
        if [[ -n "$ram_bytes" ]]; then
            ram_mb=$(( ram_bytes / 1024 / 1024 ))
        fi
    fi
    if [[ -z "$ram_mb" ]]; then
        ram_mb=0
    fi

    arch=$(uname -m 2>/dev/null || echo "unknown")

    DETECTED_RAM_MB="$ram_mb"
    DETECTED_ARCH="$arch"
    log "Detected host: ${ram_mb} MB RAM, arch ${arch}"
}

# Choose profile + embedding model + ArcadeDB heap from detected host
# specs, honoring any explicit CLI/env overrides. Populates
# SELECTED_PROFILE / SELECTED_EMBEDDING_MODEL / SELECTED_ARCADEDB_MEMORY.
select_profile() {
    local ram_mb="$DETECTED_RAM_MB"
    local arch="$DETECTED_ARCH"

    # Profile tier.
    local profile=""
    if [[ -n "$LOREHOLM_PROFILE" ]]; then
        profile="$LOREHOLM_PROFILE"
    elif [[ "$ram_mb" -lt 4096 ]]; then
        warn "Host has less than 4 GB RAM (${ram_mb} MB); proceeding with 'small' profile but expect slow embedding performance."
        profile="small"
    elif [[ "$ram_mb" -lt 8192 ]]; then
        profile="small"
    elif [[ "$ram_mb" -lt 16384 ]]; then
        profile="default"
    else
        profile="generous"
    fi
    case "$profile" in
        small|default|generous) ;;
        *)
            error "Unknown --profile value '$profile' (expected: small, default, generous)."
            ;;
    esac
    SELECTED_PROFILE="$profile"

    # Embedding model. arm64 hosts default to minilm unless explicitly overridden.
    local model=""
    if [[ -n "$LOREHOLM_EMBEDDING_MODEL" ]]; then
        model="$LOREHOLM_EMBEDDING_MODEL"
    else
        case "$profile" in
            small)    model="minilm" ;;
            default)  model="harrier-270m" ;;
            generous) model="harrier-270m" ;;
        esac
        case "$arch" in
            aarch64|arm64)
                if [[ "$model" != "minilm" ]]; then
                    log "arm64 host detected — defaulting to minilm embeddings. Override with --embedding-model harrier-270m."
                    model="minilm"
                fi
                ;;
        esac
    fi
    case "$model" in
        minilm|harrier-270m) ;;
        *)
            error "Unknown --embedding-model value '$model' (expected: minilm, harrier-270m)."
            ;;
    esac
    SELECTED_EMBEDDING_MODEL="$model"

    # ArcadeDB JVM heap. Explicit wins; otherwise derived from profile tier.
    local memory=""
    if [[ -n "$LOREHOLM_ARCADEDB_MEMORY" ]]; then
        memory="$LOREHOLM_ARCADEDB_MEMORY"
    else
        case "$profile" in
            small)    memory="-Xms512M -Xmx512M" ;;
            default)  memory="-Xms800M -Xmx800M" ;;
            generous) memory="-Xms2G -Xmx2G" ;;
        esac
    fi
    SELECTED_ARCADEDB_MEMORY="$memory"

    log "Profile: ${SELECTED_PROFILE} | embedding: ${SELECTED_EMBEDDING_MODEL} | arcadedb-memory: ${SELECTED_ARCADEDB_MEMORY}"
}

generate_arcadedb_root_password() {
    if [[ -f "$ARCADEDB_ROOT_PASSWORD_FILE" ]] && [[ -s "$ARCADEDB_ROOT_PASSWORD_FILE" ]]; then
        chmod 600 "$ARCADEDB_ROOT_PASSWORD_FILE" 2>/dev/null || true
        success "Using existing ArcadeDB root password"
        return
    fi

    local token=""
    if command -v openssl &>/dev/null; then
        token=$(openssl rand -hex 32 2>/dev/null || true)
    fi
    if [[ -z "$token" ]]; then
        token=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
    fi

    # ArcadeDB reads this file with a trailing-newline-tolerant loader, but
    # printf without a newline avoids any ambiguity for other tools.
    printf "%s" "$token" > "$ARCADEDB_ROOT_PASSWORD_FILE"
    chmod 600 "$ARCADEDB_ROOT_PASSWORD_FILE" 2>/dev/null || true
    success "Generated ArcadeDB root password"
}

create_install_dir() {
    log "Creating installation directory: $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$LOCAL_DASHBOARD_DIR"
    success "Installation directory created"
}

detect_local_lan_ip() {
    log "Detecting LAN IP for local dashboard resolution..."

    local detected=""

    if [[ "$OSTYPE" == "darwin"* ]]; then
        local default_if=""
        default_if=$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}' || true)
        if [[ -n "$default_if" ]] && command -v ipconfig &> /dev/null; then
            detected=$(ipconfig getifaddr "$default_if" 2>/dev/null || true)
        fi
        if [[ -z "$detected" ]] && command -v ipconfig &> /dev/null; then
            detected=$(ipconfig getifaddr en0 2>/dev/null || true)
        fi
    else
        if command -v ip &> /dev/null; then
            detected=$(ip -4 route get 1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)
        fi
        if [[ -z "$detected" ]] && command -v hostname &> /dev/null; then
            detected=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
        fi
    fi

    if [[ -z "$detected" ]]; then
        LOCAL_LAN_IP="127.0.0.1"
        warn "Could not auto-detect LAN IP; using $LOCAL_LAN_IP"
        return
    fi

    LOCAL_LAN_IP="$detected"
    success "Detected LAN IP: $LOCAL_LAN_IP"
}

is_truthy() {
    case "${1,,}" in
        1|true|yes|y|on)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

is_falsy() {
    case "${1,,}" in
        0|false|no|n|off)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

configure_local_dashboard_access() {
    log "Configuring local dashboard network access..."

    local allow_network=""
    if [[ -n "$LOCAL_DASHBOARD_NETWORK_ACCESS" ]]; then
        if is_truthy "$LOCAL_DASHBOARD_NETWORK_ACCESS"; then
            allow_network="yes"
        elif is_falsy "$LOCAL_DASHBOARD_NETWORK_ACCESS"; then
            allow_network="no"
        else
            warn "Ignoring invalid LOCAL_DASHBOARD_NETWORK_ACCESS value: $LOCAL_DASHBOARD_NETWORK_ACCESS"
        fi
    fi

    if [[ -z "$allow_network" ]]; then
        local prompt="Expose the local dashboard on your local network? [Y/n]: "
        local response=""
        if [[ -r /dev/tty ]]; then
            read -r -p "$prompt" response < /dev/tty || true
        elif [[ -t 0 ]]; then
            read -r -p "$prompt" response || true
        fi
        case "${response,,}" in
            ""|y|yes)
                allow_network="yes"
                ;;
            n|no)
                allow_network="no"
                ;;
            *)
                warn "Unrecognized answer '$response'; defaulting to yes."
                allow_network="yes"
                ;;
        esac
    fi

    if [[ "$allow_network" == "no" ]]; then
        LOCAL_ADMIN_BIND_HOST="127.0.0.1"
        LOCAL_ADMIN_ACCESS="localhost"
        LOCAL_ADMIN_DISPLAY_HOST="127.0.0.1"
        warn "Local dashboard access set to local-only (127.0.0.1)."
    else
        LOCAL_ADMIN_BIND_HOST="0.0.0.0"
        LOCAL_ADMIN_ACCESS="network"
        LOCAL_ADMIN_DISPLAY_HOST="$LOCAL_LAN_IP"
        success "Local dashboard access set to local network ($LOCAL_ADMIN_DISPLAY_HOST)."
    fi
}

write_local_dashboard_metadata() {
    mkdir -p "$LOCAL_DASHBOARD_DIR"
    cat > "$LOCAL_DASHBOARD_FILE" << EOF
{
  "lan_ip": "${LOCAL_LAN_IP}",
  "port": ${LOCAL_ADMIN_PORT},
  "path": "/",
  "local_admin_host": "${LOCAL_ADMIN_DISPLAY_HOST}",
  "local_admin_port": ${LOCAL_ADMIN_PORT},
  "local_admin_path": "/",
  "local_admin_access": "${LOCAL_ADMIN_ACCESS}",
  "source": "loreholm-install.sh"
}
EOF
}

write_local_dashboard_endpoint_server() {
    mkdir -p "$LOCAL_DASHBOARD_DIR"
    cat > "$LOCAL_DASHBOARD_DIR/endpoint_server.py" << 'PYEOF'
#!/usr/bin/env python3
"""Tailnet-facing shim for the FastAPI local dashboard.

Runs inside the Tailscale container's network namespace (the only thing
on this host with a Tailnet IP) and forwards every `/api/sync/*` request
to the real FastAPI local dashboard container over the Docker bridge.
Sync routes on the FastAPI side enforce bearer-token auth against the
same `local-sync.token` file the cloud's per-user derived token is
compared against, so the shim only relays headers — it does not verify
the bearer itself.

Also serves two local-only routes that predate the sync shim and don't
belong on FastAPI:
  GET /healthz              — liveness check
  GET /local-dashboard.json — LAN-admin metadata for the dashboard link
"""
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


META_FILE = os.getenv("LOCAL_DASHBOARD_META_FILE", "/opt/local-dashboard/local-dashboard.json")
BIND_HOST = os.getenv("LOCAL_SYNC_BIND_HOST", "0.0.0.0")
BIND_PORT = int(os.getenv("LOCAL_SYNC_BIND_PORT", "8081"))

# Upstream FastAPI local dashboard. Reachable from inside the tailscale
# netns over the Docker bridge via Docker's embedded DNS — both the
# tailscale container and the loreholm-local-dashboard container live on
# the same compose-default network.
UPSTREAM_URL = os.getenv(
    "LOCAL_DASHBOARD_UPSTREAM",
    "http://loreholm-local-dashboard:4466",
)
UPSTREAM_TIMEOUT = float(os.getenv("LOCAL_DASHBOARD_UPSTREAM_TIMEOUT", "30"))

_parsed_upstream = urlparse(UPSTREAM_URL)
UPSTREAM_HOST = _parsed_upstream.hostname or "loreholm-local-dashboard"
UPSTREAM_PORT = _parsed_upstream.port or 4466

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

# Path prefixes the shim forwards to the upstream FastAPI local dashboard.
# Sync lanes carry cloud→local pull traffic; chat lanes carry the chat proxy
# traffic originating from chat.loreholm.com via the cloud /chat router.
_FORWARD_PREFIXES = ("/api/sync/", "/api/chat/")

# Paths that must stream their response body without buffering (SSE).
_STREAM_PATHS = {"/api/chat/stream"}


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _detail(code, message):
    return {"detail": {"error": {"code": code, "message": message}}}


class Handler(BaseHTTPRequestHandler):
    def _write_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self, method, stream=False):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b""

        forward_headers = {}
        for name, value in self.headers.items():
            lower = name.lower()
            if lower in _HOP_BY_HOP or lower in {"host", "content-length"}:
                continue
            forward_headers[name] = value
        if body:
            forward_headers["Content-Length"] = str(len(body))

        try:
            conn = http.client.HTTPConnection(
                UPSTREAM_HOST, UPSTREAM_PORT, timeout=UPSTREAM_TIMEOUT
            )
        except OSError as exc:
            self._write_json(
                502,
                _detail(
                    "UPSTREAM_UNREACHABLE",
                    "Could not reach local dashboard upstream at "
                    "{}:{}: {}".format(UPSTREAM_HOST, UPSTREAM_PORT, exc),
                ),
            )
            return

        try:
            try:
                conn.request(method, self.path, body=body, headers=forward_headers)
                response = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                self._write_json(
                    502,
                    _detail(
                        "UPSTREAM_UNREACHABLE",
                        "Could not reach local dashboard upstream at "
                        "{}:{}: {}".format(UPSTREAM_HOST, UPSTREAM_PORT, exc),
                    ),
                )
                return

            if stream:
                # Relay without buffering — required for SSE endpoints.
                self.send_response(response.status)
                for name, value in response.getheaders():
                    lower = name.lower()
                    if lower in _HOP_BY_HOP or lower == "content-length":
                        continue
                    self.send_header(name, value)
                self.end_headers()
                try:
                    while True:
                        chunk = response.read(4096)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        try:
                            self.wfile.flush()
                        except OSError:
                            break
                except (OSError, http.client.HTTPException):
                    pass
                return

            response_body = response.read()
            status = response.status
            response_headers = list(response.getheaders())

            self.send_response(status)
            saw_content_type = False
            for name, value in response_headers:
                lower = name.lower()
                if lower in _HOP_BY_HOP or lower == "content-length":
                    continue
                if lower == "content-type":
                    saw_content_type = True
                self.send_header(name, value)
            if not saw_content_type:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _should_forward(self):
        return any(self.path.startswith(p) for p in _FORWARD_PREFIXES)

    def _is_stream_path(self):
        return self.path in _STREAM_PATHS

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self._write_json(200, {"ok": True})
            return
        if self.path == "/local-dashboard.json":
            payload = _read_json(META_FILE, {})
            if not isinstance(payload, dict):
                payload = {}
            self._write_json(200, payload)
            return
        if self._should_forward():
            self._forward("GET", stream=self._is_stream_path())
            return
        self._write_json(404, _detail("NOT_FOUND", "Route not found."))

    def do_POST(self):  # noqa: N802
        if self._should_forward():
            self._forward("POST", stream=self._is_stream_path())
            return
        self._write_json(404, _detail("NOT_FOUND", "Route not found."))

    def do_DELETE(self):  # noqa: N802
        if self._should_forward():
            self._forward("DELETE")
            return
        self._write_json(404, _detail("NOT_FOUND", "Route not found."))

    def log_message(self, _format, *_args):  # noqa: A003
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    server.serve_forever()
PYEOF
    chmod 644 "$LOCAL_DASHBOARD_DIR/endpoint_server.py" 2>/dev/null || true
}

generate_local_dashboard_token() {
    # If the user already has credentials set up, the bootstrap token is no longer
    # needed for browser login — skip generating or surfacing it.
    if [[ -f "$LOCAL_DASHBOARD_CREDENTIALS_FILE" ]] && [[ -s "$LOCAL_DASHBOARD_CREDENTIALS_FILE" ]]; then
        # Ensure the token file exists on disk (the container still mounts it) but don't regenerate it.
        if [[ ! -f "$LOCAL_DASHBOARD_TOKEN_FILE" ]]; then
            printf "" > "$LOCAL_DASHBOARD_TOKEN_FILE"
            chmod 600 "$LOCAL_DASHBOARD_TOKEN_FILE" 2>/dev/null || true
        fi
        return
    fi

    local token=""
    if command -v openssl &>/dev/null; then
        token=$(openssl rand -hex 32 2>/dev/null || true)
    fi
    if [[ -z "$token" ]]; then
        token=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
    fi

    printf "%s\n" "$token" > "$LOCAL_DASHBOARD_TOKEN_FILE"
    chmod 600 "$LOCAL_DASHBOARD_TOKEN_FILE" 2>/dev/null || true
    success "Generated local dashboard token"
}

generate_local_sync_token() {
    # If the caller passed `--sync-token`, always overwrite — this is how
    # re-running the install command picks up a newly-derived per-user
    # token (e.g. after the cloud's LOCAL_SYNC_SIGNING_SECRET rotates).
    if [[ -n "$LOCAL_SYNC_SHARED_TOKEN_EXPLICIT" ]] && [[ -n "$LOCAL_SYNC_SHARED_TOKEN" ]]; then
        printf "%s\n" "$LOCAL_SYNC_SHARED_TOKEN" > "$LOCAL_SYNC_TOKEN_FILE"
        chmod 600 "$LOCAL_SYNC_TOKEN_FILE" 2>/dev/null || true
        success "Installed sync token from --sync-token (per-user derived)"
        return
    fi

    if [[ -f "$LOCAL_SYNC_TOKEN_FILE" ]] && [[ -s "$LOCAL_SYNC_TOKEN_FILE" ]]; then
        chmod 600 "$LOCAL_SYNC_TOKEN_FILE" 2>/dev/null || true
        success "Using existing local sync token"
        return
    fi

    local token="$LOCAL_SYNC_SHARED_TOKEN"
    if [[ -z "$token" ]]; then
        if command -v openssl &>/dev/null; then
            token=$(openssl rand -hex 32 2>/dev/null || true)
        fi
        if [[ -z "$token" ]]; then
            token=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
        fi
        warn "LOCAL_SYNC_SHARED_TOKEN was not provided; generated a local-only sync token."
    fi

    printf "%s\n" "$token" > "$LOCAL_SYNC_TOKEN_FILE"
    chmod 600 "$LOCAL_SYNC_TOKEN_FILE" 2>/dev/null || true
    success "Initialized local sync token"
}

generate_local_api_key() {
    if [[ -f "$LOCAL_API_KEY_FILE" ]] && [[ -s "$LOCAL_API_KEY_FILE" ]]; then
        chmod 600 "$LOCAL_API_KEY_FILE" 2>/dev/null || true
        success "Using existing agent API key"
        return
    fi

    local token=""
    if command -v openssl &>/dev/null; then
        token=$(openssl rand -hex 32 2>/dev/null || true)
    fi
    if [[ -z "$token" ]]; then
        token=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
    fi
    printf "%s\n" "$token" > "$LOCAL_API_KEY_FILE"
    chmod 600 "$LOCAL_API_KEY_FILE" 2>/dev/null || true
    success "Generated agent API key"
}

initialize_database_registry() {
    if [[ -f "$DATABASE_REGISTRY_FILE" ]] && [[ -s "$DATABASE_REGISTRY_FILE" ]]; then
        success "Using existing database registry"
        return
    fi

    cat > "$DATABASE_REGISTRY_FILE" << EOF
{
  "version": 1,
  "databases": []
}
EOF
    chmod 600 "$DATABASE_REGISTRY_FILE" 2>/dev/null || true
    success "Initialized empty local database registry"
}

initialize_bifrost_config() {
    if [[ -f "$CHAT_BIFROST_CONFIG_FILE" ]] && [[ -s "$CHAT_BIFROST_CONFIG_FILE" ]]; then
        success "Using existing Bifrost config"
        return
    fi

    cat > "$CHAT_BIFROST_CONFIG_FILE" << EOF
{
  "providers": {}
}
EOF
    chmod 600 "$CHAT_BIFROST_CONFIG_FILE" 2>/dev/null || true
    success "Initialized default Bifrost config"
}

initialize_dashboard_keys() {
    if [[ -f "$LOCAL_DASHBOARD_KEYS_FILE" ]] && [[ -s "$LOCAL_DASHBOARD_KEYS_FILE" ]]; then
        success "Using existing dashboard API keys"
        return
    fi

    cat > "$LOCAL_DASHBOARD_KEYS_FILE" << EOF
{"version":1,"keys":[]}
EOF
    chmod 600 "$LOCAL_DASHBOARD_KEYS_FILE" 2>/dev/null || true
    success "Initialized empty dashboard API keys store"
}

initialize_dashboard_credentials() {
    if [[ -f "$LOCAL_DASHBOARD_CREDENTIALS_FILE" ]]; then
        success "Using existing dashboard credentials"
        return
    fi

    printf "" > "$LOCAL_DASHBOARD_CREDENTIALS_FILE"
    chmod 600 "$LOCAL_DASHBOARD_CREDENTIALS_FILE" 2>/dev/null || true
    success "Initialized dashboard credentials file"
}

initialize_dashboard_api_keys() {
    if [[ -f "$LOCAL_DASHBOARD_API_KEYS_FILE" ]] && [[ -s "$LOCAL_DASHBOARD_API_KEYS_FILE" ]]; then
        success "Using existing dashboard API keys file"
        return
    fi

    cat > "$LOCAL_DASHBOARD_API_KEYS_FILE" << EOF
{"version":1,"keys":[]}
EOF
    chmod 600 "$LOCAL_DASHBOARD_API_KEYS_FILE" 2>/dev/null || true
    success "Initialized dashboard API keys file"
}

initialize_dashboard_preferences() {
    if [[ -f "$LOCAL_DASHBOARD_PREFERENCES_FILE" ]] && [[ -s "$LOCAL_DASHBOARD_PREFERENCES_FILE" ]]; then
        success "Using existing dashboard preferences"
        return
    fi

    cat > "$LOCAL_DASHBOARD_PREFERENCES_FILE" << EOF
{"version":1}
EOF
    chmod 600 "$LOCAL_DASHBOARD_PREFERENCES_FILE" 2>/dev/null || true
    success "Initialized dashboard preferences file"
}

initialize_chat_db() {
    if [[ -f "$LOCAL_DASHBOARD_CHAT_DB_FILE" ]]; then
        success "Using existing chat database"
        return
    fi

    printf "" > "$LOCAL_DASHBOARD_CHAT_DB_FILE"
    chmod 600 "$LOCAL_DASHBOARD_CHAT_DB_FILE" 2>/dev/null || true
    success "Initialized chat database file"
}

write_manage_keys_script() {
    local script_path="$INSTALL_DIR/manage-keys.sh"
    cat > "$script_path" << 'SCRIPT'
#!/usr/bin/env bash
# loreholm API key manager
# Usage:
#   manage-keys.sh list
#   manage-keys.sh create "My Laptop"
#   manage-keys.sh rotate <key_id>
#   manage-keys.sh revoke <key_id>

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/.loreholm}"
TOKEN_FILE="${LOCAL_DASHBOARD_TOKEN_FILE:-$INSTALL_DIR/local-dashboard.token}"
PORT="${LOCAL_ADMIN_PORT:-4466}"
BASE_URL="http://localhost:$PORT/api"

if [[ ! -f "$TOKEN_FILE" ]]; then
    echo "Error: bootstrap token file not found at $TOKEN_FILE" >&2
    exit 1
fi
TOKEN=$(tr -d '\r\n' < "$TOKEN_FILE")

_req() {
    local method="$1"; local path="$2"; shift 2
    curl -sf -X "$method" \
        -H "X-Local-Token: $TOKEN" \
        -H "Content-Type: application/json" \
        "$@" \
        "$BASE_URL$path"
}

case "${1:-}" in
  list)
    _req GET /auth/keys | python3 -c "
import json,sys
data=json.load(sys.stdin)
keys=data.get('keys',[])
if not keys: print('No API keys found.'); sys.exit(0)
print(f\"{'KEY ID':<28} {'LABEL':<24} {'HINT':<16} {'CREATED':<12} STATUS\")
print('-'*100)
for k in keys:
    status='USED' if k.get('used') else 'active'
    print(f\"{k['key_id']:<28} {k['label']:<24} {k['token_hint']:<16} {k['created_at'][:10]:<12} {status}\")
"
    ;;
  create)
    LABEL="${2:?Usage: manage-keys.sh create <label>}"
    _req POST /auth/keys -d "{\"label\":\"$LABEL\"}" | python3 -c "
import json,sys
r=json.load(sys.stdin)
print(f\"Created key for '{r['label']}'\")
print(f\"  Key ID : {r['key_id']}\")
print(f\"  Token  : {r['token']}\")
print('Save this token — it will not be shown again.')
"
    ;;
  rotate)
    KEY_ID="${2:?Usage: manage-keys.sh rotate <key_id>}"
    _req POST "/auth/keys/$KEY_ID/rotate" | python3 -c "
import json,sys
r=json.load(sys.stdin)
print(f\"Rotated key '{r['label']}'\")
print(f\"  Key ID : {r['key_id']}\")
print(f\"  Token  : {r['token']}\")
print('Save this token — the old one is now invalid.')
"
    ;;
  revoke)
    KEY_ID="${2:?Usage: manage-keys.sh revoke <key_id>}"
    _req DELETE "/auth/keys/$KEY_ID" | python3 -c "
import json,sys
r=json.load(sys.stdin)
print(f\"Revoked key: {r['deleted']}\")
"
    ;;
  *)
    echo "Usage: manage-keys.sh <list|create|rotate|revoke> [args]"
    exit 1
    ;;
esac
SCRIPT
    chmod +x "$script_path"
    success "Wrote manage-keys.sh to $INSTALL_DIR"
}

generate_compose_file() {
    log "Generating Docker Compose configuration..."

    cat > "$COMPOSE_FILE" << EOF
# loreholm BYODB Stack
# Generated by install.sh on $(date -Iseconds)
# Documentation: https://loreholm.com/docs
# API Reference: https://api.loreholm.com/docs
#
# Profile: ${SELECTED_PROFILE} (RAM=${DETECTED_RAM_MB} MB, arch=${DETECTED_ARCH})
# Embedding model: ${SELECTED_EMBEDDING_MODEL}
# ArcadeDB heap: ${SELECTED_ARCADEDB_MEMORY}

services:
  # Tailscale sidecar - connects to Headscale mesh network
  tailscale:
    image: tailscale/tailscale:latest
    container_name: loreholm-tailscale
    hostname: ${NODE_NAME}
    restart: unless-stopped
    # Only NET_ADMIN is required: /dev/net/tun is bind-mounted, so the host
    # already provides the tun device. Re-add SYS_MODULE only if a host lacks
    # the tun kernel module and tailscale cannot create the interface.
    cap_add:
      - NET_ADMIN
    volumes:
      - tailscale_state:/var/lib/tailscale
      - /dev/net/tun:/dev/net/tun
    environment:
      - TS_AUTHKEY=${PRE_AUTH_KEY}
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_USERSPACE=false
      # No --accept-routes: a leaf node never needs subnet routes pushed by the
      # control server, so Headscale cannot steer this node's traffic.
      - TS_EXTRA_ARGS=--login-server=${HEADSCALE_URL}
    healthcheck:
      test: ["CMD", "tailscale", "status"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # Single shared ArcadeDB server. All per-database CRUD is HTTP against
  # this one container; no per-database containers, no Docker socket.
  # Lives on the default compose bridge — NOT in the tailscale netns —
  # so 2480 is unreachable from the tailnet regardless of ACL state.
  # Reached from the dashboard as `loreholm-arcadedb:2480` over the bridge.
  arcadedb:
    image: ${ARCADEDB_IMAGE}
    container_name: loreholm-arcadedb
    restart: unless-stopped
    environment:
      JAVA_OPTS: "-Darcadedb.server.httpIncoming.port=2480 -Darcadedb.server.rootPasswordPath=/opt/arcadedb/root-password -Darcadedb.server.mode=production -Darcadedb.profile=low-ram ${SELECTED_ARCADEDB_MEMORY}"
    volumes:
      - arcadedb_data:/home/arcadedb/databases
      - arcadedb_log:/home/arcadedb/log
      - ./arcadedb-root.password:/opt/arcadedb/root-password:ro

  # Bifrost proxy for local wizard and chat-compatible /v1 model APIs.
  # Lives on the default compose bridge — NOT in the tailscale netns —
  # so 8080 is unreachable from the tailnet. Reached from the dashboard
  # as `loreholm-bifrost-proxy:8080` over the bridge.
  bifrost-proxy:
    image: ${BIFROST_IMAGE}
    container_name: loreholm-bifrost-proxy
    restart: unless-stopped
    volumes:
      - ./chat-bifrost-config.json:/app/data/config.json

  # Local admin API + setup wizard for local BYODB databases.
  local-dashboard:
    image: ${LOCAL_DASHBOARD_IMAGE}
    container_name: loreholm-local-dashboard
    restart: unless-stopped
    depends_on:
      tailscale:
        condition: service_healthy
      arcadedb:
        condition: service_started
    ports:
      - "${LOCAL_ADMIN_BIND_HOST}:${LOCAL_ADMIN_PORT}:${LOCAL_ADMIN_PORT}"
    environment:
      - LOCAL_DASHBOARD_TOKEN_FILE=/opt/loreholm/local-dashboard.token
      - LOCAL_SYNC_TOKEN_FILE=/opt/loreholm/local-sync.token
      - LOCAL_API_KEY_FILE=/opt/loreholm/local-api.token
      - LOCAL_DASHBOARD_REGISTRY_FILE=/opt/loreholm/databases.json
      - LOCAL_DASHBOARD_KEYS_FILE=/opt/loreholm/dashboard-api-keys.json
      - LOCAL_DASHBOARD_CREDENTIALS_FILE=/opt/loreholm/dashboard-credentials.json
      - LOCAL_DASHBOARD_PREFERENCES_FILE=/opt/loreholm/dashboard-preferences.json
      - LOCAL_DASHBOARD_CHAT_DB_FILE=/opt/loreholm/chat.db
      - LOCAL_DASHBOARD_BIFROST_CONFIG_FILE=/opt/loreholm/chat-bifrost-config.json
      - LOCAL_DASHBOARD_BIFROST_URL=http://loreholm-bifrost-proxy:8080
      - LOCAL_DASHBOARD_ARCADEDB_HOST=loreholm-arcadedb
      - LOCAL_DASHBOARD_ARCADEDB_PORT=2480
      - LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE=/opt/loreholm/arcadedb-root.password
      - LOCAL_DASHBOARD_EMBEDDING_MODEL=${SELECTED_EMBEDDING_MODEL}
    command:
      - uvicorn
      - app.local_dashboard.main:app
      - --host
      - 0.0.0.0
      - --port
      - "${LOCAL_ADMIN_PORT}"
    volumes:
      - ./local-dashboard.token:/opt/loreholm/local-dashboard.token:ro
      - ./local-sync.token:/opt/loreholm/local-sync.token:ro
      - ./local-api.token:/opt/loreholm/local-api.token:ro
      - ./databases.json:/opt/loreholm/databases.json
      - ./chat-bifrost-config.json:/opt/loreholm/chat-bifrost-config.json
      - ./dashboard-api-keys.json:/opt/loreholm/dashboard-api-keys.json
      - ./dashboard-credentials.json:/opt/loreholm/dashboard-credentials.json
      - ./dashboard-api-keys.json:/opt/loreholm/dashboard-api-keys.json
      - ./dashboard-preferences.json:/opt/loreholm/dashboard-preferences.json
      - ./chat.db:/opt/loreholm/chat.db
      - ./arcadedb-root.password:/opt/loreholm/arcadedb-root.password:ro
      # Persist the embedding-model cache across restarts and image
      # upgrades so first-start downloads (~300 MB Harrier or ~80 MB
      # MiniLM) are amortized over the life of the install.
      - loreholm-hf-cache:/root/.cache/huggingface
      - loreholm-st-cache:/root/.cache/torch

  # Tailnet-facing shim. Runs inside the tailscale container's netns
  # (the only thing on this host with a Tailnet IP) and:
  #   - serves /healthz and /local-dashboard.json locally, and
  #   - reverse-proxies every /api/sync/* request to loreholm-local-dashboard
  #     over the Docker bridge. The FastAPI dashboard verifies the sync
  #     bearer token against /opt/loreholm/local-sync.token on its side, so
  #     this shim just relays the Authorization header unmodified.
  local-dashboard-endpoint:
    image: python:3.12-alpine
    container_name: loreholm-local-dashboard-endpoint
    restart: unless-stopped
    network_mode: service:tailscale
    depends_on:
      tailscale:
        condition: service_healthy
      local-dashboard:
        condition: service_started
    command:
      - python
      - /opt/local-dashboard/endpoint_server.py
    environment:
      - LOCAL_DASHBOARD_META_FILE=/opt/local-dashboard/local-dashboard.json
      - LOCAL_SYNC_BIND_PORT=${LOCAL_DASHBOARD_ENDPOINT_PORT}
      - LOCAL_DASHBOARD_UPSTREAM=http://loreholm-local-dashboard:${LOCAL_ADMIN_PORT}
    volumes:
      - ./local-dashboard:/opt/local-dashboard:ro

volumes:
  tailscale_state:
    name: loreholm-tailscale-state
  arcadedb_data:
    name: loreholm-arcadedb-data
  arcadedb_log:
    name: loreholm-arcadedb-log
  loreholm-hf-cache:
    name: loreholm-hf-cache
  loreholm-st-cache:
    name: loreholm-st-cache
EOF

    success "Docker Compose file generated: $COMPOSE_FILE"
}

cleanup_existing_containers() {
    log "Checking for existing loreholm containers..."
    
    # Check if any loreholm containers are running and stop them
    local existing_containers
    existing_containers=$(docker ps -a --filter "name=loreholm-" --format "{{.Names}}" 2>/dev/null || echo "")
    
    if [[ -n "$existing_containers" ]]; then
        warn "Found existing loreholm containers, cleaning up..."
        
        # Stop and remove existing loreholm containers
        for container in $existing_containers; do
            log "Stopping $container..."
            docker stop "$container" &>/dev/null || true
            docker rm "$container" &>/dev/null || true
        done
        
        success "Cleaned up existing containers"
    fi
}

check_port_availability() {
    local port=$1
    local service_name=$2
    
    # Check if port is in use by a non-loreholm container or host process
    if docker ps --format "{{.Names}} {{.Ports}}" 2>/dev/null | grep -v "loreholm-" | grep -q ":${port}->"; then
        # Port is used by another Docker container
        local container_using_port
        container_using_port=$(docker ps --format "{{.Names}} {{.Ports}}" | grep ":${port}->" | grep -v "loreholm-" | awk '{print $1}' | head -1)
        warn "Port $port is already in use by container: $container_using_port"
        return 1
    fi
    
    # Check if port is in use by a host process (not Docker)
    if command -v ss &>/dev/null; then
        if ss -tuln 2>/dev/null | grep -q ":${port} "; then
            warn "Port $port is already in use by a host process"
            return 1
        fi
    elif command -v netstat &>/dev/null; then
        if netstat -tuln 2>/dev/null | grep -q ":${port} "; then
            warn "Port $port is already in use by a host process"
            return 1
        fi
    fi
    
    return 0
}

start_services() {
    log "Starting loreholm services..."
    
    # Clean up any existing loreholm containers first
    cleanup_existing_containers
    
    if ! check_port_availability "$LOCAL_ADMIN_PORT" "local-dashboard" 2>/dev/null; then
        error "Port ${LOCAL_ADMIN_PORT} is in use. Stop the conflicting service and rerun install."
    fi
    
    cd "$INSTALL_DIR"
    local compose_args=(-f "$COMPOSE_FILE")
    if [[ -f "$CHAT_COMPOSE_FILE" ]]; then
        log "Including optional chat compose overlay: $CHAT_COMPOSE_FILE"
        compose_args+=(-f "$CHAT_COMPOSE_FILE")
    fi
    if [[ "$COMPOSE_CMD" == "docker compose" ]]; then
        docker compose "${compose_args[@]}" pull
        docker compose "${compose_args[@]}" up -d --remove-orphans
    else
        docker-compose "${compose_args[@]}" pull
        docker-compose "${compose_args[@]}" up -d --remove-orphans
    fi
    
    success "Services started"
}

cleanup_images() {
    log "Cleaning up unused Docker images..."
    
    # Remove dangling images (untagged images that are not used by any container)
    if docker image prune -f &> /dev/null; then
        success "Cleaned up unused images"
    else
        warn "Could not clean up images (non-critical)"
    fi
}

wait_for_tailscale() {
    log "Waiting for Tailscale to connect..."
    
    local max_attempts=30
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        if docker exec loreholm-tailscale tailscale status &> /dev/null; then
            local ip
            ip=$(docker exec loreholm-tailscale tailscale ip -4 2>/dev/null || echo "")
            if [[ -n "$ip" ]]; then
                success "Tailscale connected! IP: $ip"
                return 0
            fi
        fi
        
        attempt=$((attempt + 1))
        sleep 2
    done
    
    warn "Tailscale connection timeout. It may still be connecting in the background."
    warn "Check status with: docker logs loreholm-tailscale"
}

show_status() {
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Installation Complete!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    
    # Get Tailscale IP if available
    local ts_ip
    ts_ip=$(docker exec loreholm-tailscale tailscale ip -4 2>/dev/null || echo "pending...")
    local credentials_set=false
    if [[ -f "$LOCAL_DASHBOARD_CREDENTIALS_FILE" ]] && [[ -s "$LOCAL_DASHBOARD_CREDENTIALS_FILE" ]]; then
        credentials_set=true
    fi

    local dashboard_token
    dashboard_token=""
    if [[ "$credentials_set" == "false" ]] && [[ -f "$LOCAL_DASHBOARD_TOKEN_FILE" ]]; then
        dashboard_token=$(tr -d '\r\n' < "$LOCAL_DASHBOARD_TOKEN_FILE" 2>/dev/null || true)
    fi

    echo -e "  ${BLUE}Node Name:${NC}     $NODE_NAME"
    echo -e "  ${BLUE}Tailscale IP:${NC}  $ts_ip"
    echo -e "  ${BLUE}LAN IP:${NC}        $LOCAL_LAN_IP"
    echo -e "  ${BLUE}Admin Access:${NC}  $LOCAL_ADMIN_ACCESS"
    echo -e "  ${BLUE}Local Admin:${NC}   http://${LOCAL_ADMIN_DISPLAY_HOST}:${LOCAL_ADMIN_PORT}"
    if [[ "$credentials_set" == "false" ]]; then
        echo -e "  ${BLUE}Local Token:${NC}   ${dashboard_token:-unavailable}"
    fi
    echo -e "  ${BLUE}Install Dir:${NC}   $INSTALL_DIR"
    echo ""
    echo -e "  ${YELLOW}Useful Commands:${NC}"
    echo "  └─ View logs:     docker logs loreholm-local-dashboard"
    echo "  └─ Check status:  docker exec loreholm-tailscale tailscale status"
    echo "  └─ Local admin:   http://${LOCAL_ADMIN_DISPLAY_HOST}:${LOCAL_ADMIN_PORT}"
    if [[ "$credentials_set" == "false" ]]; then
        echo "  └─ Token file:    $LOCAL_DASHBOARD_TOKEN_FILE"
        echo "  └─ Show token:    cat $LOCAL_DASHBOARD_TOKEN_FILE"
    fi
    echo "  └─ Resolver data: cat $LOCAL_DASHBOARD_FILE"
    echo "  └─ Stop services: cd $INSTALL_DIR && $COMPOSE_CMD down"
    echo "  └─ Restart:       cd $INSTALL_DIR && $COMPOSE_CMD restart"
    echo "  └─ Uninstall:     curl -fsSL loreholm.com/uninstall.sh | bash"
    echo ""
    echo -e "  ${YELLOW}API Key Management:${NC}"
    echo "  └─ List keys:     $INSTALL_DIR/manage-keys.sh list"
    echo "  └─ Create key:    $INSTALL_DIR/manage-keys.sh create \"My Device\""
    echo "  └─ Rotate key:    $INSTALL_DIR/manage-keys.sh rotate <key_id>"
    echo "  └─ Revoke key:    $INSTALL_DIR/manage-keys.sh revoke <key_id>"
    echo ""
    echo -e "  ${YELLOW}Next Steps:${NC}"
    if [[ "$LOCAL_ADMIN_ACCESS" == "localhost" ]]; then
        echo "  1. Open http://${LOCAL_ADMIN_DISPLAY_HOST}:${LOCAL_ADMIN_PORT} on this machine"
    else
        echo "  1. Open http://${LOCAL_ADMIN_DISPLAY_HOST}:${LOCAL_ADMIN_PORT} on your local network"
    fi
    echo "  2. Complete the local setup wizard to create your first database"
    echo "  3. Return to https://loreholm.com/dashboard to verify connection"
    echo "  4. Configure your LLM client to use the MCP tools"
    echo ""
}

uninstall() {
    warn "Uninstalling loreholm..."
    
    if [[ -f "$COMPOSE_FILE" ]]; then
        cd "$INSTALL_DIR"
        $COMPOSE_CMD down -v 2>/dev/null || true
    fi
    
    rm -rf "$INSTALL_DIR"
    success "loreholm uninstalled"
}

main() {
    show_banner
    parse_args "$@"
    check_dependencies
    detect_host_specs
    select_profile
    create_install_dir
    detect_local_lan_ip
    configure_local_dashboard_access
    generate_local_dashboard_token
    generate_local_sync_token
    generate_local_api_key
    generate_arcadedb_root_password
    initialize_database_registry
    initialize_bifrost_config
    initialize_dashboard_keys
    initialize_dashboard_credentials
    initialize_dashboard_api_keys
    initialize_dashboard_preferences
    initialize_chat_db
    write_manage_keys_script
    write_local_dashboard_metadata
    write_local_dashboard_endpoint_server
    generate_compose_file
    start_services
    write_local_dashboard_metadata
    cleanup_images
    wait_for_tailscale
    show_status
}

main "$@"
