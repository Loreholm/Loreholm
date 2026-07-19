# Loreholm architecture

Loreholm is a self-hosted capture and knowledge-building framework. Connected
tools submit the context around a person's work; the user's Loreholm instance
alone decides how that context becomes entities, claims, relationships, and
other durable knowledge.

This boundary keeps individual integrations simple and keeps interpretation,
identity, evidence, and graph changes under one instance-owned policy. A public
front door can reach the containerized instance through a Headscale-managed
Tailscale tunnel while the data itself stays local.

## Status legend

This document distinguishes running code from accepted design:

- **Implemented** means the Loreholm application and deployment provide it now.
- **Planned** means the behavior is accepted design but is not executable yet.

## Data flow

```text
surface adapter
    |
    | raw event or snapshot
    v
embedded spine                         [planned]
    | identity, policy, offline queue
    v
POST /v2/captures                      [implemented]
    |
    v
append-only V2Capture staging          [implemented]
    |
    v
interpret -> mine -> resolve           [planned]
    |
    v
knowledge graph + provenance           [planned]
    |
    v
query and surfacing                     [planned]
```

The adapter is a sensor, not an authority. It maps native context into a
capture envelope without deciding which facts should enter the graph. The
instance is the sole eventual graph writer.

## Implemented foundation

### Capture contract

The authenticated `POST /v2/captures` endpoint accepts batches of capture
envelopes. Every envelope includes:

- a spine-minted UUIDv7 `capture_id`;
- `event` or `snapshot` kind and a versioned capture class;
- source surface, optional session reference, and device occurrence time;
- an opaque payload plus optional references and non-authoritative hints; and
- contract, spine, adapter, device, user, queue-age, and policy metadata.

Snapshots require a SHA-256 content hash. Event identity and snapshot content
identity remain separate so delivery retries can be idempotent without losing
object-version semantics.

### Durable staging

The instance stores the complete envelope in ArcadeDB's append-only
`V2Capture` document region. A unique index on `capture_id` makes retries
idempotent. Device timestamps are retained, while gross clock skew is
normalized using receipt time and the adapter-reported queue age.

Capture classes present in instance policy enter the `staged` state. Unknown
classes are accepted into `quarantined_unknown_class` rather than rejected or
silently lost. Quarantined captures cannot enter the future mining pipeline
until the instance supports their class.

### Policy

Authenticated clients read instance policy from `GET /v2/policy`. Policy
advertises the supported contract range, capture-class controls, remote
processing mode, and mining status. Administrators can update the persisted
policy through the local Loreholm dashboard API.

The future embedded spine will cache and enforce this policy before upload.
That client-side spine and its offline queue are not implemented yet.

### Browser chat capture

The Loreholm chat path records both the user's message and the completed assistant
response as raw `transcript.message` captures. This is the first working
example of passive capture: using the conversation surface creates capture
records without a separate “remember this” tool call. See
[Loreholm browser chat](09_Chat.md) for the front-door, streaming, and capture
contract.

### Instance deployment

One Loreholm instance is one lifecycle and trust boundary:

```text
host
  |- instance API :8082 (loopback by default)
  |- Bifrost management proxy :8083 (loopback by default)
  `- private Compose bridge
       |- ArcadeDB (one database owned by this instance)
       `- Bifrost (the only model-egress path)
```

ArcadeDB and Bifrost inference are not published to the host. The instance API
is the only process that writes captures to ArcadeDB. Hosting several separate
knowledge worlds means deploying several instances, not creating tenants in a
shared instance database.

### Front-door and tunnel topology

Loreholm keeps the public service separate from the private instance:

```text
browser / remote client
          |
          | HTTPS + OIDC
          v
public front door
          |
          | Headscale-managed Tailscale network
          | per-user route + instance sync credential
          v
Tailnet endpoint shim :8081
          |
          | explicit application routes only
          v
Loreholm instance API ------ ArcadeDB
          |
          `------------ Bifrost
```

The front door authenticates the user, resolves the user's Tailnet node, and
relays an authorized request. The Tailscale client runs in its own container;
only the endpoint shim shares its network namespace. The instance, ArcadeDB,
and Bifrost remain on the private Compose bridge. The tunnel therefore reaches
locally stored data through the instance's application contract, never through
direct database or model-gateway exposure.

The Loreholm remote Compose overlay already implements the Tailscale sidecar and
`:8081` endpoint pattern for browser chat. The current shim allow-list forwards
`/api/chat/*` only. As capture, policy, and graph-surfacing flows are connected
to the front door, they should extend this application-level allow-list without
placing ArcadeDB, Bifrost, or the host on the Tailnet.

See [Loreholm networking](02_Networking.md) for the retained invariants and current
implementation boundary.

The implemented HTTP envelope and receipt semantics are documented in the
[capture API](03_CaptureAPI.md). The planned adapter side is documented in
[clients and spine](06_ClientsAndSpine.md).

## Planned mining pipeline

The accepted Loreholm design calls for the instance to turn staged context into
inspectable knowledge:

1. **Trigger:** process transcript sessions after quiescence, explicit pushes
   immediately, and stragglers during a periodic sweep.
2. **Interpret:** derive episodes, references, temporal meaning, and salience
   without mutating the raw capture.
3. **Mine:** extract mentions and candidate claims through Bifrost under the
   instance's processing, egress, and budget policy.
4. **Resolve:** reconcile mentions with stable entities and distinguish exact
   retries from independent supporting observations.
5. **Commit:** write claims and first-class evidence records that link every
   derived assertion back to its source captures and mining run.
6. **Surface:** answer queries from the mined graph while retaining the raw
   context and its audit trail.

Mining outputs will carry miner, model/config, input, and schema fingerprints.
Re-mining must be scoped and idempotent: a new successful generation can
supersede prior derived output without replacing stable accreted identities or
destroying independent evidence.

None of the interpreter, miner, entity-resolution, graph-commit, or query
stages above are implemented in the current foundation milestone.

See [mining and knowledge](07_MiningAndKnowledge.md) for the accepted identity,
provenance, schema, vector, and surfacing decisions, and
[data lifecycle](08_DataLifecycle.md) for deletion, backup, and sharing.

## Current component map

| Component | Status | Responsibility |
|---|---|---|
| Loreholm instance API | Implemented | Authentication, policy, capture ingestion, dashboard, chat proxy |
| ArcadeDB capture store | Implemented | Append-only raw captures and instance configuration |
| Bifrost gateway | Implemented | Sole model-egress boundary and provider management |
| Browser-chat capture | Implemented | Automatically records both sides of a chat transcript |
| Headscale/Tailscale tunnel | Implemented for chat | Front-door route to the user's containerized instance |
| Tailnet endpoint shim | Implemented for chat | Allow-listed application ingress on port 8081 |
| Surface adapters | Planned | Convert IDE, browser, mobile, and other native context into captures |
| Embedded spine | Planned | Identity, policy enforcement, redaction, ordering, and offline delivery |
| Interpreter/miner | Planned | Turn raw captures into episodes, mentions, entities, claims, and evidence |
| Graph query/surfacing | Planned | Retrieve mined knowledge for users and assistants |

## Source map

- `api/app/v2/models.py` — capture, policy, model, and chat contracts
- `api/app/v2/service.py` — capture ingestion and ArcadeDB persistence
- `api/app/v2/app.py` — Loreholm application, administration, and chat capture
- `deploy/docker-compose.v2.yml` — private instance deployment
- `deploy/docker-compose.v2.remote.yml` — retained tunnel topology, currently wired for chat
- `deploy/v2-endpoint-shim.py` — Tailnet application-route allow-list
