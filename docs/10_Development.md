# Loreholm development stack

The current executable slice implements contract-v2 capture ingestion, policy sync,
server-side capture blocking, idempotent staging, clock normalization,
unknown-class quarantine, session assembly, and the durable admission queue.

The installation path uses the installer; it checks prerequisites, generates instance
and device credentials, installs under `~/.local/share/loreholm-v2`, starts the
stack, waits for health, and prints actionable logs if startup fails:

```bash
curl -fsSL https://your-loreholm-host/install-v2.sh | bash
```

For source development, create a `.env` with a strong ArcadeDB password and
the SHA-256 digest of the device bearer token:

```dotenv
ARCADEDB_ROOT_PASSWORD=replace-me
LOREHOLM_V2_DEVICE_TOKEN_SHA256=replace-with-64-hex-character-digest
```

Then run:

```bash
docker compose --env-file deploy/.env.v2 -f deploy/docker-compose.v2.yml up -d --build
curl http://127.0.0.1:8082/health
```

The base development API is intentionally bound to loopback. Loreholm's retained
remote architecture uses the public front door and Headscale/Tailscale tunnel
described in [Loreholm networking](02_Networking.md); do not publish the instance
directly as a substitute. Bifrost is present as the sole model-egress boundary,
but endpoint configuration remains instance-owned and is not baked into the
repository. Model-assisted mining is not enabled in this foundation milestone.
The scheduled deterministic worker consumes admission work into salience
records without calling a model. The mining-run coordinator and durable output
store are available for future stages, but no background process invokes a
model-backed interpreter. The transcript quiet period defaults to 300 seconds;
`LOREHOLM_V2_SESSION_QUIESCENCE_SECONDS` in the Compose environment overrides
it and is passed into the API container.

For a trusted LAN, set `LOREHOLM_V2_BIND_HOST` to the host's LAN address in the
instance environment and recreate the `instance` service. Authentication still
applies to policy, capture, and dashboard administration. Avoid `0.0.0.0` when
the machine also has untrusted network interfaces.

The Bifrost management dashboard is published separately on port `8083` and
defaults to loopback. The installer enables Bifrost's built-in authentication;
its generated username and password are stored in the mode-0600 instance env
file. Set `BIFROST_BIND_HOST` and `BIFROST_PUBLIC_URL` only on a trusted network.
The published dashboard passes through a narrow reverse proxy that blocks
`/v1/*`; Bifrost's built-in login protects the remaining dashboard and
management routes. Inference is reachable only on the private Compose bridge,
and browser clients never receive Bifrost credentials or network access.

## Local model development

Development inference uses vLLM only. Do not configure Ollama or paid/cloud
providers in the development Bifrost instance. The sole development model name
is `loreholm-local`; Bifrost must route it to the local vLLM service and must
have no fallback provider.

The GPU development overlay is separate from the adopter stack so a normal Loreholm
installation does not require an NVIDIA GPU. It defaults to the cached
`Qwen/Qwen3-8B` weights and NVIDIA vLLM 25.11, the newest tested image
compatible with the dev machine's 580-series driver:

```bash
docker compose \
  --env-file deploy/.env.v2 \
  -f deploy/docker-compose.v2.yml \
  -f deploy/docker-compose.v2.dev.yml \
  up -d
```

Set `VLLM_IMAGE`, `VLLM_MODEL`, or `VLLM_SERVED_MODEL` in the environment to
test another local configuration. Keep `HF_HUB_OFFLINE=1` so startup fails
closed when weights are not already present instead of downloading implicitly.
The development service uses eager execution because CUDA graph/FlashAttention
capture is not reliable on the current GB10 development driver stack.

## Browser chat networking

The Loreholm browser chat remains a separate-origin static application. It sends an
OIDC access token to the cloud API's `/chat/stream` endpoint; the cloud resolves
the user's Tailnet address and replaces that credential with the per-user sync
token before dialing port `8081`. In development, the retained tunnel topology
is applied as the `docker-compose.v2.remote.yml` overlay, which runs the
Tailscale sidecar and endpoint shim. That shim currently forwards
`/api/chat/*` only, while the instance streams from Bifrost on its private
Compose bridge and records both sides as raw `transcript.message` captures.
