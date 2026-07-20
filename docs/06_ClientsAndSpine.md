# Loreholm clients, adapters, and embedded spine

**Status:** Accepted design. No general-purpose adapter or reusable spine is
implemented yet. Browser chat directly exercises the capture service and is
the first surface-specific proof.

## Responsibility split

```text
surface adapter       embedded spine             instance
---------------       --------------             --------
map native events --> identity and queue -----> durable capture
no interpretation     policy and redaction       interpretation
no graph writes       authenticated delivery     mining and graph commit
```

The client is a sensor. It records what occurred and may attach source-native
references or hints, but it does not decide which entities, relationships, or
claims are true.

## Adapter responsibilities

An adapter is a thin integration for one surface such as an IDE assistant,
browser chat, mobile client, or explicit share action. It should:

- map native events or pushed objects into a supported capture class;
- preserve native session and object references without interpreting them;
- pass raw content and source metadata to the spine;
- expose a visible capture indicator and one-gesture pause; and
- avoid summarization, semantic deduplication, entity extraction, or graph
  vocabulary.

Surface-specific normalization such as mapping a native role to `user` or
`assistant` is appropriate. Deciding that a message establishes a `works_at`
claim is not.

## Spine responsibilities

The shared spine is an embedded library, not a background daemon. It provides:

- UUIDv7 capture identity;
- device and adapter metadata;
- local permission and redaction enforcement;
- persistent offline queue and retry state;
- ordering and queue-age reporting;
- policy synchronization and version stamping;
- authenticated batch delivery; and
- device/adapter revocation handling.

Each host application embeds the spine. There is no invisible always-running
process that continues capturing after the host closes.

## Offline behavior

The spine caches the last instance policy and may always apply a stricter local
pause. While disconnected:

- permitted captures remain in the local queue;
- queued content cannot be mined, shared, or sent to a model;
- delivery retries preserve the original capture ID; and
- the queue records age so the instance can normalize gross device-clock skew.

On reconnection, the spine refreshes policy before upload and re-evaluates the
queue. Captures no longer allowed by policy stay local for review or deletion.

## Revocation

Device or adapter revocation is a hard boundary. Once credentials are revoked,
the instance rejects new uploads using them regardless of the claimed
`occurred_at`. An offline queue cannot bypass revocation by asserting that its
content predates the revocation time.

Per-device credential issuance and revocation are accepted requirements but
are not implemented by the single-token foundation service.

## Initial capture scope

Loreholm begins with:

- assistant transcripts from chat and IDE-agent surfaces; and
- explicit pushes through a “remember this” or share gesture.

Pushed documents and screens use snapshot identity with a content hash. Loreholm does
not initially passively watch files, browser history, or the screen. “Passive
capture” in the initial scope refers to transcript capture occurring as part of
using an assistant, without explicit memory-tool calls.

## Hints and references

Adapters may send:

- `refs`: source-native links, spans, paths, URLs, message IDs, or object IDs;
- `hints`: non-authoritative suggestions that may help later interpretation.

The server treats hints as priors it can ignore or override. Moving intelligent
deduplication or summarization into hints does not make it valid client logic.

## Packaging contract still needed

Before a reusable spine can ship, Loreholm still needs concrete specifications for:

- local queue storage and encryption expectations;
- policy refresh and compatibility negotiation;
- credential enrollment, rotation, and revocation;
- payload size and batching limits below the HTTP maximum;
- deterministic sanitization behavior;
- adapter lifecycle hooks and pause indicators;
- backpressure, retry timing, and poison-envelope handling; and
- supported language/runtime packaging.

Those specifications should build on the implemented
[capture API](03_CaptureAPI.md) without changing the authority boundary.
