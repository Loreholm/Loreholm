# Loreholm trust and security model

Loreholm's front door, Headscale/Tailscale tunnel, container isolation, and
local-data boundary define how requests reach user-owned memory. The knowledge
pipeline changes how information is produced behind that boundary. This page
distinguishes the base local stack from the retained remote topology; future
mining and sharing features will require their own threat-model review as they
are implemented.

## Custody is not invisibility

Self-hosting changes who owns Loreholm's durable context and policy. It does not
make an external model endpoint unable to read or retain a payload it receives.
The strongest confidentiality boundary is to keep content local; whenever
remote processing is allowed, provider settings, contract, jurisdiction, and
retention behavior remain part of the threat model.

Loreholm should expose the complete effective model request and the policy
decision behind it. That universal egress inspection is a planned security
control, not an implemented guarantee in the current foundation. See
[why Loreholm exists](00_Principles.md).

## Default local boundary

A normal Loreholm installation is private to the machine by default:

- The instance API is published on `127.0.0.1:8082`.
- The authenticated Bifrost management proxy is published separately on
  `127.0.0.1:8083` and blocks Bifrost's `/v1/*` inference routes.
- ArcadeDB and Bifrost inference remain on the private Compose bridge and have
  no host ports.
- The instance is the only application process that persists Loreholm captures.

Binding the instance or Bifrost dashboard to a LAN address is an explicit
operator choice. Authentication still applies, but operators should avoid
`0.0.0.0` on machines with untrusted interfaces and place remote access behind
an authenticated TLS proxy or private overlay.

## Credentials

The installer generates separate random credentials for:

- device capture and policy access;
- instance dashboard administration;
- optional cloud-to-instance chat synchronization;
- ArcadeDB administration; and
- Bifrost administration.

Raw bearer tokens and passwords are stored in
`~/.local/share/loreholm-v2/state/instance.env`. The state directory is mode
`0700` and the environment file is mode `0600`. The application receives
SHA-256 token digests for device, dashboard, and sync authentication rather
than using the raw bearer values as its comparison source.

Treat the environment file as the key to the instance. Host compromise or an
account that can read that file is outside the isolation the application can
provide. Loreholm currently relies on host disk or volume encryption for data at
rest.

## Capture and policy authorization

- `GET /v2/policy` and `POST /v2/captures` require the device bearer token.
- `/v2/admin/*` endpoints require the distinct administrator bearer token.
- `/api/chat/*` requires the distinct synchronization bearer token.
- Token digests are compared with constant-time comparison.

Capture authorization controls who may submit context; it does not make every
captured statement true. Capture hints are non-authoritative. The implemented
extractor produces candidates only. The implemented resolver records bounded,
auditable identity decisions, while the deterministic claim committer validates
the versioned relation schema and creates first-class Evidence before graph
truth is admitted.

## Model-egress boundary

Bifrost is the only permitted path from the instance to a model endpoint. The
instance does not fall back to direct provider calls. Provider selection,
processing mode, and future mining budgets belong to instance policy. Before
structured extraction, the worker rechecks current capture policy for every
source and applies the endpoint's operator-declared `local` or `remote`
processing location. Remote raw extraction requires `unrestricted` for every
involved class; the other modes fail closed until their transformations exist.

The processing-location declaration is a security assertion, not network
detection. Marking an externally hosted endpoint as `local` defeats the remote
egress gate. Operators must use `local` only for a model running inside the
Loreholm instance boundary.

For development, the supported configuration is the local `loreholm-local`
model through vLLM with no cloud fallback. Production operators may configure
their own compatible endpoint through Bifrost.

## Front-door and tunnel boundary

The production architecture keeps a public front door between remote clients
and user instances. The front door authenticates users with OIDC, uses
Headscale to resolve the correct node, and reaches that node over Tailscale.
It replaces the public credential with a per-instance synchronization token
before dialing the user's endpoint.

On the user machine, the remote Compose overlay adds:

- a Tailscale client with its own container network namespace; and
- an endpoint shim sharing that namespace on port `8081`.

The current Loreholm shim returns health on `/healthz` and forwards only
`POST /api/chat/*` to the instance. All other GET and POST paths return 404. It
does not expose `/v2/captures`, `/v2/policy`, `/v2/admin`, ArcadeDB, or Bifrost.
That allow-list will grow only as Loreholm adds authorized remote application
contracts; direct database, model-gateway, Docker, and host access remain
outside the design.

The separate-origin browser sends its OIDC access token to the cloud API. The
cloud resolves the user's Tailnet endpoint and replaces that credential with
the per-instance sync token before calling the shim. The instance records the
resulting user and assistant messages as raw transcript captures.

The Tailnet ACL, cloud credential exchange, endpoint allow-list, and container
network namespaces are load-bearing parts of the retained topology. Changes to
any of them require security review. See [Loreholm networking](02_Networking.md).

## Data and deletion limits

Raw captures currently persist in ArcadeDB without an implemented retention or
deletion interface. The planned design includes capture-level deletion,
provenance-aware derived-data cleanup, backup semantics, and retention policy,
but those controls do not exist in the foundation milestone. Do not document
planned deletion guarantees as available behavior.

The accepted lifecycle guarantees and their unimplemented status are detailed
in [data lifecycle](08_DataLifecycle.md).

## Verify a local installation

```bash
export LOREHOLM_HOME=${LOREHOLM_HOME:-$HOME/.local/share/loreholm-v2}
ENV_FILE="$LOREHOLM_HOME/state/instance.env"
COMPOSE_FILE="$LOREHOLM_HOME/source/deploy/docker-compose.v2.yml"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
ss -lnt | grep -E ':(8082|8083)'
curl -fsS "http://127.0.0.1:${LOREHOLM_V2_PORT:-8082}/health"
```

For the tunnel deployment, also inspect
`deploy/docker-compose.v2.remote.yml`, `deploy/v2-endpoint-shim.py`, and the
active Headscale/Tailscale ACL before relying on its reachability boundary.

## Reporting security issues

See [`SECURITY.md`](../SECURITY.md). Do not test against hosted infrastructure
or another user's instance without explicit authorization.
