#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="${LOREHOLM_HOME:-$HOME/.local/share/loreholm-v2}"
SOURCE_DIR="$INSTALL_ROOT/source"
ENV_FILE="$INSTALL_ROOT/state/instance.env"

if [[ -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" && -f "$ENV_FILE" ]]; then
  docker compose --env-file "$ENV_FILE" -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" down
fi

printf 'Loreholm V2 containers stopped. Data and credentials remain in %s\n' "$INSTALL_ROOT"
printf 'To permanently erase them, rerun with LOREHOLM_ERASE_DATA=1.\n'
if [[ "${LOREHOLM_ERASE_DATA:-0}" == "1" ]]; then
  if [[ -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" && -f "$ENV_FILE" ]]; then
    docker compose --env-file "$ENV_FILE" -f "$SOURCE_DIR/deploy/docker-compose.v2.yml" down --volumes
  fi
  rm -rf "$INSTALL_ROOT"
  printf 'Loreholm V2 data and credentials erased.\n'
fi
