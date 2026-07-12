#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_URL="${LOREHOLM_SOURCE_URL:-https://github.com/Loreholm/Loreholm/archive/refs/heads/v2.tar.gz}"
INSTALL_ROOT="${LOREHOLM_HOME:-$HOME/.local/share/loreholm-v2}"
SOURCE_DIR="$INSTALL_ROOT/source"
STATE_DIR="$INSTALL_ROOT/state"
ENV_FILE="$STATE_DIR/instance.env"
PORT="${LOREHOLM_V2_PORT:-8082}"

say() { printf 'Loreholm V2: %s\n' "$*"; }
die() { printf 'Loreholm V2: error: %s\n' "$*" >&2; exit 1; }
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
  previous_umask="$(umask)"
  umask 077
  {
    printf 'ARCADEDB_ROOT_PASSWORD=%s\n' "$arcade_password"
    printf 'LOREHOLM_V2_DEVICE_TOKEN_SHA256=%s\n' "$device_digest"
    printf 'LOREHOLM_V2_DEVICE_TOKEN=%s\n' "$device_token"
    printf 'LOREHOLM_V2_ADMIN_TOKEN_SHA256=%s\n' "$admin_digest"
    printf 'LOREHOLM_V2_ADMIN_TOKEN=%s\n' "$admin_token"
    printf 'LOREHOLM_V2_PORT=%s\n' "$PORT"
  } > "$ENV_FILE"
  umask "$previous_umask"
fi

# Upgrade older V2 installs that predate the dashboard credential without
# replacing their existing database or device credentials.
if ! grep -q '^LOREHOLM_V2_ADMIN_TOKEN=' "$ENV_FILE"; then
  admin_token="$(openssl rand -hex 32)"
  admin_digest="$(printf '%s' "$admin_token" | sha256sum | cut -d' ' -f1)"
  previous_umask="$(umask)"
  umask 077
  printf 'LOREHOLM_V2_ADMIN_TOKEN_SHA256=%s\nLOREHOLM_V2_ADMIN_TOKEN=%s\n' "$admin_digest" "$admin_token" >> "$ENV_FILE"
  umask "$previous_umask"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
say "fetching release"
curl --fail --location --silent --show-error "$SOURCE_URL" -o "$tmp/loreholm.tar.gz"
mkdir -p "$tmp/unpacked"
tar -xzf "$tmp/loreholm.tar.gz" -C "$tmp/unpacked"
compose_file="$(find "$tmp/unpacked" -type f -path '*/deploy/docker-compose.v2.yml' -print -quit)"
[[ -n "$compose_file" ]] || die "release does not contain the V2 stack"
source_root="${compose_file%/deploy/docker-compose.v2.yml}"

rm -rf "$SOURCE_DIR.next"
mv "$source_root" "$SOURCE_DIR.next"
if [[ -d "$SOURCE_DIR" ]]; then mv "$SOURCE_DIR" "$SOURCE_DIR.previous"; fi
mv "$SOURCE_DIR.next" "$SOURCE_DIR"
rm -rf "$SOURCE_DIR.previous"

say "building and starting the private instance"
docker compose --env-file "$ENV_FILE" -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" up -d --build

say "waiting for the API"
for _ in $(seq 1 60); do
  if curl --fail --silent "http://127.0.0.1:$PORT/health" > "$tmp/health.json"; then
    say "ready at http://127.0.0.1:$PORT"
    say "device token is stored in $ENV_FILE (mode 0600)"
    say "dashboard ready at http://127.0.0.1:$PORT/dashboard"
    cat "$tmp/health.json"
    printf '\n'
    exit 0
  fi
  sleep 2
done

docker compose --env-file "$ENV_FILE" -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" ps >&2 || true
docker compose --env-file "$ENV_FILE" -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" logs --tail=80 instance arcadedb >&2 || true
die "the API did not become healthy within 120 seconds"
