#!/usr/bin/env bash
#
# loreholm BYODB uninstall script (Linux/macOS)
# Usage: curl -fsSL __APP_DOMAIN__/uninstall.sh | bash
#
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

INSTALL_DIR="${INSTALL_DIR:-$HOME/.loreholm}"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
CHAT_COMPOSE_FILE="$INSTALL_DIR/docker-compose.chat.yml"
ASSUME_YES="false"
KEEP_DATA="false"

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

usage() {
    cat << EOF
Usage: $0 [options]

Options:
  --dir <path>  Installation directory (default: $INSTALL_DIR)
  --keep-data   Remove containers only; keep the memory database, chat
                history, and $INSTALL_DIR so a reinstall re-adopts them
  --yes         Skip confirmation prompt
  -h, --help    Show help
EOF
    exit 0
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dir)
                if [[ $# -lt 2 ]]; then
                    error "Option --dir requires a value"
                fi
                INSTALL_DIR="$2"
                COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
                CHAT_COMPOSE_FILE="$INSTALL_DIR/docker-compose.chat.yml"
                shift 2
                ;;
            --keep-data)
                KEEP_DATA="true"
                shift
                ;;
            --yes)
                ASSUME_YES="true"
                shift
                ;;
            -h|--help)
                usage
                ;;
            *)
                error "Unknown option: $1"
                ;;
        esac
    done
}

confirm_uninstall() {
    if [[ "$ASSUME_YES" == "true" ]]; then
        return
    fi

    echo ""
    if [[ "$KEEP_DATA" == "true" ]]; then
        warn "This removes the loreholm containers but KEEPS your data:"
        warn "  memory database, chat history, and $INSTALL_DIR."
    else
        warn "This PERMANENTLY DELETES your loreholm data and cannot be undone:"
        warn "  the memory database and chat history (loreholm-* Docker volumes),"
        warn "  plus all containers and $INSTALL_DIR."
        warn "To keep your data, cancel and re-run with --keep-data (or back up the"
        warn "loreholm-* Docker volumes first)."
    fi
    local answer=""
    if [[ -r /dev/tty ]]; then
        printf "Continue uninstall? [y/N]: " > /dev/tty
        IFS= read -r answer < /dev/tty || true
    elif [[ -t 0 ]]; then
        read -r -p "Continue uninstall? [y/N]: " answer || true
    else
        warn "No interactive terminal detected; uninstall canceled."
        warn "Run with --yes to uninstall non-interactively."
        exit 0
    fi

    case "${answer,,}" in
        y|yes) ;;
        *)
            log "Uninstall canceled."
            exit 0
            ;;
    esac
}

detect_compose_cmd() {
    if docker compose version &> /dev/null; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        COMPOSE_CMD=""
    fi
}

stop_stack_if_present() {
    if [[ ! -f "$COMPOSE_FILE" ]]; then
        return
    fi
    if [[ -z "$COMPOSE_CMD" ]]; then
        warn "Docker Compose not found; skipping compose-managed teardown."
        return
    fi

    log "Stopping loreholm compose services..."
    cd "$INSTALL_DIR"
    local compose_args=(-f "$COMPOSE_FILE")
    if [[ -f "$CHAT_COMPOSE_FILE" ]]; then
        compose_args+=(-f "$CHAT_COMPOSE_FILE")
    fi

    # -v removes named volumes (the memory data); omit it under --keep-data.
    local down_args=(down --remove-orphans)
    if [[ "$KEEP_DATA" != "true" ]]; then
        down_args+=(-v)
    fi

    if [[ "$COMPOSE_CMD" == "docker compose" ]]; then
        docker compose "${compose_args[@]}" "${down_args[@]}" || true
    else
        docker-compose "${compose_args[@]}" "${down_args[@]}" || true
    fi
}

remove_loreholm_containers() {
    log "Removing any remaining loreholm-* containers..."
    local ids
    ids=$(docker ps -a --filter "name=^/loreholm-" --format "{{.ID}}" 2>/dev/null || true)
    if [[ -n "$ids" ]]; then
        # shellcheck disable=SC2086
        docker rm -f $ids >/dev/null 2>&1 || true
    fi
}

remove_loreholm_volumes() {
    log "Removing loreholm-* Docker volumes..."
    local volumes
    volumes=$(docker volume ls --format "{{.Name}}" 2>/dev/null | grep '^loreholm-' || true)
    if [[ -n "$volumes" ]]; then
        # shellcheck disable=SC2086
        docker volume rm $volumes >/dev/null 2>&1 || true
    fi
}

remove_install_dir() {
    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        success "Removed install directory: $INSTALL_DIR"
    else
        warn "Install directory not found: $INSTALL_DIR"
    fi
}

main() {
    parse_args "$@"
    confirm_uninstall

    if ! command -v docker &> /dev/null; then
        if [[ "$KEEP_DATA" == "true" ]]; then
            warn "Docker is not installed and --keep-data was set; nothing to do."
            exit 0
        fi
        warn "Docker is not installed; removing local files only."
        remove_install_dir
        success "Uninstall complete."
        exit 0
    fi

    detect_compose_cmd
    stop_stack_if_present
    remove_loreholm_containers
    if [[ "$KEEP_DATA" == "true" ]]; then
        success "loreholm containers removed; data kept at $INSTALL_DIR."
        exit 0
    fi
    remove_loreholm_volumes
    remove_install_dir
    success "loreholm uninstall complete."
}

main "$@"
