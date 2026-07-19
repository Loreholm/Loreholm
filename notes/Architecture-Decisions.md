# Architecture Decisions

Recorded 2026-07-08. Loreholm is moving from an MCP where third-party clients
shape what lands in ArcadeDB to a framework that ships its own client, with a
capture system and mining system in complete control of the database.

This is a greenfield v2 design, not an in-place migration. The v1 database
schema, MCP write contract, reconciler behavior, and deployment topology do
not constrain v2, and v2 does not promise backward compatibility with v1
clients or databases. Useful v1 implementation work may be reused deliberately,
but compatibility adapters and data migration are separate future decisions,
not requirements on this architecture.

Development-machine constraint recorded 2026-07-11: all development inference
uses the local vLLM endpoint (`loreholm-local`). Ollama is not part of the
development environment, and paid/cloud providers must not be configured as a
development fallback.

## Settled

### Client/server split
- Two minds: the assistant only answers the user; interpretation happens
  server-side in the mining pipeline. The client never writes the graph.
- Client = sensor, not authority. It emits raw captures (plus optional
  evidence/hints with provenance); the server treats hints as
  priors it can override. The instance is the sole writer.
- Firewall rule: no interpretation logic in the client or spine. The moment
  the spine dedupes "intelligently" or summarizes, the observer has been
  smuggled back into the client.

### Client harness: adapter + spine
- Thin per-surface adapter (IDE, browser, mobile app, etc.): dumb mapping of
  native events/context into a common capture shape. No intelligence.
- Shared spine (one codebase): identity/auth, offline queue, ordering,
  redaction, permission-flag enforcement, transport.
- Capture shape at the seam: `(surface, timestamp, kind, payload, refs)` —
  refs ground deixis (what was on screen/selected). Expanded into the full
  envelope under decision H.
- Redaction and permission enforcement live in the spine, below the adapter,
  so no surface can forget it. Policy is synced from the server.

### Spine packaging: embedded library, no daemon
- User rejected invisible background processes. Each adapter embeds the spine
  and is independently visible/removable through its host app's native
  uninstall mechanism.
- Accepted costs: N queues and N flag syncs (mitigated: server owns flag
  truth); stranded captures while host app is closed (instances accept
  out-of-order arrival); version skew across adapters on one device (spine
  version goes in capture metadata from day one).

### Permissions
- Permissions are behaviour control, not a privacy feature. What lands on
  the user's machine is the user's; flags exist so the user steers what the
  system captures and spends, not to protect third parties.
- Per-capture-class permission flags; per-user policy synced through the
  server, enforced locally in the spine.
- The policy source is the user's instance — the same server that owns the
  database and miner — not a separate cloud control plane. Each embedded
  spine caches the last policy it received and continues to enforce that
  policy while it cannot reach the instance.
- Local adapter controls may always make the cached policy stricter: pause or
  disable takes effect immediately and never waits for connectivity. Offline
  captures remain only in the adapter's local queue; they cannot be mined,
  shared, or sent to a model while disconnected.
- On reconnection, the spine refreshes policy before upload and re-evaluates
  the queued captures against the current policy. Captures no longer allowed
  by that policy stay local for user review or deletion rather than uploading
  under stale permission.
- Device/adapter revocation is a hard boundary. Once its credentials are
  revoked, the instance rejects every subsequent upload authenticated by
  those credentials, including captures claiming an `occurred_at` before the
  revocation. Client timestamps and queued payloads are untrusted input and
  cannot bypass revocation; there is no automatic quarantine or recovery path.
- Default to app-level context, not raw screen.
- Visible capture indicator plus one-gesture pause.
- Permissions and budgets are one control surface: what is captured and what
  is spent understanding it are dials on the same panel, set at install,
  conservative defaults.

### Data egress boundary
- Raw captures remain local by default. Capture permission and model egress
  are separate controls: permission to capture something does not imply
  permission to send it to a remote model.
- The default remote-processing mode is **sanitized remote**. A local
  preprocessing stage detects and replaces sensitive values (names, email
  addresses, credentials, paths, customer identifiers, and similar data)
  with stable opaque tokens before a remote model sees the content. Model
  output retains those tokens; only local code resolves them before graph
  commit.
- Instance policy supports a spectrum rather than a local/remote binary:
  local-only, sanitized remote, derived-only remote, per-capture-class
  allowlisting, endpoint allowlisting, and explicitly enabled unrestricted
  remote processing. Sending raw content remotely always requires an
  explicit user choice.
- Miner egress is enforced below the mining stages and all model calls pass
  through the instance's Bifrost gateway. Bifrost is the sole outbound model
  network path and owns detailed request/response logging under its configured
  retention policy. Loreholm records only the Bifrost correlation ID,
  endpoint/model assignment, processing mode, capture IDs/classes, policy
  version, and cost with the mining run; it does not duplicate prompt/response
  bodies into a second audit log.
- Sanitization reduces disclosure risk; it is not treated as a privacy
  guarantee. Strict guarantees require local-only processing. Raw captures,
  including detected secrets, remain intact in local storage; redaction and
  tokenization transform only the copy prepared for remote model egress.
- The user assigns an egress trust mode to each model/endpoint configuration,
  controlling whether it receives sanitized, derived-only, class-allowlisted,
  or explicitly unrestricted content. Changing that trust mode affects future
  calls and re-mining, not the locally preserved raw capture.

### Encryption at rest
- V2 relies on host disk/volume encryption for data at rest. Loreholm does not
  add application-level payload encryption, per-class keys, or its own key
  recovery system. Install and security guidance must make host encryption an
  explicit prerequisite/recommendation and note that it protects powered-off
  storage, not a running compromised host.
- The host-encryption boundary covers ArcadeDB data and external-property
  buckets, the Git-versioned schema, local client queues, logs, and backups
  only when those paths reside on encrypted volumes. Application-wide
  encryption is a separate future architecture decision, not a v2 requirement.

### Backup + future redundancy
- Backup is an instance capability controlled by the user, not an external
  Loreholm service. Instance configuration selects whether backups run, their
  schedule, retention, and destination. Loreholm provides backup status and
  restore verification rather than assuming that local storage is sufficient.
- A recoverable backup is an instance-consistent bundle: ArcadeDB data
  including external-property buckets, the Git-versioned schema and active
  commit, plus the non-secret configuration needed to reconstruct the
  deployment. Backup creation coordinates these components so their recorded
  versions agree.
- Backups exclude secrets: model-provider keys, Bifrost credentials, client
  credentials, signing material, and session tokens are never included. After
  restore, the human re-enters provider keys and reauthorizes clients. V2
  accepts this small recovery step rather than introducing encrypted secret
  bundles or making every backup a credential vault.
- Finalized deletions are excluded from every future backup. Older backup sets
  may retain bytes captured before deletion until those sets reach their
  configured backup-retention expiry; Loreholm does not rewrite immutable
  historical backups. Restore tooling identifies such backup age and reapplies
  any available later deletion tombstones before restored content becomes
  active.
- Redundancy/replication is expected in the future but is a separate decision.
  V2 backup interfaces should not assume a single permanent storage topology,
  but V2 does not require a redundant ArcadeDB or object-storage deployment.

### Decision A: instance topology
- Instance = ArcadeDB + miner/control service + Bifrost, one self-contained
  deployable unit. Personal and shared instances live on a network; same
  artifact, N deployments.
- Each instance owns exactly one ArcadeDB database. Personal/shared isolation,
  schema Git history, policy, budget, backup, and lifecycle boundaries align
  at that database boundary. Hosting several worlds means deploying several
  instances, not creating tenants inside one instance database.
- The spine ships everything to the user's default (personal) instance only.
  Zero routing judgment in the spine.
- Sharing to other instances is an explicit user act, served by the personal
  instance (not the spine): send a slice — raw session or mined subgraph —
  to instance X over one instance-to-instance protocol.
- Consent = the share gesture. Raw crosses a trust boundary only by explicit
  user action.
- Sharing is disclosure, not a revocable remote reference. Outside instances
  are treated like the internet: once a slice is sent, the recipient has an
  independent copy and the origin cannot enforce recall, deletion, or future
  use. The share UI must state this at the gesture. Loreholm does not present
  best-effort deletion notices as a security or privacy guarantee.
- Instances never auto-sync. Entity overlap across instances (e.g. the same
  person in personal and work graphs) is by design, fused at query time by
  whatever surfaces it, not at write time.

### Network boundary retained from V1 (called 2026-07-19)
- V2 changes capture, mining, and graph authority; it retains the front-door
  and private-tunnel topology. Remote browsers and clients authenticate to a
  public front door, which resolves the user's instance through Headscale and
  reaches it over Tailscale.
- Tailscale runs in a container rather than on the user host. A narrow endpoint
  shim shares its network namespace and forwards an explicit allow-list of V2
  application routes to the instance over the private Compose bridge.
- ArcadeDB, Bifrost, Docker, and the host are never exposed directly on the
  Tailnet. The tunnel connects authorized users to locally held data through
  the instance contract, not through raw database access.
- Public OIDC credentials terminate at the front door. The front door replaces
  them with a per-instance synchronization credential before dialing port
  `8081`. Headscale/Tailscale ACLs retain implicit-deny isolation between user
  nodes and all non-allow-listed ports.
- The route allow-list may grow with V2 capture, policy, sharing, and surfacing
  contracts, but doing so is a security-boundary change. It must not recreate
  V1's client authority to write interpreted graph facts.

### Decision B: capture classes
- Two capture kinds, two identity regimes; downstream treats them differently:
  - **Events** — immutable, point-in-time (a message sent, a command run).
    Identity = UUIDv7 minted once at capture, stored in the spine queue:
    stable across retries (idempotent delivery), time-ordered so it doubles
    as a time-series key. Content hashing buys events nothing — identical
    payloads ("yes" typed twice) are distinct occurrences.
  - **Object snapshots** — observations of a stateful thing (a document, a
    screen). Content-addressed: hash of the bytes IS the version identity;
    same bytes captured twice dedup for free. Object identity
    (path/URL/doc-id) is a separate second layer; version chain = sequence
    of distinct hashes attached to one object ID.
- **V2 initial scope: transcripts + explicit push.** Transcripts means all assistant
  surfaces (chat, IDE agent). Explicit push is the "remember this" /
  share-sheet gesture.
- **V2 initially has no passive capture of documents, browser, or screen.** Documents
  and screen shares enter only as explicit user pushes. The rationale is
  initial client simplicity. The separate capture, retention, and data-egress
  policies govern privacy and control. Passive capture
  of these classes means FS watchers, format parsing, and a screen pipeline;
  push needs none of it.
- Pushed documents/screens arrive as snapshots, so the initial scope carries a thin slice
  of the snapshot regime: content hash for version dedup; object ID
  best-effort (user-supplied ref or path when present).
- Rationale (why transcripts + push won):

| Class | Kind | Signal density | Volume | Adapter cost | Identity |
|---|---|---|---|---|---|
| Assistant transcripts | Event | Max — user's thinking, pre-distilled | Low MB | Low — few apps, text APIs | Easy |
| Explicit push | Either | Max — user pre-judged salience | Tiny | Trivial | Easy |
| Documents (passive FS watch) | Snapshot | High | Med | Med — watch + format parsing | Hard — version chains |
| Code events (commit, save, PR) | Event | Med — git records much already | Med | Low | Easy |
| Email/Slack | Event | High | Med | Med | Easy |
| Browser history/pages | Snapshot | Low — mostly noise | High | Med | Hard — what is "the page" |
| Screen (OCR) | Snapshot | Low per byte | Max | High | Worst |

  Transcripts and push dominate every column. Passive candidates
  (documents, code events, email/Slack, browser, screen) remain possible
  later classes once the client can afford them.

### Mining pipeline
- Observer and maintainer are one agent, two re-runnable stages:
  raw captures -> ArcadeDB staging -> interpret (episodes, refs, salience)
  -> mine (entities, reconcile) -> graph commit.
- Cheap gate before tokens: salience filtering with no LLM before anything
  reaches the miner (heuristics only initially — see G; raw is never embedded,
  so no embedding signals either). Capture liberally, interpret
  selectively.
- Provenance from every graph element back to the raw captures it came from.

### Mining idempotency + inference preservation
- Inference is user-paid work and is retained. Re-runnable does not mean
  recompute by default: before inference, each stage looks for a successful
  result with the same source scope, stage, miner version, model/config
  fingerprint, and input fingerprint, and reuses it on an exact match.
- Every mining run and output has a stable operation identity. Exact retries
  resume or return the existing outputs; they do not create duplicate
  mentions, candidate claims, evidence links, or committed claims.
- A newer miner or changed configuration may interpret the same source scope
  again. Old outputs remain visible as history until the replacement run
  completes successfully. The replacement then marks the outputs it covered
  as superseded; normal reads prefer the newest successful unsuperseded
  result, while audit/history reads can still inspect the earlier inference.
- Supersession is scoped: a newer miner only supersedes prior outputs for the
  captures or episodes it actually processed. A partial or failed backfill
  never hides earlier successful work.
- The same fact found in multiple independent captures is not a retry. It
  contributes another provenance/evidence link to the committed claim rather
  than creating a duplicate claim or erasing the earlier observation.
- User deletion is the exception to inference preservation and follows the
  retention rule below.

### Retention: deleting a raw capture
- Raw captures have no automatic age-based expiry by default. The instance
  keeps them until the user deletes them; storage pressure does not silently
  discard provenance.
- Stored content is a first-class discoverable inventory. Users can browse and
  filter captures by time, surface, class, session/object, size, mining state,
  salience, and last use; inspect aggregate storage by those dimensions; and
  select individual items or bulk ranges for deletion. Before confirmation,
  the instance previews payload/vector bytes reclaimed and which claims would
  remain supported or become archived.
- Deletion has a user-configurable local grace period, defaulting to 30 days;
  the user may shorten, extend, or choose immediate deletion. A deletion
  request marks the capture and its source-scoped artifacts
  `pending_deletion`: they disappear
  from normal queries, mining, sharing, and model egress immediately but their
  bytes and links remain recoverable until the deadline. Undo restores their
  prior active state without re-mining.
- When the grace period expires, finalization purges the payload and vector
  artifacts, closes evidence, and archives claims that lost their final active
  support as described below. Hard revocation/security deletion may bypass the
  grace period when explicitly requested.
- Deleting a raw capture purges its stored payload and every vector entry or
  embedding derived from that capture. Provenance makes this a mechanical
  traversal, not a semantic search.
- Evidence links sourced from the deleted capture are closed and retain only
  a tombstone sufficient to record that their source was deleted; deleted
  content and vectors are not retained for audit.
- A committed claim that loses its last surviving evidence link is archived:
  it remains visible in history/audit reads but is excluded from normal
  surfacing and no longer participates as an active fact.
- If a claim still has evidence from another, non-deleted capture, the claim
  remains active. Deleting one source does not erase independently supported
  knowledge.
- These deletion guarantees apply only inside the instance performing the
  deletion. Material previously shared to another instance is already an
  independent disclosure and cannot be recalled or reliably erased by the
  origin.

### Decision E: miner model class (called 2026-07-08)
- BYO endpoints are configured behind the instance's Bifrost gateway; miners
  speak one OpenAI-compatible contract to Bifrost and never call providers
  directly. Cloud providers and local runtimes such as Ollama, vLLM, and
  llama.cpp share one instance setup, routing, and credential surface; no
  bundled model. The small local proxy hop is accepted in exchange for keeping
  provider/runtime differences out of the miner and client configuration.
  Endpoint choice is primarily a quality and capability decision; the
  separate data-egress policy above governs what representation each endpoint
  may receive. Matches the BYODB / generic-OIDC pattern.
- Bifrost is a hard gateway, not an optimization. If it is unavailable,
  captures continue landing in staging but new inference remains queued until
  the gateway recovers. The miner never falls back to a direct provider call,
  because that would bypass endpoint policy, trust mode, logging, and the
  user's configured routing boundary.
- Tiered stages: pipeline stages declare a capability tier — extraction:
  high, C2 judge band: low, episode summaries: mid, schema maintainer:
  high — and instance config maps tiers to models. All tiers may map to
  one model; tiering is config, not obligation. J's budget dial gets a
  natural knob: downgrade a tier.
- Agent roles remain separately assignable even when they share a tier. In
  particular, schema proposer and schema reviewer/decider each have an
  instance-controlled model assignment. Loreholm may warn that assigning the
  same model reduces review independence, but it does not require different or
  stronger models; the human owns that tradeoff.
- Quality floor guard: a small bundled self-test runs when a model is assigned
  to a tier. It uses synthetic fixed transcripts and tolerant capability
  assertions (valid structured output, required facts/signals present, no
  invented required fields), not exact deterministic text or one canonical
  extraction. Failure warns the user and explains the failed capabilities;
  the user may override and use the model. Results record the test-suite and
  model/config versions so later changes are distinguishable. Rationale: a
  weak miner can pollute the accreted layer, but a brittle benchmark must not
  become an unchallengeable gate on user-chosen models.

### Decision C7: temporal model (semantics called; execution waits on C2)
- Claims carry `(subject, relation, object, valid_from?, valid_to?,
  recorded_at)` plus explicit precision/constraint metadata for temporal
  bounds. Unknown is represented as unknown, not filled with capture time.
- Models propose observations and temporal signals; deterministic claim-commit
  code applies the relation's schema. The same subject/relation/object adds
  evidence. A different object sits beside existing claims for a multi-valued
  relation. For a single-valued relation, prior claims close only when the
  source explicitly signals replacement; ambiguity preserves both.
- If a source says a transition happened but gives no date ("I left Acme and
  now work at Beta"), the old claim records an unknown end constrained to be
  before the observation, rather than pretending it ended at capture time.
  The new claim likewise has an unknown start unless the source supplies one.
- Missing temporal bounds become first-class maintenance gaps. Query responses
  can surface these gaps to Loreholm clients, whose assistants participate in
  maintaining the ecosystem by asking the user for clarification when it fits
  the conversation. A gap is an invitation to improve the record, not an
  inference failure that blocks the claim.
- After claim commit, each gap produces a durable structured
  `MaintenanceNotice` tied to the claim and relevant source session. Clients
  place open notices into assistant context; the model decides whether and how
  to ask naturally, while client code enforces user preference, relevance,
  suppression, and at-most-one-question limits. Notices remain open until
  resolved, dismissed, or marked unanswerable; if no conversation is active,
  they wait for a relevant future session.
- Record time is always present (append-only; buys undo/audit/supersede).
- Schema marks relations as stateful or eventive and declares cardinality;
  this is a requirement on decision D.

### Decisions C2/C3/C5/C6: entity and claim identity (called 2026-07-08)
- Entity = surrogate ID (UUIDv7) with no intrinsic content. Names, aliases,
  claims all hang off it as edges; minted at graph commit when resolution
  finds no acceptable match. Content-free identity is what keeps merge and
  split tractable.
- Resolution per new mention: NN over the mention-vector space + string
  similarity on surface forms (the complementary signals from I) ->
  candidate entities via their attached mentions -> match decision.
  Mechanism: auto-accept above a high similarity threshold, auto-mint below
  a low one, LLM judge on candidate pairs in the middle band. Band width is
  a budget dial (interacts with E and J); token cost sits behind the
  salience gate.
- C5 (ID stability across re-mining) dissolves by splitting the mined side
  into two regimes:
  - **Derived** — mentions, embeddings, candidate claims.
    Miner-version-stamped (C9), rebuildable but retained by default as paid
    inference; replaced generations are superseded, not erased.
  - **Accreted** — entities and committed claims. Never dropped on re-mine;
    re-mining produces fresh mentions that re-resolve against the existing
    entity registry.
  Stability is achieved by making entities not derived, not by making
  extraction deterministic (impossible). A re-mine may change which mentions
  point at an entity, never the entity's ID.
- C3: claims are surrogate-ID'd rows too. C7 interval-closing and
  contradiction detection are structured lookups on (subject entity,
  relation) — no content-derived claim identity.
- C6: merge = append a `merged_into(B, A)` supersede edge; readers resolve
  transitively at query time. Un-merge = close the supersede edge. Split =
  re-partition the entity's mentions across new entities. Append-only,
  consistent with C7's never-delete discipline; mention->entity edges and
  provenance survive every operation.
- Automatic merge direction is deterministic: the older entity ID is the
  canonical survivor and the newer entity points to it. Merge commit resolves
  both candidates to their current canonical roots in one transaction and
  rejects self-links or cycles. A human correction may explicitly select a
  different canonical entity; that choice is recorded as another reversible
  supersession rather than rewriting entity history.

### Decision C4: provenance link model
- Provenance is a first-class `Evidence` record between a raw capture and a
  committed claim: `Capture <- Evidence -> Claim`. This notation describes
  references, not graph edges: Evidence is a document carrying indexed
  `capture_id` and `claim_id` properties. This is many-to-many: one
  capture can support several claims and one claim can accumulate support from
  several independent captures.
- Evidence records carry a stable ID, source capture ID and span/ref, claim ID,
  extraction run/output identity, miner and schema versions, lifecycle, and
  recorded time. The source excerpt itself remains capture-derived content,
  not an independent permanent copy.
- Exact mining retries reuse the same evidence identity. A distinct capture
  asserting the same fact creates another evidence record attached to the
  existing claim.
- When a source capture is deleted, its evidence content is purged and the
  link is closed, leaving only a content-free deletion tombstone. The claim is
  archived only if no active evidence remains, per the retention decision.
- Shared slices preserve Evidence IDs under C8's origin namespace so support
  remains distinguishable and re-sharing is idempotent.

### Decision C8: cross-instance identity survival
- A shared record preserves its source identity as the pair
  `(origin_instance_id, entity_id)`. Instance IDs are stable and capture IDs,
  claims, evidence, and schema references carried in a shared slice use the
  same origin namespace, preventing collisions without a global authority.
- The receiving instance does not assume an imported entity is identical to a
  similar local entity. Normal entity resolution may attach the imported
  identity to an existing local entity or mint a new local entity; either way,
  the origin identity and provenance remain intact.
- A later correction can change the local mapping without changing the
  imported identity. Sharing the same slice again is idempotent on its
  origin-namespaced IDs.

### Decisions F/G: mining trigger + salience gate (called 2026-07-08)
- **F — trigger = session quiescence, not capture arrival or clock.** An
  active session is an incomplete episode; interpret's read pattern is
  range scans over session partitions, so the session is the natural unit.
  - Transcripts: interpret fires on a per-session idle timeout
    (configurable).
  - Explicit pushes: fire immediately — the user-initiated initial gesture
    must surface promptly.
  - Periodic sweep as safety net for stragglers and out-of-order arrivals
    (guaranteed by the spine's offline queues). Re-runnable stages make
    double-fires harmless.
  - Schema maintainer keeps its own slower cadence (D).
- **G — the gate defers, never destroys.** Staging is append-only; the
  gate controls what enters prompts, not what exists. Two layers:
  1. Mechanical trim at interpret-read time: drop/truncate tool outputs,
     dedupe retry loops, cap payload sizes. IDE-agent transcripts are
     mostly tool noise by bytes; user-authored text and assistant
     conclusions are the signal. Staging keeps the raw bytes.
  2. Episode admission: heuristic scoring only in the initial release (user-authored
     volume, turn count, surface) — no LLM, no embeddings, keeping "raw is
     never embedded" unqualified. Pushes bypass at max salience. The
     threshold is a J dial; initial volume is low enough to set it generous.
- Below-threshold sessions are skipped, not dropped: the gate decision is
  recorded and stamped (C9 pattern), so a later re-mine under looser
  thresholds picks them up. Skipped is re-runnable state, not loss.

### Decision H: capture contract + versioning (called 2026-07-08)
- Envelope (expands the seam shape from the harness section):
  - `capture_id` — UUIDv7, minted at the spine, on **every** envelope.
    The capture act is always an event ("user pushed doc X at t"), so
    transport identity and idempotent delivery are uniform across kinds;
    B's two identity regimes apply to what's inside, not to transport.
    Snapshot payloads carry `content_hash` + `object_ref` one layer down.
    Pushing the same bytes twice dedups on hash but keeps the record that
    the user pushed again.
  - `kind` (event | snapshot); `class` from a versioned class registry
    (transcript.message, push.document, ...).
  - `surface`; `session_ref` — the adapter's native session identifier,
    passed through dumbly. When a surface has no native session, the spine
    does NOT invent one (no time-gap clustering — that is interpretation
    smuggled into the spine); sessionless captures are sessionized
    server-side by interpret.
  - `occurred_at` (device clock) + `payload` (class-specific, opaque to
  the envelope) + `refs` (deixis) + `hints` (priors with provenance and
    source attribution).
  - `meta`: spine version, adapter id+version, contract version,
    device/user identity.
- **Time: preserve device time, normalize obvious skew.** The spine records
  device-claimed `occurred_at` plus the capture's queue age at upload; the
  server stamps `received_at`. Normally `occurred_at` drives ordering and
  episode segmentation, since offline queues make arrival time misleading.
  The instance compares `occurred_at + queue_age` with `received_at`; when the
  difference exceeds a configurable coarse tolerance (hours, not
  milliseconds), normalized time becomes `received_at - queue_age`. The raw
  device timestamp is always retained for audit. This corrects grossly wrong
  clocks without treating legitimate offline delay as clock skew.
- **Unknown classes are uploaded, accepted, and quarantined on the
  instance.** The instance is the durable storage authority and is expected
  to have more capacity than an embedded client, so a newer adapter does not
  strand captures locally while waiting for an instance upgrade. The stable
  envelope is still validated and normal authentication, payload-size, and
  storage-quota limits apply, but the class-specific payload remains opaque.
  Unknown classes cannot enter interpret, mining, sharing, or model egress.
  After the instance gains support, it releases them from quarantine into
  the normal pipeline. Version skew (accepted in the spine packaging call)
  is a normal state, not an error.
- Versioning: the envelope is the stable seam — additive-only within a
  major contract version; the class registry versions separately and is
  the growing edge. The instance publishes its supported contract range
  alongside the permission-policy sync.

### Decision C9: miner version stamping
- Every derived graph element is stamped with the miner version that produced
  it, plus the model/config and input fingerprints needed for exact reuse.
  This enables selective backfill without repeating unchanged inference.
  Successful backfills supersede only the earlier outputs whose source scope
  they actually covered; earlier generations remain available to audit and
  history reads.

### Decision J: budget ownership + exhaustion
- Mining budgets are configured and enforced by the user's instance alongside
  its capture and data-egress policy. Provider-side spending limits are useful
  backstops but are not Loreholm's source of truth because they may be absent
  or shared with unrelated applications.
- When the configured budget is exhausted, the miner stops starting new
  inference. It does not silently downgrade models or switch endpoints.
  Captures may continue uploading to staging and inference work remains queued
  until the budget is replenished or reset.
- The instance publishes `mining_status: paused_budget_exhausted` through the
  client policy/status sync. Connected clients surface the notification; an
  offline client receives it on reconnection. Budget exhaustion does not
  disable capture.
- Reset policy is user-configurable per instance: manual replenishment, a
  calendar schedule, or a rolling allowance are all supported. Exhausted work
  resumes only after the configured reset/replenishment takes effect; changing
  reset policy never silently starts inference while the instance still
  reports budget-paused.
- Budgets are denominated in money only, in the instance's configured
  currency. Paid endpoints use provider-reported cost where available and a
  configured model price table otherwise; the miner reserves estimated cost
  before a call and reconciles actual cost afterward so it does not knowingly
  start work beyond the cap. Local models with no monetary inference charge
  consume zero budget. Existing cost dials remain the G admission threshold,
  E tier-to-model mapping, and C2 judge-band width.

### Decision K: surfacing/query defaults
- The primary query consumer is model inference. Normal results return current
  claims with the strongest compact supporting evidence for each claim,
  selected within the query's token budget. Naked claims are too weakly
  grounded; full provenance on every result wastes context.
- Each result includes the structured claim, temporal bounds,
  compact evidence excerpt, source kind, and observation time. Archived or
  superseded claims are excluded from normal results.
- Additional evidence, full provenance, superseded generations, archived
  claims, and history are available through explicit query options rather than
  being included by default.

### Decision D: schema ownership (called 2026-07-08)
- Hybrid: shipped core + open extension tail + autonomous promotion.
- **The schema is Git-versioned.** Each instance keeps its schema as
  human-readable files in a local Git repository. The shipped core is the
  initial history; user edits and autonomous schema-maintainer changes each
  produce commits. A remote is optional — Git is the local version and audit
  mechanism, not a requirement to publish the schema.
- **Core**: Loreholm ships a default vocabulary of entity types and
  relations; instance-editable. Every core relation is marked stateful
  or eventive and declares its cardinality. Stateful extension relations
  begin multi-valued; this conservative default preserves competing claims
  rather than closing one incorrectly. Extraction against the core is
  classification at extract time — no vectors needed.
- **Statefulness is a core privilege.** Interval-closing runs only on
  core-marked stateful relations; it must never depend on fuzzy "is this
  the same relation?" judgments.
- **Extension tail**: extractions that don't fit the core are kept as
  namespaced `ext:*` relations, eventive-by-default point observations —
  signal in escrow, never dropped. Entity types get the same treatment:
  core types plus free-form tags.
- **Promotion is autonomous but deliberative.** Per-change user approval is
  rejected as unsustainable, especially at cold start. Schema maintenance may
  use multiple agents. A proposer clusters the extension tail (tail claims are
  embedded in the initial release for exactly this), assembles frequency and source evidence,
  and submits a structured schema change. A separate reviewer/decider
  challenges the proposal in a bounded back-and-forth, then approves or denies
  it. Approval includes a structured migration decision for existing values:
  which claims remain concurrent, close, archive, or require a maintenance
  gap. Agents decide the schema semantics; deterministic migration code
  validates and applies the approved plan transactionally.
- During development, reaching the deliberation limit without a clear
  approve/deny decision leaves the proposal unapplied and surfaces it to the
  human as `unresolved`. The review includes the proposal, evidence,
  deliberation, model assignments, token/cost usage, and points of
  disagreement so it doubles as evidence about agent effectiveness. This
  fallback is explicitly revisitable after development rather than silently
  becoming permanent policy.
- The proposal, cited evidence, deliberation, decision, and migration plan are
  stored with the resulting Git commit. User control is oversight, not
  approval busywork: Git diff/history, revert, relation pinning,
  demotion/veto, and conservative evidence thresholds. A pinned relation
  cannot be changed by the maintainer.
- Stateful and cardinality marking are autonomous too but evidence-gated
  harder: single-valued promotion needs repeated explicit supersession
  patterns, not mere recurrence. Wrongly-eventive or multi-valued is cheap to
  fix later; wrongly single-valued can close intervals it should not.
- Promotion migrates existing `ext:*` claims under the new core relation,
  record time preserved (C7 append-only). The schema itself is versioned;
  derived elements stamp the active schema Git commit alongside miner version
  (C9 extends again). The instance records which schema commit is active in
  ArcadeDB; applying a new commit and graph migration is transactional, and a
  Git revert drives a compensating migration rather than deleting history.

## Decisions

| # | Decision | Status |
|---|---|---|
| A | Instance topology | Called. Instance = ArcadeDB + miner/control service + Bifrost; spine ships to the personal instance only; sharing is an explicit, irreversible disclosure served by that instance |
| NET | Network boundary | Called. Retain the public front door, Headscale/Tailscale tunnel, containerized Tailnet identity, `:8081` application shim, implicit-deny ACL, and local-only data; V2 routes replace V1 application contracts without exposing ArcadeDB or Bifrost |
| B | Capture classes | Called. V2 initial scope = assistant transcripts + explicit push; documents/screens enter as pushed snapshots; no passive document, browser, or screen capture initially |
| C1 | Capture identity | Called with B/H. Every envelope gets a spine-minted UUIDv7 transport identity; snapshot payload versions are content-hashed with best-effort object identity as a separate layer |
| C2 | Entity identity | Called. Surrogate UUIDv7, content-free; resolution = mention NN + string similarity, thresholds with LLM middle band |
| C3 | Edge/claim identity | Called. Surrogate-ID'd claim rows; deterministic commit uses canonical entities plus relation temporal/cardinality schema; repeated support attaches Evidence |
| C4 | Provenance link model in ArcadeDB | Called. First-class Evidence records link captures to claims many-to-many and carry source span, mining lineage, lifecycle, and origin provenance |
| C5 | ID stability across re-mining | Called. Derived generations are fingerprinted, retained as paid inference, reused on exact match, and superseded only after successful scoped backfill; accreted identities remain stable |
| C6 | Merge/split semantics (supersede, alias records, un-merge) | Called. Older entity ID survives automatic merge; canonical-root resolution is transactional and cycle-free; un-merge/split remain reversible history |
| C7 | Temporal model | Called. Unknown bounds stay unknown; deterministic commit applies relation cardinality and explicit replacement signals; gaps create durable client maintenance notices |
| C8 | Cross-hop ID survival | Called. Preserve `(origin_instance_id, entity_id)` and other shared record IDs; receiving instance resolves them to local entities without erasing origin provenance |
| C9 | Miner version stamping | Called. Derived output records miner, model/config, input, and schema-commit fingerprints for reuse, scoped supersession, and audit |
| D | Schema ownership | Called. Git-versioned hybrid schema; extension tail is conservatively eventive/multi-valued; proposer and reviewer agents autonomously deliberate changes with human oversight and transactional migrations |
| E | Miner model class | Called. Bifrost is the mandatory gateway across cloud and local runtimes; humans map models to tiers/roles; advisory overrideable self-tests check capabilities |
| F | Mining trigger (per-capture / batch / idle) | Called. Session-quiescence timeout for transcripts; immediate for pushes; periodic sweep for stragglers; idempotent by re-runnability |
| G | Salience gate design | Called. Read-time mechanical trim + heuristic episode admission, no raw embeddings; pushes bypass; skip decisions are recorded and revisitable |
| H | Capture contract + versioning | Called. UUIDv7 envelope identity; queue-age-aware clock normalization; unknown classes upload into bounded quarantine; additive envelope + separate class registry |
| I | Staging layout in ArcadeDB, incl. vector usage | Direction called. Time-series events, snapshot documents with external BINARY payloads, post-mining vectors, and graph knowledge; concrete per-region schemas remain open |
| J | Retention + budget control surface | Called. Raw is retained and discoverable until user deletion; 30-day configurable grace, vector purge, claim archival, configurable secret-free backups; money budget exhaustion queues inference and notifies clients |
| K | Surfacing/query API | Direction called. Default model-facing results contain current claims plus strongest compact evidence within a token budget; deeper provenance/history is explicit. Endpoint and query-shape design remains open |
| — | Target user | Discussed (AI-native workers; homelab devs as install-capable intersection; org wedge via shared instances) but not finally called |

## Working: I (staging layout + vectors)

ArcadeDB is multi-model; the two capture kinds map onto it directly:

| Capture kind | ArcadeDB model | Why |
|---|---|---|
| Events (transcripts, event-shaped pushes) | Time series | Append-only, timestamp-ordered, session-partitioned; matches event semantics exactly |
| Object snapshots (pushed documents/screens) | Documents keyed by content hash; payload in an `EXTERNAL` `BINARY` property; object record links its version chain | Content-addressed metadata plus ArcadeDB's lazy heavy-value storage |
| Mined knowledge | Graph | Entities, claims (with C7 intervals) |
| Embeddings | Vector (HNSW) | Post-mining content only — see below |

This turns decision I from "how do we lay out staging" into "confirm the
above mapping and design each region's schema." Events-in-time-series also
gives the interpret stage its natural read pattern: episode segmentation =
range scans over session-partitioned series.

**Large payload storage is inside ArcadeDB (direction, 2026-07-10).** V2 uses
ArcadeDB's external-property storage for pushed documents, screenshots, and
other large or unknown-class payloads: the capture/snapshot document keeps
queryable metadata and a content hash while its `BINARY EXTERNAL true`
property lives in the paired external bucket and is loaded only when read.
External buckets may be placed on a separate bulk-storage volume and use
per-value compression. This keeps metadata, payload commit, crash recovery,
and cascading deletion under one transactional owner. A separate S3-compatible
object store is deferred unless realistic upload-size and streaming-memory
tests show that ArcadeDB's whole-value API is inadequate.

**Database boundary is one-to-one with the instance.** The per-region schema
below is designed for one Loreholm world in one ArcadeDB database; it does not
carry tenant columns or cross-instance records without C8 origin namespacing.

**The graph is mined, human-readable knowledge only.** Raw captures, Sessions,
mining runs, Evidence records, queue state, quarantine state, and vector
content rows remain in their document/time-series/vector regions; they do not
become graph vertices merely because ArcadeDB can link models. Provenance uses
indexed IDs across regions. This keeps graph browsing about people, things,
claims, and their understandable relationships rather than pipeline internals.
- The graph has one physical `Entity` vertex type. Person, Organization,
  Project, Place, and future semantic entity types are Git-versioned schema
  values on Entity records, not ArcadeDB vertex subtypes. Schema evolution can
  therefore add or revise semantic types without graph DDL or vertex migration.
- Entities may carry several semantic types and have no mandatory primary
  type. Each `instance_of` assertion is an ordinary evidence-backed Claim, so
  its provenance, supersession, and history remain visible. Entity also keeps
  an indexed derived `types` set containing its current active type assertions
  for efficient AI lookup; it is a rebuildable cache, not separate truth. A UI
  may choose a display type without changing graph semantics.
- Names and aliases use the same hybrid pattern. Evidence-backed `name` and
  `alias` Claims are historical truth, including temporal bounds and
  supersession. Entity stores an indexed derived `display_name` plus normalized
  active-name/alias set for resolution and lookup. Corrections, merge reversal,
  and claim archival rebuild that cache from active claims; cached strings do
  not acquire independent provenance or permanence.
- Claims are graph vertices, not direct entity-to-entity edges. A Claim vertex
  carries the relation, temporal bounds/constraints, lifecycle, schema commit,
  and record time, and connects to its subject and object through readable
  graph edges. This makes claim identity and history explicit without turning
  evidence or pipeline state into graph topology.
- Relation schema declares the claim object kind. Entity-valued claims connect
  the Claim vertex to an Entity vertex; literal-valued claims store a typed
  value on the Claim (`value_type` plus the matching date/number/string/boolean
  field). Composite indexes on relation and typed value support reverse and
  range lookup without filling the graph with literal vertices.
- The extractor does not invent object-kind policy per claim. Core relations
  supply it. For `ext:*`, deterministically recognizable scalars begin as typed
  literals, identity-bearing named mentions become entity candidates, and
  ambiguity preserves the original surface form and mention link without a
  permanent choice. Schema promotion decides and Git-versions the object kind;
  its approved migration plan resolves prior extension values.
- Model-generated confidence is not stored on claims or Evidence and is not
  returned as factual confidence by the query API. It is an uncalibrated
  hallucinated number. Measured matcher similarity/distance and deterministic
  validation outcomes may guide internal workflows, but they remain named
  operational signals rather than claims about truth.

**Capture type hierarchy.** All stored envelopes share a common ArcadeDB
`Capture` document type containing the stable contract fields (`capture_id`,
`class`, `surface`, session reference, device and normalized time,
`received_at`, refs/hints, identity/version metadata, policy version, and
lifecycle state). `EventCapture EXTENDS Capture` and `SnapshotCapture EXTENDS
Capture` carry the two identity/storage regimes from B. Inheritance keeps
cross-class inventory, retention, provenance, and quarantine queries
polymorphic while allowing regime-specific constraints and indexes below the
parent.
- Inheritance describes physical placement and storage behavior, not every
  semantic media/use case. `class`, `media_type`, payload encoding, and the
  versioned class-registry entry tell Loreholm how to validate, interpret,
  permission, and mine the capture. New semantic classes therefore do not
  require ArcadeDB DDL; they fit an existing storage subtype and remain
  quarantined until the instance understands their registry definition.
- All capture payloads use external properties initially, independent of
  event/snapshot kind or text/JSON/binary encoding. This keeps polymorphic
  inventory and queue scans compact, makes large and unknown payloads safe by
  default, and gives payload storage one placement rule. The extra lazy lookup
  is accepted; an inline fast path requires measurement before adding a second
  representation.
- Transcript mining is session-granular, not message-job-granular. Individual
  messages remain immutable `EventCapture` records with stable retry identity,
  but the queue points to a `session_ref`; interpret performs one batched range
  read for the session and projects the admitted external payloads together.
  Miner APIs must avoid one network round trip per message.
- `Session` is a first-class ArcadeDB record and the durable transcript/mining
  unit. It owns the stable server session ID, surface and native session ref,
  first/last normalized activity, idle deadline, lifecycle/mining state, and
  links to mining runs and maintenance notices. It does not duplicate an
  assembled transcript payload; message captures remain the raw source of
  truth. When an adapter has no native session, interpret creates a stable
  server-owned Session and assigns captures under the server-side
  sessionization decision.
- `EventCapture.session_id` is an indexed property, not a graph edge. The
  transcript read path filters by `session_id` and orders by normalized time
  (with capture identity as the stable tie-breaker), matching the miner's
  batch range-read workload without polluting the knowledge graph.

**Vector region is post-mining (direction, 2026-07-08).** Raw captures are
never embedded. The pipeline runs: raw staging -> interpret/mine for
content (mentions, candidate claims, episode summaries) -> embed that
content into the vector region -> mine the vector space -> graph commit.
Consequences:

- Embedding cost sits behind the salience gate and the interpret stage —
  consistent with "capture liberally, interpret selectively."
- Vector entries are keyed by content-row ID, so provenance chains
  graph element -> content row -> raw capture without a separate link type
  for vectors.
- Embeddings are derived artifacts: re-derivable and miner-version-stamped
  (C9 extends to content and vectors), but retained and reused as paid
  inference until superseded or removed by the source-deletion policy.
- C2 sharpens: entity resolution = mining the vector space over extracted
  mentions (cluster/nearest-neighbor for candidates), surrogate ID minted
  at graph commit.

**What goes raw -> vector.** The extract stage can emit three unit types;
each has a different consumer, so each earns (or doesn't earn) its
embedding separately:

| Unit | What it is | Example | Consumer | Verdict |
|---|---|---|---|---|
| Mentions | Entity reference + context window, linked to its episode | "Sarah" in "Sarah signed off on the OIDC migration" — later clustered with "S. Chen" and "sarah@..." into one entity | C2 matcher: cluster/NN over mention space = entity resolution | Embed initially — this is the identity workload, the reason the vector region exists |
| Candidate claims | Extracted (subject, relation, object) + source text | works_at(Kevin, Acme) from "started at Acme last month"; embedding would let "employed by" and "works at" land as one relation | Relation canonicalization and contradiction candidates | Settled by D: core-fitting claims are classified at extract time, no embedding; extension-tail (ext:*) claims embed initially to feed schema-proposal clustering |
| Episode summaries | One per episode | "Debugged the ArcadeDB uninstall script; decided to keep user data by default" — answers a later "when did I change the uninstall behavior?" | Surfacing/recall (K), not mining | Defer to K; nothing in the mining path reads them |

Raw chunks are excluded by construction — embedding them would be
embedding pre-mining data.

**Mention vector granularity: mention-in-context, span-marked.** The
embedded text is the context window with the mention span delimited
(e.g. `... [Sarah] signed off on the OIDC migration ...`), not the
surface form alone and not the bare window:

- Surface form alone can't do the job in either direction: "Sarah" embeds
  identically everywhere, so it can't split two different Sarahs, and
  "Sarah" vs "S. Chen" share no string, so it can't merge them — context
  is the identity signal.
- An unmarked window is ambiguous when it contains several mentions; the
  marking makes the vector about this one.
- The surface form still travels with the row as structured metadata
  (surface, span offsets, content-row ref), so the matcher combines
  string similarity and vector NN as complementary signals.

Vector-mining scope (updated with D): initial residents are mentions plus
extension-tail claims. Vector-stage mining is two workloads — identity
(C2 resolution over mentions) and promotion clustering (the schema
maintainer over ext:* claims). The two populations stay in separate
spaces; nothing matches a mention against a claim.

## Dependency spine

A through H and C1-C9 are called at the semantic level. J's retention,
backup, deletion, and money-budget behavior is called; K's default
model-facing result is called. I's concrete ArcadeDB types, properties,
buckets, and indexes remain the main open architecture work. K still needs
endpoint and request/response schemas, and implementation specifications are
needed for the capture envelope, policy/status sync, maintenance notices, and
backup coordinator. These are design elaboration against settled boundaries,
not unresolved dependencies among the core decisions.
