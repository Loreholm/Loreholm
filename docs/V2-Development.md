# Loreholm V2 development stack

V2 is a greenfield instance and does not reuse a V1 database. The initial
executable slice implements contract-v2 capture ingestion, policy sync,
idempotent staging, clock normalization, and unknown-class quarantine.

The adopter path is the installer; it checks prerequisites, generates instance
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

The API is intentionally bound to loopback. Put an authenticated TLS reverse
proxy or private overlay network in front of it before connecting a remote
adapter. Bifrost is present as the sole model-egress boundary, but endpoint
configuration remains instance-owned and is not baked into the repository.
Mining is not enabled in this foundation milestone.

For a trusted LAN, set `LOREHOLM_V2_BIND_HOST` to the host's LAN address in the
instance environment and recreate the `instance` service. Authentication still
applies to policy, capture, and dashboard administration. Avoid `0.0.0.0` when
the machine also has untrusted network interfaces.

## Local model development

Development inference uses vLLM only. Do not configure Ollama or paid/cloud
providers in the development Bifrost instance. The sole development model name
is `loreholm-local`; Bifrost must route it to the local vLLM service and must
have no fallback provider.

The GPU development overlay is separate from the adopter stack so a normal V2
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
