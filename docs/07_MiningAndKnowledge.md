# Loreholm mining and knowledge model

**Status:** Accepted design, partially implemented. Raw capture staging,
quarantine, durable session assembly, quiescence, immediate push admission, and
the leased admission and mining work queues exist. Mechanical trimming,
scheduled work consumption, durable salience decisions, policy-gated structured
extraction through Bifrost, mention vector storage, and entity resolution also
exist. Schema-backed claim/Evidence commit and surfacing do not.

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

A periodic straggler sweep and slower independent schema-maintenance cadence
remain planned.

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
planned grounded recall API.

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

For single-valued stateful relations, prior intervals close only on an explicit
replacement signal. Ambiguity preserves competing claims. Missing temporal
bounds create maintenance notices that a future client may surface naturally
and sparingly.

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
generation. Earlier successful output remains available; future graph stages
must mark only covered output superseded after its replacement succeeds. Failed
or partial backfills must never hide prior successful knowledge.

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
index and stable `V2Entity` vertices. The expanded production layout still
needs concrete event time-series, snapshot, external-payload, claim, Evidence,
and maintenance types, buckets, and indexes.

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

## Surfacing direction

The default model-facing result will contain current claims plus the strongest
compact supporting evidence within a token budget. Archived and superseded
claims are excluded from normal reads. Full provenance, additional evidence,
history, and earlier generations require explicit query options.

Endpoint and request/response schemas for surfacing remain undecided and must
not be inferred from retired search-tool behavior.

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
7. schema-backed deterministic claim commit and Evidence records;
8. maintenance notices, re-mining, and scoped supersession; and
9. grounded query/surfacing API.
