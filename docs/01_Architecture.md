# Loreholm architecture

Loreholm is a self-hosted capture and knowledge-building framework. Connected
tools submit the context around a person's work; the user's Loreholm instance
alone decides how that context becomes entities, claims, relationships, and
other durable knowledge.

The architecture follows a moral boundary: model providers may supply
intelligence, but they should not become the default owners of a person's
durable working memory. Loreholm aims to make application-added model context
visible and keep capture, policy, storage, mining, and evidence in the user's
instance. See [why Loreholm exists](00_Principles.md).

This boundary keeps individual integrations simple and keeps interpretation,
identity, evidence, and graph changes under one instance-owned policy. A public
front door can reach the containerized instance through a Headscale-managed
Tailscale tunnel while the data itself stays local.

Both sides are self-hostable. Most users only need to operate the private
instance, while operators who want control of authentication, TLS, and network
coordination can also run the repository's server-plane stack. See
[self-hosting](11_SelfHosting.md).

## What makes this more than ordinary RAG

Conventional retrieval-augmented generation often begins by splitting source
material into chunks, embedding every chunk, and retrieving whichever vectors
are closest to a question. That can be useful, but it makes the retrieval index
carry responsibilities it was never designed to own: identity, duplicate
control, time, relationships, evidence, and the difference between a repeated
observation and a changed fact.

Loreholm keeps the raw context first. Every capture arrives with stable
identity, source, time, policy, and delivery metadata, so retries can be
recognized without losing the original record. The implemented pipeline admits
useful material, interprets it into episodes and mentions, and derives only the
vectors needed for tasks such as entity resolution and schema maintenance.
Raw captures are not indiscriminately embedded.

The vector regions are working indexes, not the final authority. A specialized
instance-owned mining and graph-maintenance pipeline receives structured
candidates plus their provenance, resolves them against stable entities,
checks the relation schema, and attaches evidence before deterministic graph
commit. Repeated support for the same claim adds evidence rather than requiring
a duplicate fact, while uncertainty can remain explicit instead of being
forced into a brittle link.

That is the database upgrade Loreholm is pursuing: raw history remains
inspectable, vector retrieval works over selected derived material, and the
graph is built from rich metadata and evidence rather than isolated chunks.
Capture, idempotent staging, derived mention vectors, extraction, identity
resolution, evidence-backed graph commit, and compensating knowledge maintenance
work today. Vector-seeded one-hop grounded surfacing also works today; multi-hop
and episode-style recall plus the remaining lifecycle layers remain to be
implemented.

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
policy gate + capture staging          [implemented]
    |
    v
session assembly + admission work      [implemented]
    |
    v
mechanical trim + salience record      [implemented]
    |
    v
interpret + candidate extraction       [implemented]
    |
    v
mention vectors + resolve              [implemented]
    |
    v
schema-valid Claims + Evidence          [implemented]
    |
    v
vector-seeded query and surfacing       [implemented]
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

Known classes enabled by instance policy enter the `staged` state. For a new
capture ID, a known but disabled class returns a `policy_blocked` receipt and is
not stored. An ID that was already stored still returns `duplicate`, even if
the class is now disabled. Unknown classes are accepted into
`quarantined_unknown_class` rather than rejected or silently lost. Quarantined
captures cannot enter the future mining pipeline until the instance supports
their class.

### Admission boundary

Accepted `transcript.message` captures assemble into durable session scopes
with idempotent membership. A session work item becomes available after the
latest received member has been quiet for the configured interval. Accepted
explicit pushes receive immediately available admission work.

Admission items have generation-numbered identities and fixed-duration leases.
Workers can reclaim expired leases, complete work they currently own, or return
owned work to the pending state with a delayed retry. The instance worker now
consumes ready items, builds a bounded derived view without changing raw
captures, and stores an idempotent admitted-or-skipped salience record. Admitted
records receive separate durable mining work. When mining is active, the
extractor sends policy-permitted bounded input through Bifrost and persists
strictly validated episode, mention, and candidate-claim output. It does not
write the graph directly; the resolver and deterministic committer consume its
durable output before mining work completes.

### Policy

Authenticated clients read instance policy from `GET /v2/policy`. Policy
advertises the supported contract range, capture-class controls, remote
processing mode, and an `active` or `paused` mining status.
Administrators can update the persisted capture-class policy through the local
Loreholm dashboard API. The instance enforces disabled capture classes before
storage.

The future embedded spine will cache and enforce the same policy before upload.
That client-side spine and its offline queue are not implemented yet. The
extractor enforces processing location and remote-processing policy before
model egress; sanitizer and derived-only transformations remain unimplemented
and therefore fail closed for remote extraction.

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

## Admission, mining, and grounded query pipeline

The accepted Loreholm design calls for the instance to turn staged context into
inspectable knowledge:

1. **Trigger and gate:** durable quiescent-session and immediate-push admission,
   scheduled consumption, mechanical trimming, and salience decisions are
   implemented. A periodic straggler sweep is planned.
2. **Interpret:** derive episodes, references, and temporal meaning without
   mutating the raw capture.
3. **Mine:** extract mentions and candidate claims through Bifrost under the
   instance's processing, egress, and budget policy.
4. **Resolve:** reconcile mentions with stable entities and distinguish exact
   retries from independent supporting observations.
5. **Commit:** write claims and first-class evidence records that link every
   derived assertion back to its source captures and mining run.
6. **Surface:** answer queries from the mined graph while retaining the raw
   context and its audit trail. The implemented first slice embeds the question,
   groups nearest mention vectors by resolved entity, traverses registered
   one-hop Claims, hydrates active Evidence, and optionally synthesizes a
   citation-validated answer through Bifrost.

The implemented `V2MiningRun` foundation carries stage, miner,
model/configuration, and input fingerprints plus parent-run lineage. For a new
session generation, the input coordinator reuses the latest compatible
successful output and supplies only new captures plus a bounded context tail.
Context-only captures are excluded from the new-evidence set. Graph commit uses
that boundary now: exact source retries reuse Evidence, while a genuinely new
capture can add independent Evidence to the same semantic Claim. Scoped
supersession across replacement generations is implemented as an explicit,
audited re-mining request: replacement commit succeeds before unsupported old
Evidence is retired, and independently supported Claims remain active.

The structured interpreter/extractor, entity resolver, deterministic claim
committer, compensating maintainer, and grounded one-hop query stage are
implemented. Multi-hop query planning and episode-style broad recall remain
planned.

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
| Structured extractor | Implemented | Turn admitted captures into versioned episode, mention, and candidate-claim output |
| Entity resolver | Implemented | Embed extracted mentions, retrieve typed candidates, and persist audited identity decisions |
| Claim and evidence committer | Implemented | Validate the versioned relation schema and commit idempotent Claims with first-class Evidence |
| Graph query/surfacing | Implemented | Use mention vectors to seed bounded Claim traversal and return source-linked Evidence with optional cited synthesis |

## Source map

- `api/app/v2/models.py` — capture, policy, model, and chat contracts
- `api/app/v2/service.py` — capture ingestion and ArcadeDB persistence
- `api/app/v2/salience.py` — bounded mechanical views and admission decisions
- `api/app/v2/mining.py` — incremental preparation, Bifrost extraction, policy
  enforcement, validation, and mining-run persistence
- `api/app/v2/resolution.py` — Bifrost embeddings, vector/string candidate
  scoring, bounded identity judgment, and durable mention resolution
- `api/app/v2/claims.py` — deterministic relation validation, semantic Claim
  identity, Evidence attachment, and commit markers
- `api/app/v2/query.py` — vector seed discovery, constrained planning, Claim
  traversal, Evidence hydration, policy checks, and cited synthesis
- `api/app/v2/relation_schema.json` — shipped Git-versioned core relation schema
- `api/app/v2/app.py` — Loreholm application, administration, and chat capture
- `deploy/docker-compose.v2.yml` — private instance deployment
- `deploy/docker-compose.v2.remote.yml` — retained tunnel topology, currently wired for chat
- `deploy/v2-endpoint-shim.py` — Tailnet application-route allow-list
