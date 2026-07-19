# V2 instance installation and operations

**Status:** The base local stack and installer are implemented. The retained
front-door/Tailscale deployment is implemented for browser chat and documented
separately in [V2 networking](02_Networking.md).

## Requirements

- Linux with Docker Engine
- Docker Compose v2
- `curl`, `tar`, `openssl`, and `sha256sum`
- Docker access for the current user without `sudo`
- Host disk or volume encryption when data-at-rest protection is required

## Install

From a V2 checkout:

```bash
LOREHOLM_SOURCE_URL=https://github.com/Loreholm/Loreholm/archive/refs/heads/v2.tar.gz \
  bash web/install-v2.sh
```

The installer downloads the V2 source, generates credentials, bootstraps
Bifrost authentication, builds the instance image, starts the stack, and waits
up to 120 seconds for instance health.

## Installation layout

The default root is `~/.local/share/loreholm-v2`:

```text
~/.local/share/loreholm-v2/
  |- source/                 installed release source and Compose files
  `- state/
       `- instance.env      credentials and local configuration (mode 0600)
```

The state directory is mode `0700`. Re-running the installer replaces the
installed source while preserving `instance.env` and Docker volumes.

## Containers and volumes

| Service | Purpose | Host exposure |
|---|---|---|
| `instance` | V2 API, dashboard, capture service, and chat path | `127.0.0.1:8082` by default |
| `arcadedb` | One ArcadeDB database owned by this instance | None |
| `bifrost` | Mandatory model gateway | None |
| `bifrost-dashboard` | Authenticated Bifrost management proxy | `127.0.0.1:8083` by default |

`arcadedb_data` contains database state. `bifrost_data` contains Bifrost state.
Neither ArcadeDB nor Bifrost inference is published directly.

## Configuration

Set installer overrides before the first run:

| Variable | Default | Purpose |
|---|---|---|
| `LOREHOLM_HOME` | `~/.local/share/loreholm-v2` | Installation root |
| `LOREHOLM_SOURCE_URL` | V2 branch archive | Release source archive |
| `LOREHOLM_V2_BIND_HOST` | `127.0.0.1` | Instance API bind address |
| `LOREHOLM_V2_PORT` | `8082` | Instance API host port |
| `BIFROST_BIND_HOST` | `127.0.0.1` | Bifrost dashboard bind address |
| `BIFROST_PORT` | `8083` | Bifrost dashboard host port |
| `BIFROST_PUBLIC_URL` | Derived from bind host/port | Link shown by the instance dashboard |
| `LOREHOLM_V2_SYNC_TOKEN` | Random | Prearranged front-door-to-instance credential |

The generated environment also contains ArcadeDB and Bifrost credentials plus
raw and hashed device, administrator, and synchronization tokens. Avoid
editing only one side of a raw-token/digest pair.

## Health and status

```bash
export LOREHOLM_HOME=${LOREHOLM_HOME:-$HOME/.local/share/loreholm-v2}
ENV_FILE="$LOREHOLM_HOME/state/instance.env"
COMPOSE_FILE="$LOREHOLM_HOME/source/deploy/docker-compose.v2.yml"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
curl -fsS "http://127.0.0.1:${LOREHOLM_V2_PORT:-8082}/health"
```

Expected health shape:

```json
{"ok":true,"version":"2.0.0","storage":"arcadedb"}
```

Recent logs:

```bash
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" \
  logs --tail=100 instance arcadedb bifrost
```

## Dashboard

Open `http://127.0.0.1:8082/dashboard` and unlock it with
`LOREHOLM_V2_ADMIN_TOKEN` from `instance.env`. The browser retains that token
in session storage only.

The current dashboard can:

- show instance, Bifrost, and configured model status;
- edit capture-class and remote-processing policy;
- set the advertised mining status; and
- configure an OpenAI-compatible model endpoint through Bifrost.

It does not yet provide capture inventory, deletion, mining runs, graph
inspection, backup, or restore.

## Updating

Re-run `web/install-v2.sh` with the same `LOREHOLM_HOME`. It preserves the
existing credential file and named Docker volumes while replacing installed
source and recreating services as needed.

There is not yet a release migration coordinator for future mined schemas.
Before relying on this upgrade path across schema-bearing milestones, add and
test explicit database migration and backup behavior.

## Stop or erase

Stop containers while retaining credentials and volumes:

```bash
bash "$LOREHOLM_HOME/source/web/uninstall-v2.sh"
```

Permanently remove the base stack's volumes, credentials, and installed source:

```bash
LOREHOLM_ERASE_DATA=1 bash "$LOREHOLM_HOME/source/web/uninstall-v2.sh"
```

The erase form is destructive. The current uninstall script operates on the
base Compose file; deployments using additional overlays should stop their
overlay services explicitly as part of their operational runbook.

## Backup status

V2 has an accepted design for secret-free, instance-consistent backups, but no
backup coordinator or supported restore command yet. Copying a live ArcadeDB
volume is not documented as a consistent backup. See [data lifecycle](08_DataLifecycle.md).

## Troubleshooting

### Docker is unavailable

`docker info` must succeed for the current user. Fix Docker group or daemon
access before running the installer.

### Instance remains unhealthy

Check `arcadedb` health first, then instance logs. The API reports healthy only
when its ArcadeDB-backed capture service was configured at startup.

### Bifrost dashboard is unavailable

Check the `bifrost` and `bifrost-dashboard` services and verify port `8083` is
not already in use. The proxy blocks `/v1/*` intentionally.

### Remote chat cannot reach the instance

Check the Tailscale sidecar, endpoint shim, Headscale node/ACL state, port
`8081`, and synchronization token alignment. Do not fix tunnel failures by
publishing ArcadeDB or Bifrost.
