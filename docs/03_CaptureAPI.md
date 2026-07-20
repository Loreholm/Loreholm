# Loreholm capture API

**Status:** Implemented foundation contract. Adapter/spine automation is
planned; the HTTP ingestion and persistence path exists now.

Loreholm clients submit observed context, not graph facts. The instance accepts raw
events and snapshots, stores them idempotently, and will eventually mine them
into knowledge server-side.

## Authentication

`GET /v2/policy`, `POST /v2/captures`, and `POST /v2/query` require the device
bearer token:

```http
Authorization: Bearer <LOREHOLM_V2_DEVICE_TOKEN>
```

The installer stores the raw token in
`~/.local/share/loreholm-v2/state/instance.env` and passes only its SHA-256
digest to the instance process.

## Read instance policy

```http
GET /v2/policy
```

Example response:

```json
{
  "version": 1,
  "contract_min": "2.0",
  "contract_max": "2.0",
  "classes": {
    "transcript.message": {
      "capture": true,
      "remote_processing": "sanitized_remote"
    },
    "push.document": {
      "capture": true,
      "remote_processing": "sanitized_remote"
    },
    "push.screen": {
      "capture": true,
      "remote_processing": "sanitized_remote"
    }
  },
  "mining_status": "paused"
}
```

Clients should refresh policy before uploading an offline queue. The instance
also enforces `capture: false` as a defense-in-depth boundary. The planned spine
will enforce the same rule locally and may always make instance policy stricter
through a user pause or surface-specific control.

## Query grounded knowledge

```http
POST /v2/query
Content-Type: application/json
```

The natural-language query is embedded through Bifrost and searched against
the mention-in-context vector region. Matching mentions are grouped by their
resolved entity, a strict planner selects only returned entity IDs and
registered relations, and Loreholm performs a bounded one-hop Claim traversal.
The operation is read-only and never mints an entity.

Example request:

```json
{
  "query": "Where is the deployment data stored?",
  "as_of": "2026-07-19T14:30:00Z",
  "include_history": false,
  "answer": true,
  "candidate_limit": 10,
  "minimum_seed_score": 0.45,
  "max_claims": 20,
  "max_evidence_per_claim": 3,
  "token_budget": 1800,
  "ambiguity_margin": 0.03
}
```

Normal reads return only active Claims and Evidence applicable at `as_of`.
`include_history` also permits superseded records but never returns deleted
records. Evidence excerpts are reconstructed from the original capture and its
stored source offsets. The response contains:

- `status`: `ok`, `no_match`, or `no_evidence`;
- optional synthesized `answer` and its validated `[E#]` `citations`;
- ranked vector `seeds`, including the graph entities selected by the planner;
- structured `claims` and source-linked `evidence`;
- explicit ambiguity, temporal, conflict, extension, policy, and integrity
  `warnings`; and
- a `trace` with the embedding model, schema version, effective traversal,
  `as_of`, truncation, and synthesis state.

Set `answer` to `false` to receive the deterministic grounding bundle without
a chat-completion call. A remote planner requires seed sources permitted for
`derived_only` or `unrestricted` processing. Remote cited synthesis includes
raw Evidence excerpts and requires `unrestricted` for every source capture.

## Submit captures

```http
POST /v2/captures
Content-Type: application/json
```

The request is a batch of 1–250 envelopes:

```json
{
  "captures": [
    {
      "capture_id": "018f5e2a-1234-7abc-8def-1234567890ab",
      "kind": "event",
      "class": "transcript.message",
      "surface": "example.chat",
      "session_ref": "conversation-42",
      "occurred_at": "2026-07-19T14:30:00Z",
      "payload": {
        "role": "user",
        "content": "Remember that the network boundary stays the same."
      },
      "refs": [],
      "hints": [],
      "meta": {
        "contract_version": "2.0",
        "spine_version": "2.0.0",
        "adapter_id": "example-chat",
        "adapter_version": "2.0.0",
        "device_id": "device-7",
        "user_id": "user-9",
        "queue_age_seconds": 0,
        "policy_version": 1
      }
    }
  ]
}
```

Example response:

```json
{
  "receipts": [
    {
      "capture_id": "018f5e2a-1234-7abc-8def-1234567890ab",
      "status": "accepted",
      "received_at": "2026-07-19T14:30:01Z",
      "normalized_at": "2026-07-19T14:30:00Z",
      "clock_adjusted": false
    }
  ]
}
```

## Envelope fields

| Field | Required | Current rule |
|---|---|---|
| `capture_id` | Yes | Lowercase UUIDv7; stable across delivery retries |
| `kind` | Yes | `event` or `snapshot` |
| `class` | Yes | Lowercase dotted identifier, 3–64 characters |
| `surface` | Yes | Source application or integration, up to 128 characters |
| `session_ref` | No | Adapter-native session/object reference, up to 512 characters |
| `occurred_at` | Yes | Timezone-aware timestamp, normalized to UTC |
| `payload` | Yes | Class-specific raw JSON payload |
| `refs` | No | At most 100 source references |
| `hints` | No | At most 100 non-authoritative adapter hints |
| `meta` | Yes | Contract, adapter, device, user, queue, and policy metadata |

Unknown top-level and metadata fields are currently rejected. Future
contract evolution therefore requires an explicit compatible model change; an
adapter must not assume arbitrary envelope extensions will be ignored.

## Event and snapshot identity

Events are occurrences. Two identical messages sent twice are different events
and require different UUIDv7 capture IDs.

Snapshots observe a stateful object version. In addition to a unique capture
ID, their payload must contain:

```json
{"content_hash": "sha256:<64 lowercase-or-uppercase hexadecimal characters>"}
```

The capture ID identifies the act of observing or pushing the object. The hash
identifies its content version. Best-effort object identity, such as a path,
URL, or document ID, belongs in the payload or references.

## Receipt states

| Status | Meaning |
|---|---|
| `accepted` | The class is known to policy and the capture was stored as `staged` |
| `duplicate` | That `capture_id` was already stored; the retry made no new record |
| `quarantined` | The class is unknown and was stored as `quarantined_unknown_class` |
| `policy_blocked` | The class is known but disabled; the instance stored no copy |

`policy_blocked` is a per-capture receipt rather than a batch-level HTTP error,
so a mixed batch can acknowledge permitted captures while telling the client to
retain or delete blocked material locally.

Unknown classes are deliberately durable. They cannot enter interpretation,
mining, sharing, or model egress until the instance supports their class.

## Clock normalization

The instance compares receipt time with:

```text
occurred_at + queue_age_seconds
```

If the difference exceeds six hours, `normalized_at` becomes receipt time minus
queue age and `clock_adjusted` is true. The original device timestamp remains
inside the stored envelope.

## Idempotency and ordering

- Retry an envelope with the same `capture_id` until a receipt is received.
- Never mint a new ID merely because delivery timed out.
- Do not use payload equality to deduplicate events.
- Offline delivery may arrive out of order; preserve occurrence time and queue
  age rather than rewriting history in the adapter.

## Current storage representation

The foundation stores each complete envelope as JSON in an ArcadeDB
`V2Capture` document with indexed identity, class, kind, state, surface,
session reference, device time, receipt time, and normalized time. Accepted
transcript captures also assemble into `V2Session` records through idempotent
`V2SessionCapture` membership. Each session owns a generation-numbered
`V2WorkItem` that becomes available after the last received capture has been
quiet for the configured interval. Explicit pushes receive an immediately
available admission item.

Work claims use fixed-duration, reclaimable leases. An expired lease can be
claimed by another worker, while completion and retry require the recorded
lease owner. The current store contract does not provide an active lease-renewal
operation. A scheduled instance worker consumes ready items into durable
`V2SalienceRecord` documents, schedules admitted records onto independent
`V2MiningWork` leases, then completes the admission lease. Paused mining leaves
that durable work unclaimed. Active mining performs policy-gated structured
extraction through Bifrost.

The accepted Loreholm storage design later divides events, snapshots, external
payloads, sessions, derived content, vectors, and graph knowledge into the
appropriate ArcadeDB models. The session, work-item, and salience-record
document types implement the admission and deterministic-gating slice of that
design. `V2MiningWork` provides recoverable inference claims, while
`V2MiningRun` stores validated candidate output, compatible predecessor
lineage, covered captures, and the captures eligible to become new evidence.
The production event, snapshot, broader derived, vector, and graph layout
remains open implementation work.

## Errors

| HTTP status | Meaning |
|---|---|
| `401` | Missing or invalid device bearer token |
| `422` | Envelope validation failed or batch size is invalid |
| `503` | Device authentication or ArcadeDB storage is not configured |

Storage or upstream failures may currently surface as server errors. Clients
should retain unacknowledged captures and retry with the same IDs.

## Source of truth

- `api/app/v2/models.py` — request and response validation
- `api/app/v2/router.py` — policy and capture routes
- `api/app/v2/service.py` — policy enforcement, idempotency, clock normalization,
  quarantine, session assembly, admission work, and persistence
- `api/app/v2/salience.py` — mechanical trimming, salience evaluation, and work consumption
- `api/app/v2/mining.py` — incremental mining input, predecessor reuse, and evidence boundaries
