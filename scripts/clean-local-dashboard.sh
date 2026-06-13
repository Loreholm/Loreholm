#!/usr/bin/env bash
#
# Reset the local dashboard dev loop to a fresh slate.
#
# Removes:
#   - the dev compose stack and its named volumes (arcadedb data/log)
#   - .dev-state/ (tokens, credentials, registry, bifrost config)
#   - any loreholm-arcadedb-* containers and volumes the dashboard/wizard may
#     have spun up during iteration
#
# Re-running scripts/dev-local-dashboard.sh after this gives you a pristine
# environment with the `dev` database pre-registered again.
#
# Usage:
#   scripts/clean-local-dashboard.sh          # prompts for confirmation
#   scripts/clean-local-dashboard.sh --yes    # skip the prompt

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_STATE="$REPO_ROOT/.dev-state"
COMPOSE_FILE="$REPO_ROOT/deploy/docker-compose.dev.yml"

blue()  { printf "\033[0;34m[clean-dev]\033[0m %s\n" "$1"; }
green() { printf "\033[0;32m[\xE2\x9C\x93]\033[0m %s\n" "$1"; }
warn()  { printf "\033[0;33m[!]\033[0m %s\n" "$1"; }

ASSUME_YES="no"
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES="yes" ;;
    -h|--help)
      cat <<'EOF'
Reset the local dashboard dev loop to a fresh slate.

Removes:
  - the dev compose stack and its named volumes (arcadedb data/log)
  - .dev-state/ (tokens, credentials, registry, bifrost config)
  - any loreholm-arcadedb-* containers/volumes (wizard-created test DBs)

Usage:
  scripts/clean-local-dashboard.sh          # prompts for confirmation
  scripts/clean-local-dashboard.sh --yes    # skip the prompt
EOF
      exit 0
      ;;
    *)
      warn "Unknown flag: $arg"
      exit 1
      ;;
  esac
done

# ---------- confirm ----------
if [[ "$ASSUME_YES" != "yes" ]]; then
  printf "\n"
  warn "This will delete:"
  warn "  - dev containers + volumes from $COMPOSE_FILE"
  warn "  - any loreholm-arcadedb-* containers/volumes (wizard-created test DBs)"
  warn "  - $DEV_STATE (tokens, credentials, registry, bifrost config)"
  printf "\n"
  read -r -p "Proceed? [y/N] " answer
  case "${answer,,}" in
    y|yes) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
fi

# ---------- dev compose stack ----------
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  if [[ -f "$COMPOSE_FILE" ]]; then
    blue "Tearing down dev compose stack..."
    docker compose -f "$COMPOSE_FILE" down --volumes --remove-orphans || true
    green "Dev compose stack removed"
  fi

  # ---------- extra wizard-created arcadedb containers ----------
  # Anything matching loreholm-arcadedb-* that wasn't owned by compose above —
  # these come from dashboard create-database flows during iteration.
  extra_containers=$(
    docker ps -a --filter "name=^loreholm-arcadedb-" --format "{{.Names}}" 2>/dev/null || true
  )
  if [[ -n "$extra_containers" ]]; then
    blue "Removing leftover arcadedb containers:"
    while IFS= read -r name; do
      [[ -z "$name" ]] && continue
      printf "  - %s\n" "$name"
      docker rm -f "$name" >/dev/null 2>&1 || true
    done <<< "$extra_containers"
  fi

  # Matching named volumes (dashboard names them <container>-data / -log).
  extra_volumes=$(
    docker volume ls --filter "name=^loreholm-arcadedb-" --format "{{.Name}}" 2>/dev/null || true
  )
  if [[ -n "$extra_volumes" ]]; then
    blue "Removing leftover arcadedb volumes:"
    while IFS= read -r vol; do
      [[ -z "$vol" ]] && continue
      printf "  - %s\n" "$vol"
      docker volume rm "$vol" >/dev/null 2>&1 || true
    done <<< "$extra_volumes"
  fi
else
  warn "docker / docker compose not available — skipping container cleanup."
fi

# ---------- .dev-state/ ----------
if [[ -d "$DEV_STATE" ]]; then
  blue "Removing $DEV_STATE ..."
  rm -rf "$DEV_STATE"
  green ".dev-state/ removed"
fi

printf "\n"
green "Clean slate. Run scripts/dev-local-dashboard.sh to rebuild."
