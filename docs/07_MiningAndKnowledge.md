# Loreholm mining and knowledge model

**Status:** Accepted design, partially implemented. Raw capture staging,
quarantine, durable session assembly, quiescence, immediate push admission, and
the leased admission and mining work queues exist. Mechanical trimming,
scheduled work consumption, durable salience decisions, policy-gated structured
extraction through Bifrost, mention vector storage, entity resolution, and
schema-backed Claim/Evidence commit, maintenance notices, scoped re-mining,
compensating supersession, extension-schema maintenance, and vector-seeded
grounded query/surfacing also exist.

## The idea in plain language

Loreholm does not use a vector store as a dumping ground for every source
chunk. It first preserves the raw context, then gives specialized mining stages
the job of deciding what is salient, which people or concepts are mentioned,
what may be a claim, when it applies, and where its evidence lives.

Only selected derived records receive embeddings. Those vector indexes help
find candidate matches—for example, whether a mention may refer to an existing
entity—but they do not decide graph truth. A graph-maintenance stage starts
with richer material: source and time metadata, candidate claims, identity
matches, existing schema, existing knowledge, and provenance.

The final write is still validated deterministically. An LLM can propose an
interpretation or help judge an ambiguous identity, but it cannot bypass schema,
idempotency, evidence, lifecycle, or instance policy. Exact retries reuse prior
work, and an independently repeated observation should strengthen the evidence
for one claim instead of polluting the graph with cloned facts.

## Pipeline

```text
raw captures
    |
    v
session/object assembly
    |
    v
durable admission work
    |
    v
mechanical trim + salience admission
    |
    v
interpret: episodes, mentions, temporal signals
    |
    v
mine: candidates, resolution, schema classification
    |
    v
deterministic validation and graph commit
    |
    v
claims + entities + evidence
    |
    v
query embedding -> mention-vector seed -> bounded Claim traversal
    |
    v
source-span Evidence -> optional cited answer
```

The observer and maintainer are one instance-owned agent expressed as
separately re-runnable stages. No external client writes graph facts.

## Triggering work

The implemented admission and salience boundary:

- makes transcript-session work available after a configurable quiet period;
- makes explicit-push work available immediately;
- lets the scheduled instance worker claim ready or expired work with a
  fixed-duration lease;
- persists one idempotent salience record before completing each admission item;
- schedules admitted records onto a separate, recoverable mining lease; and
- backfills mining work for admitted records created by older releases.

A periodic straggler sweep remains planned. Schema maintenance currently runs
at commit time for conservative extension admission and through explicit
authenticated operator actions for promotion and revert.

The durable unit for transcript processing is a session, not an individual
message job. Messages remain immutable captures; the salience worker reads each
generation in normalized-time order with capture identity as tie-breaker. The
implemented `structured-extractor-v1` worker consumes admitted derived records
when mining is active.

## Structured extraction

The first model-backed stage sends bounded, policy-permitted input only through
Bifrost. It requests strict JSON for episodes, mentions, temporal bounds, and
candidate claims. Every candidate carries an exact source quote with character
offsets. Pydantic validation rejects extra or malformed fields, every cited
capture ID must belong to the incremental delta rather than the context-only
tail, and the worker verifies the quote against that bounded delta text. Valid
output is stored in `V2MiningRun`; malformed output and
gateway failures are recorded and the owned mining work returns for delayed
retry. Exact successful work is reused without another model call.

Administrators can inspect the newest candidate or failure records through
`GET /v2/admin/mining/runs?limit=50`. This local authenticated endpoint exposes
versioned extraction output and lineage; it is not a graph query or recall API.

The configured endpoint declares whether processing is local to the instance
or remote. Local extraction still honors a currently disabled capture class.
Remote extraction currently permits raw input only for classes set to
`unrestricted`; `local_only`, `sanitized_remote`, and `derived_only` fail
closed because sanitizer and local-derived transformations do not exist yet.

## Implemented entity resolution

After a successful extraction, `entity-resolver-v1` materializes each new
mention as a durable `V2Mention` document. Its mention-in-context text is sent
through Bifrost's embedding endpoint, normalized to unit length, and stored in
a persistent ArcadeDB cosine HNSW region. Raw captures are never embedded.
Each vector region is locked to one provider/model name and dimension count;
changing either requires a new region so incomparable vector spaces are never
silently mixed.

Resolution first reuses an exact normalized surface and entity-type match. For
other mentions it retrieves same-type mention neighbors, combines cosine and
string similarity, accepts a match above the high threshold, and mints a new
content-free `V2Entity` vertex below the low threshold. The configurable middle
band asks the selected inference model through Bifrost to choose only from the
retrieved entity IDs or mint a new entity. A judgment that names any other ID
is rejected and the owned mining work is retried.

Every resolved mention records its extraction run, exact capture span,
embedding model, resolver version, method, scores, thresholds, retrieved
candidate set, and optional model judgment. Mention and mint keys make the
stage idempotent after partial failure: a retry reuses already written mentions
and cannot mint a second entity for the same mention. Mining work completes
only after all extracted mentions have resolved successfully.
An idempotent `V2ResolutionRun` marker covers zero-mention output as well as
populated runs. Startup backfill reopens successful extraction work created by
older releases only when that marker is absent.
Administrators can inspect recent decisions and entities through authenticated
`GET /v2/admin/resolution/mentions` and
`GET /v2/admin/resolution/entities` endpoints. These are audit views, not the
grounded recall API.

The resolver rechecks current capture policy before embedding. Local processing
requires the source class to remain enabled. Remote derived resolution requires
`derived_only` or `unrestricted`; the current combined extraction/resolution
run still requires `unrestricted` because raw extraction input leaves first.

## Salience gate

The implemented initial gate uses no LLM and no raw embeddings.

1. Mechanical trim removes or truncates tool noise, repeated retry loops, and
   oversized prompt material at read time without deleting stored bytes.
2. Heuristic episode admission uses user-authored volume and turn count, records
   the source surface with those signals, and lets explicit pushes bypass the
   threshold.

Below-threshold sessions are marked skipped, not discarded. Future reprocessing
with a different algorithm version or threshold can reconsider them. The current
`mechanical-salience-v1` gate removes `tool` and `function` roles, exact
whitespace-normalized repeats, and text beyond its per-capture and per-scope
bounds. It admits transcript work with at least 40 retained user-authored
characters or at least three retained turns including a user turn. The record
stores signals, thresholds, bounded derived text, and an input fingerprint;
the original `V2Capture` bytes are unchanged.

## Raw, derived, and accreted regions

| Regime | Examples | Lifecycle |
|---|---|---|
| Raw | Captures, sessions, source payloads | Capture bytes stay immutable; session aggregates advance as immutable members arrive |
| Derived | Episodes, mentions, candidate claims, embeddings | Miner-versioned, reusable, and supersedable |
| Accreted | Entities and committed claims | Stable identities that accumulate evidence and reversible history |

Re-mining does not drop stable entities. It creates new derived material that
resolves against the existing entity registry.

## Entity identity

Entities use content-free surrogate UUIDv7 IDs. Names, aliases, and types are
evidence-backed claims, not the entity's identity.

The implemented resolver uses:

- nearest-neighbor search over mention-in-context vectors;
- string similarity over surface forms;
- automatic acceptance above a high threshold;
- automatic minting below a low threshold; and
- an LLM judge for the configurable middle band.

Merge, unmerge, and split operations remain planned. Their accepted behavior
keeps the older canonical entity, records `merged_into` supersession rather
than erasing either identity, and repartitions mentions without rewriting
source provenance.

## Claims and time

Claims also use surrogate identities. A claim records:

```text
(subject, relation, object, valid_from?, valid_to?, recorded_at)
```

Unknown temporal bounds remain unknown. Capture time is not substituted for
missing fact time. Relation schema declares whether a relation is stateful or
eventive, its cardinality, and its object kind.

For single-valued stateful relations, ambiguity preserves competing claims.
Missing temporal bounds and competing active values create idempotent
`V2MaintenanceNotice` records. The field console surfaces that ledger without
silently choosing a winner or inventing dates.

## Provenance

Evidence is a first-class record connecting captures and claims by indexed IDs:

```text
Capture <- Evidence -> Claim
```

One capture may support several claims, and one claim may have evidence from
several independent captures. Evidence records include source span/reference,
claim ID, mining operation, miner and schema versions, lifecycle, and record
time. Source excerpts remain capture-derived rather than permanent duplicate
content.

The same claim observed in a new capture adds evidence. It is not treated as an
inference retry.

The implemented `claim-committer-v1` loads the shipped
`api/app/v2/relation_schema.json` vocabulary. It resolves entity-valued claim
subjects and objects only through mentions resolved in the same mining run,
checks entity types, object kind, statefulness, cardinality, temporal order, and
the run's evidence boundary, then validates every candidate before making the
first graph write. Unknown relations fail closed. Semantic Claim keys collapse
the same subject, relation, object, and time meaning; Evidence keys collapse the
same claim/capture/span support while allowing a new capture to add independent
support. Storage writes and the final `V2ClaimCommitRun` marker are idempotent,
so a partial infrastructure failure can safely retry.

Administrators can audit the vocabulary and committed records through
`GET /v2/admin/knowledge/schema`, `GET /v2/admin/knowledge/claims`, and
`GET /v2/admin/knowledge/evidence`. These are inspection endpoints, not the
grounded recall API described below.

## Idempotency and re-mining

The mining-run foundation is used by the model-backed extractor. Before
inference, the stage searches for a successful result matching:

- source scope;
- stage and miner version;
- model/configuration fingerprint; and
- input fingerprint.

Exact retries reuse the `V2MiningRun` output and stable run key. For a later
session generation, `MiningRunCoordinator` selects the latest earlier
successful run only when its stage, miner version, and configuration fingerprint
are compatible. It passes the prior structured output, new captures after the
predecessor's coverage boundary, and a bounded tail of preceding turns needed
to understand replies such as “yes.” Those preceding turns are context-only;
the coordinator permits only delta capture IDs to be recorded as new evidence.

A changed miner or configuration deliberately falls back to the full admitted
generation. Earlier successful output remains available; maintenance marks only
the source run's unsupported output superseded after its replacement succeeds.
Failed or partial backfills never hide prior successful knowledge.

The implemented maintenance API makes that replacement boundary explicit.
`POST /v2/admin/maintenance/remining` targets one successful source run and
reopens its durable mining work with a unique reprocess token. The old Claims
and Evidence remain active during extraction, resolution, validation, and
commit. Only after the replacement commit succeeds does Loreholm supersede
active Evidence owned by the source run whose Claims are absent from the
replacement. A Claim becomes superseded only when it has no other active
Evidence, so independent support is never retired with one inference lineage.
The request and a resolved maintenance notice preserve the source run,
replacement run, reason, scope, and affected Claim IDs.

## ArcadeDB model

The accepted direction uses ArcadeDB's models according to workload:

| Content | ArcadeDB model |
|---|---|
| Transcript and other events | Time series, partitioned by session |
| Snapshot metadata and version chain | Documents |
| Large capture payloads | External binary properties owned by ArcadeDB |
| Mentions and extension-tail embeddings | Separate HNSW vector regions |
| Human-readable entities and claims | Graph |
| Runs, evidence, queues, and quarantine | Documents/time series, not graph vertices |

The current foundation uses an append-only `V2Capture` document for each raw
envelope, mutable `V2Session` aggregates, idempotent `V2SessionCapture`
membership documents, generation-numbered `V2WorkItem` documents,
`V2SalienceRecord` derived gates, and reusable `V2MiningRun` output documents.
It also uses `V2Mention` derived documents with a persistent cosine vector
index, stable `V2Entity` vertices, `V2Claim` vertices, indexed `V2Evidence`
documents, per-run commit markers, `V2MaintenanceNotice`, `V2ReminingRequest`,
and `V2ExtensionRelation` documents. The expanded production layout still
needs concrete event time-series, snapshot, external-payload types, buckets,
and indexes.

## Vector policy

Raw captures are never embedded. Embeddings are created only after admission
and interpretation:

- mentions in marked context are embedded for entity resolution;
- extension-tail claims are embedded for schema-promotion clustering;
- core-fitting claims are classified directly and need no embedding;
- episode-summary retrieval remains an open surfacing choice.

Vector rows are derived artifacts keyed to content rows so their provenance
and deletion path lead back to raw captures.

## Schema ownership

Each instance owns a human-readable Git-versioned schema:

- a shipped, editable core vocabulary;
- a conservative `ext:*` tail for observations that do not fit the core; and
- autonomous proposer/reviewer maintenance with human oversight.

Core relations declare statefulness, cardinality, and object kind. Extension
relations begin conservatively eventive and multi-valued. Approved promotions
include a deterministic migration plan and Git commit; revert drives a
compensating migration rather than deleting history.

The current executable core schema is the shipped `core-v1` JSON file. It contains
the initial `created`, `depends_on`, `located_in`, `member_of`, `owns`,
`storage_location`, `uses`, and `works_for` relations. Operators can review or
version that file with their deployment source. An extracted `ext:*` relation
that does not yet exist is admitted deterministically with the entity types
actually present in the run and conservative eventive, multi-valued semantics.
It receives a content-derived schema version and a reviewable migration plan.

An authenticated operator may promote it only to a compatible shipped core
relation and must record a Git commit hash. Promotion records an
alias-then-reprocess plan and keeps existing extension Claims active until safe
re-mining replaces them. Revert records another Git commit and applies a
compensating supersession to the extension's Claims and Evidence; it never
deletes history. Non-`ext:*` unknown relations still fail closed before graph
writes.

## Grounded query and surfacing

The authenticated `POST /v2/query` endpoint implements a read-only grounded
query path. A natural-language question is embedded through the configured
Bifrost embedding route with the same model and width locked to the
`V2Mention` vector region. ArcadeDB nearest-neighbor search returns mention
hits, groups them by their already-resolved `entity_id`, and ranks candidate
graph seeds from their strongest and supporting hits. Querying never mints an
entity or changes graph state.

A strict Bifrost planner may select only those returned entity IDs, registered
core or active extension relations, and an incoming, outgoing, or bidirectional
one-hop traversal. Loreholm rejects invented planner output before querying the
graph. It then:

1. reads adjacent Claims for the selected seed entities;
2. excludes deleted Claims in every mode and excludes superseded Claims from
   normal reads;
3. applies `valid_from` and `valid_to` to the request's timezone-aware `as_of`;
4. loads active Evidence, rechecks current capture policy, and reconstructs
   exact excerpts from raw capture bytes and stored source offsets;
5. ranks independently supported Claims and packs representative Evidence
   within the requested token budget; and
6. optionally asks Bifrost for a strict cited answer whose `[E#]` handles must
   match Evidence in the returned bundle.

The structured Claims and Evidence remain available when answer synthesis is
not requested. The response also includes all vector seed candidates, which
ones the planner selected, the effective traversal, schema and embedding
versions, truncation state, and explicit warnings for near-tied seeds,
incomplete temporal bounds, competing single-valued Claims, extension
relations, missing captures, invalid spans, or disabled capture classes.

Remote query planning receives the question and derived entity candidates, so
every seed source must permit `derived_only` or `unrestricted` processing.
Remote answer synthesis additionally receives raw Evidence excerpts and
therefore requires `unrestricted` for every included capture. Both calls stay
inside the instance when their configured processing location is local.

The initial implementation deliberately supports bounded one-hop Claim
traversal. Multi-hop path planning, episode-summary vectors, broad transcript
recall, and a correction interface remain later work.

## Implementation milestones

A practical sequence is:

1. production capture/session storage schema — foundation documents, session
   scope, and membership implemented; expanded production layout planned;
2. durable session quiescence and work queue — scheduling, leases, recovery,
   completion, delayed retry, and the scheduled consumer implemented; periodic
   sweep planned;
3. mechanical trim and salience records — implemented;
4. mining-run identity, compatible predecessor selection, reusable output
   store, incremental delta, and context-only evidence boundary — implemented;
5. mention and candidate-claim extraction through Bifrost — implemented;
6. mention vector region and entity resolution — implemented; merge/unmerge,
   split, and multi-region migration remain planned;
7. schema-backed deterministic claim commit and Evidence records — implemented;
8. maintenance notices, re-mining, scoped supersession, and extension-schema
   admission/promotion/revert — implemented; and
9. vector-seeded grounded query/surfacing API with Evidence hydration and
   optional cited synthesis — implemented; multi-hop and episode recall remain
   planned.
