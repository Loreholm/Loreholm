#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_URL="${LOREHOLM_SOURCE_URL:-https://github.com/Loreholm/Loreholm/archive/refs/heads/v2.tar.gz}"
INSTALL_ROOT="${LOREHOLM_HOME:-$HOME/.local/share/loreholm-v2}"
SOURCE_DIR="$INSTALL_ROOT/source"
STATE_DIR="$INSTALL_ROOT/state"
ENV_FILE="$STATE_DIR/instance.env"
PORT="${LOREHOLM_V2_PORT:-8082}"
BIND_HOST="${LOREHOLM_V2_BIND_HOST:-127.0.0.1}"
HEALTH_HOST="$BIND_HOST"
[[ "$HEALTH_HOST" == "0.0.0.0" ]] && HEALTH_HOST="127.0.0.1"
BIFROST_BIND_HOST="${BIFROST_BIND_HOST:-127.0.0.1}"
BIFROST_PORT="${BIFROST_PORT:-8083}"
BIFROST_PUBLIC_URL="${BIFROST_PUBLIC_URL:-http://$BIFROST_BIND_HOST:$BIFROST_PORT}"

say() { printf 'Loreholm: %s\n' "$*"; }
die() { printf 'Loreholm: error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "'$1' is required"; }

need curl
need docker
need tar
need sha256sum
need openssl
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
docker info >/dev/null 2>&1 || die "Docker is not available to this user (try adding the user to the docker group)"

if [[ -e "$ENV_FILE" ]]; then
  say "preserving existing instance credentials in $ENV_FILE"
else
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR"
  arcade_password="$(openssl rand -base64 36 | tr -d '/+=' | head -c 40)"
  device_token="$(openssl rand -hex 32)"
  device_digest="$(printf '%s' "$device_token" | sha256sum | cut -d' ' -f1)"
  admin_token="$(openssl rand -hex 32)"
  admin_digest="$(printf '%s' "$admin_token" | sha256sum | cut -d' ' -f1)"
  sync_token="${LOREHOLM_V2_SYNC_TOKEN:-$(openssl rand -hex 32)}"
  sync_digest="$(printf '%s' "$sync_token" | sha256sum | cut -d' ' -f1)"
  bifrost_admin_password="Lh!$(openssl rand -hex 24)"
  previous_umask="$(umask)"
  umask 077
  {
    printf 'ARCADEDB_ROOT_PASSWORD=%s\n' "$arcade_password"
    printf 'LOREHOLM_V2_DEVICE_TOKEN_SHA256=%s\n' "$device_digest"
    printf 'LOREHOLM_V2_DEVICE_TOKEN=%s\n' "$device_token"
    printf 'LOREHOLM_V2_ADMIN_TOKEN_SHA256=%s\n' "$admin_digest"
    printf 'LOREHOLM_V2_ADMIN_TOKEN=%s\n' "$admin_token"
    printf 'LOREHOLM_V2_SYNC_TOKEN_SHA256=%s\n' "$sync_digest"
    printf 'LOREHOLM_V2_SYNC_TOKEN=%s\n' "$sync_token"
    printf 'LOREHOLM_V2_PORT=%s\n' "$PORT"
    printf 'LOREHOLM_V2_BIND_HOST=%s\n' "$BIND_HOST"
    printf 'BIFROST_BIND_HOST=%s\n' "$BIFROST_BIND_HOST"
    printf 'BIFROST_PORT=%s\n' "$BIFROST_PORT"
    printf 'BIFROST_PUBLIC_URL=%s\n' "$BIFROST_PUBLIC_URL"
    printf 'BIFROST_ADMIN_USERNAME=%s\n' "loreholm"
    printf 'BIFROST_ADMIN_PASSWORD=%s\n' "$bifrost_admin_password"
  } > "$ENV_FILE"
  umask "$previous_umask"
fi

if ! grep -q '^LOREHOLM_V2_SYNC_TOKEN=' "$ENV_FILE"; then
  sync_token="${LOREHOLM_V2_SYNC_TOKEN:-$(openssl rand -hex 32)}"
  sync_digest="$(printf '%s' "$sync_token" | sha256sum | cut -d' ' -f1)"
  previous_umask="$(umask)"
  umask 077
  printf 'LOREHOLM_V2_SYNC_TOKEN_SHA256=%s\nLOREHOLM_V2_SYNC_TOKEN=%s\n' \
    "$sync_digest" "$sync_token" >> "$ENV_FILE"
  umask "$previous_umask"
fi

if ! grep -q '^BIFROST_ADMIN_PASSWORD=' "$ENV_FILE"; then
  bifrost_admin_password="Lh!$(openssl rand -hex 24)"
  previous_umask="$(umask)"
  umask 077
  printf 'BIFROST_ADMIN_USERNAME=%s\nBIFROST_ADMIN_PASSWORD=%s\n' \
    "loreholm" "$bifrost_admin_password" >> "$ENV_FILE"
  umask "$previous_umask"
fi

# Upgrade older installs that predate the dashboard credential without
# replacing their existing database or device credentials.
if ! grep -q '^LOREHOLM_V2_ADMIN_TOKEN=' "$ENV_FILE"; then
  admin_token="$(openssl rand -hex 32)"
  admin_digest="$(printf '%s' "$admin_token" | sha256sum | cut -d' ' -f1)"
  previous_umask="$(umask)"
  umask 077
  printf 'LOREHOLM_V2_ADMIN_TOKEN_SHA256=%s\nLOREHOLM_V2_ADMIN_TOKEN=%s\n' "$admin_digest" "$admin_token" >> "$ENV_FILE"
  umask "$previous_umask"
fi

if ! grep -q '^BIFROST_PUBLIC_URL=' "$ENV_FILE"; then
  previous_umask="$(umask)"
  umask 077
  printf 'BIFROST_BIND_HOST=%s\nBIFROST_PORT=%s\nBIFROST_PUBLIC_URL=%s\n' \
    "$BIFROST_BIND_HOST" "$BIFROST_PORT" "$BIFROST_PUBLIC_URL" >> "$ENV_FILE"
  umask "$previous_umask"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
say "fetching release"
curl --fail --location --silent --show-error "$SOURCE_URL" -o "$tmp/loreholm.tar.gz"
mkdir -p "$tmp/unpacked"
tar -xzf "$tmp/loreholm.tar.gz" -C "$tmp/unpacked"
compose_file="$(find "$tmp/unpacked" -type f -path '*/deploy/docker-compose.v2.yml' -print -quit)"
[[ -n "$compose_file" ]] || die "release does not contain the Loreholm stack"
source_root="${compose_file%/deploy/docker-compose.v2.yml}"

rm -rf "$SOURCE_DIR.next"
mv "$source_root" "$SOURCE_DIR.next"
if [[ -d "$SOURCE_DIR" ]]; then mv "$SOURCE_DIR" "$SOURCE_DIR.previous"; fi
mv "$SOURCE_DIR.next" "$SOURCE_DIR"
rm -rf "$SOURCE_DIR.previous"

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

say "starting Bifrost on loopback for authentication bootstrap"
BIFROST_BIND_HOST=127.0.0.1 docker compose --env-file "$ENV_FILE" \
  -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" up -d bifrost bifrost-dashboard
for _ in $(seq 1 60); do
  curl --fail --silent "http://127.0.0.1:$BIFROST_PORT/health" >/dev/null && break
  sleep 1
done
curl --fail --silent --show-error -X PUT "http://127.0.0.1:$BIFROST_PORT/api/config" \
  --user "$BIFROST_ADMIN_USERNAME:$BIFROST_ADMIN_PASSWORD" \
  -H 'Content-Type: application/json' \
  --data "{\"auth_config\":{\"is_enabled\":true,\"admin_username\":\"$BIFROST_ADMIN_USERNAME\",\"admin_password\":\"$BIFROST_ADMIN_PASSWORD\"},\"client_config\":{\"log_retention_days\":30,\"enforce_auth_on_inference\":false}}" \
  >/dev/null

say "building and starting the authenticated private instance"
docker compose --env-file "$ENV_FILE" -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" up -d --build

say "waiting for the API"
for _ in $(seq 1 60); do
  if curl --fail --silent "http://$HEALTH_HOST:$PORT/health" > "$tmp/health.json"; then
    say "ready at http://$HEALTH_HOST:$PORT"
    say "device token is stored in $ENV_FILE (mode 0600)"
    say "dashboard ready at http://$HEALTH_HOST:$PORT/dashboard"
    say "Bifrost dashboard ready at $BIFROST_PUBLIC_URL (username: $BIFROST_ADMIN_USERNAME)"
    say "Bifrost password is stored in $ENV_FILE (mode 0600)"
    cat "$tmp/health.json"
    printf '\n'
    exit 0
  fi
  sleep 2
done

docker compose --env-file "$ENV_FILE" -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" ps >&2 || true
docker compose --env-file "$ENV_FILE" -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" logs --tail=80 instance arcadedb >&2 || true
die "the API did not become healthy within 120 seconds"
