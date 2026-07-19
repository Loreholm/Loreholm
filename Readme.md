# Loreholm

Loreholm is a self-hosted capture and knowledge-mining framework for assistant
conversations. Loreholm owns the client contract, raw capture store, policy, model
gateway, and knowledge graph so third-party clients never write interpreted
facts directly into the database.

Its purpose is larger than better retrieval. LLM applications can send far
more context than the words visible in a chat box, while the durable memory
built from that context is usually controlled by the application or provider.
Loreholm puts a user-owned storage and policy layer in front of model providers
so people can inspect what is captured, choose what may leave, and keep their
working history on infrastructure they control. Read
[why Loreholm exists](docs/00_Principles.md) for the moral commitments and the
limits that self-hosting cannot erase.

## Current milestone

The executable Loreholm foundation currently provides:

- versioned capture ingestion for transcript events and explicit pushes;
- UUIDv7 capture identity and idempotent retry handling;
- snapshot content-hash validation;
- device-time normalization with the original timestamp retained;
- durable ArcadeDB staging and quarantine of unknown capture classes;
- server-side rejection of capture classes disabled by instance policy;
- durable transcript-session assembly and leased admission work;
- deterministic mechanical trimming and durable salience decisions;
- reusable mining-run identity and incremental-input lineage;
- authenticated client policy sync; and
- a self-contained ArcadeDB, instance API, and Bifrost deployment.

Model-assisted interpretation and extraction, graph commit, query/surfacing,
retention UI, sharing, backup, and client adapters are designed but are not
implemented in this milestone.

## Architecture

```text
assistant adapter
      |
      | raw context captures
      v
embedded spine  ---> offline queue / local permission enforcement
      |
      v
instance API  ---> ArcadeDB staging ---> admission queue ---> salience gate
      |                                      |
      |                                      v
      |                               interpreter (planned)
      |                                      |
      |                                      v
      |                               knowledge graph
      |
      +-------> Bifrost ---> user-configured model endpoints
```

The adapter is a sensor, not an authority. Interpretation happens inside the
instance. Bifrost is the only permitted model-egress path.

Loreholm's remote trust boundary lets browsers and remote clients enter
through the public front door, which authenticates them and reaches their
containerized instance over a Headscale-managed Tailscale tunnel. ArcadeDB,
Bifrost, and durable context remain local and are never exposed directly on
the Tailnet. You can run only your private instance or operate the public
server plane too; see [self-hosting](docs/11_SelfHosting.md) and
[Loreholm networking](docs/02_Networking.md).

Organizations can run a bounded pilot on their own infrastructure and may
operate the complete front door. The current foundation is not yet a finished
mission-critical knowledge platform; see
[using Loreholm in an organization](docs/12_Organizations.md).

## Requirements

- Linux with Docker Engine
- Docker Compose v2
- `curl`, `tar`, `openssl`, and `sha256sum`
- Host disk or volume encryption for data-at-rest protection

The API binds to loopback by default. Use an authenticated TLS proxy or private
overlay network before connecting a client from another machine.

## Install from source

From a checkout:

```bash
git switch v2
LOREHOLM_SOURCE_URL=https://github.com/Loreholm/Loreholm/archive/refs/heads/v2.tar.gz \
  bash web/install-v2.sh
```

The installer:

- generates ArcadeDB and device credentials;
- stores them with mode `0600` under
  `~/.local/share/loreholm-v2/state/instance.env`;
- installs release source under `~/.local/share/loreholm-v2/source`;
- starts the private instance; and
- waits for the API to become healthy.

Re-running the installer upgrades the source while preserving credentials and
database volumes.

## Test the platform

### 1. Check container and API health

```bash
export LOREHOLM_HOME=${LOREHOLM_HOME:-$HOME/.local/share/loreholm-v2}
ENV_FILE="$LOREHOLM_HOME/state/instance.env"
COMPOSE_FILE="$LOREHOLM_HOME/source/deploy/docker-compose.v2.yml"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
. "$ENV_FILE"
curl -fsS "http://127.0.0.1:${LOREHOLM_V2_PORT:-8082}/health"
```

Expected response:

```json
{"ok":true,"version":"1.0.0","storage":"arcadedb"}
```

### 2. Read the client policy

```bash
curl -fsS \
  -H "Authorization: Bearer $LOREHOLM_V2_DEVICE_TOKEN" \
  "http://127.0.0.1:${LOREHOLM_V2_PORT:-8082}/v2/policy"
```

The response should advertise contract `2.0`, supported capture classes, their
processing modes, and the current mining status.

### 3. Ingest a transcript event

Use a fresh UUIDv7 for a new test. The fixed ID below is useful for explicitly
testing retry behavior:

```bash
payload='{
  "captures": [{
    "capture_id": "018f5e2a-1234-7abc-8def-1234567890ab",
    "kind": "event",
    "class": "transcript.message",
    "surface": "manual-smoke-test",
    "session_ref": "readme-test",
    "occurred_at": "2026-07-11T00:00:00Z",
    "payload": {"role": "user", "content": "Remember this test capture."},
    "refs": [],
    "hints": [],
    "meta": {
      "contract_version": "2.0",
      "spine_version": "2.0.0",
      "adapter_id": "manual",
      "adapter_version": "2.0.0",
      "device_id": "readme-device",
      "user_id": "local-user",
      "queue_age_seconds": 0,
      "policy_version": 1
    }
  }]
}'

curl -fsS \
  -H "Authorization: Bearer $LOREHOLM_V2_DEVICE_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$payload" \
  "http://127.0.0.1:${LOREHOLM_V2_PORT:-8082}/v2/captures"
```

The first submission returns `accepted`. Submit the same payload again and it
must return `duplicate`; this verifies durable transport idempotency.

If this ID was already used, both calls will return `duplicate`. Change it to a
new valid UUIDv7 to repeat the first-ingest test.

### 4. Verify unknown-class quarantine

Change `"class": "transcript.message"` in the payload to
`"class": "future.test"` and use another UUIDv7. The receipt must return
`quarantined`, proving a newer adapter cannot lose data while an older instance
also cannot mine an unsupported payload.

### 5. Run automated tests

```bash
python3 -m venv /tmp/loreholm-v2-tests
/tmp/loreholm-v2-tests/bin/pip install \
  -r api/requirements.txt -r api/requirements-dev.txt
/tmp/loreholm-v2-tests/bin/python -m pytest api/tests/test_v2_capture.py -q
```

Expected result: `4 passed`.

## Troubleshooting

Show current state and recent logs:

```bash
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" \
  logs --tail=100 instance arcadedb bifrost
```

The most common installation failure is Docker access for the current user.
`docker info` must work without `sudo` before running the installer.

## Stop or uninstall

Stop containers while preserving credentials and database volumes:

```bash
bash "$LOREHOLM_HOME/source/web/uninstall-v2.sh"
```

Permanently remove the Loreholm volumes, credentials, and installed source:

```bash
LOREHOLM_ERASE_DATA=1 bash "$LOREHOLM_HOME/source/web/uninstall-v2.sh"
```

The second command is destructive and cannot be undone without a backup.

## Documentation

- [Documentation index](docs/README.md)
- [Loreholm architecture](docs/01_Architecture.md)
- [Loreholm networking](docs/02_Networking.md)
- [Capture API](docs/03_CaptureAPI.md)
- [Instance operations](docs/04_InstanceOperations.md)
- [Policy and models](docs/05_PolicyAndModels.md)
- [Clients and embedded spine](docs/06_ClientsAndSpine.md)
- [Mining and knowledge model](docs/07_MiningAndKnowledge.md)
- [Data lifecycle](docs/08_DataLifecycle.md)
- [Loreholm browser chat](docs/09_Chat.md)
- [Loreholm development stack](docs/10_Development.md)
- [Loreholm trust and security model](docs/13_SecurityModel.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## License

Server-side code is licensed under [AGPL-3.0](LICENSE). Client-facing web and
installer code under `web/` is licensed under [MIT](web/LICENSE). These licenses
govern code, never user data.
