# Loreholm mining and knowledge model

**Status:** Accepted design, not implemented in the foundation milestone. The
current code stops after raw capture staging and quarantine.

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

- Transcript sessions trigger after a configurable inactivity timeout.
- Explicit pushes trigger immediately.
- A periodic sweep catches out-of-order arrivals and interrupted work.
- Schema maintenance runs on a slower independent cadence.

The durable unit for transcript processing is a session, not an individual
message job. Messages remain immutable captures; interpretation reads a
session range ordered by normalized time with capture identity as tie-breaker.

## Salience gate

The initial gate uses no LLM and no raw embeddings.

1. Mechanical trim removes or truncates tool noise, repeated retry loops, and
   oversized prompt material at read time without deleting stored bytes.
2. Heuristic episode admission considers signals such as user-authored volume,
   turn count, and surface. Explicit pushes bypass the threshold.

Below-threshold sessions are marked skipped, not discarded. A later run with a
different threshold can reconsider them.

## Raw, derived, and accreted regions

| Regime | Examples | Lifecycle |
|---|---|---|
| Raw | Captures, sessions, source payloads | Immutable source material retained until user deletion |
| Derived | Episodes, mentions, candidate claims, embeddings | Miner-versioned, reusable, and supersedable |
| Accreted | Entities and committed claims | Stable identities that accumulate evidence and reversible history |

Re-mining does not drop stable entities. It creates new derived material that
resolves against the existing entity registry.

## Entity identity

Entities use content-free surrogate UUIDv7 IDs. Names, aliases, and types are
evidence-backed claims, not the entity's identity.

Each extracted mention is resolved using:

- nearest-neighbor search over mention-in-context vectors;
- string similarity over surface forms;
- automatic acceptance above a high threshold;
- automatic minting below a low threshold; and
- an LLM judge for the configurable middle band.

Automatic merge keeps the older canonical entity and appends a `merged_into`
supersession relationship. Unmerge closes that relationship. Split
repartitions mentions rather than rewriting source provenance.

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

Before paid inference, a stage searches for a successful result matching:

- source scope;
- stage and miner version;
- model/configuration fingerprint; and
- input fingerprint.

Exact retries reuse output and stable operation identity. A new miner or
configuration creates a new generation. Earlier successful output remains
active until the replacement succeeds, after which only covered output is
marked superseded. Failed or partial backfills never hide prior successful
knowledge.

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

The current `V2Capture` foundation is a simpler append-only document schema.
Concrete production types, properties, buckets, and indexes for the expanded
layout remain open implementation work.

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

1. production capture/session storage schema;
2. durable session quiescence and work queue;
3. mechanical trim and salience records;
4. mining-run identity and reusable output store;
5. mention and candidate-claim extraction through Bifrost;
6. vector regions and entity resolution;
7. schema-backed deterministic claim commit and Evidence records;
8. maintenance notices, re-mining, and scoped supersession; and
9. grounded query/surfacing API.
