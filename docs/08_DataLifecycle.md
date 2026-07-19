# V2 data lifecycle: retention, deletion, backup, and sharing

**Status:** Accepted design unless explicitly labeled implemented. The
foundation currently retains captures in ArcadeDB and has no capture inventory,
deletion API, backup coordinator, restore command, or sharing protocol.

## Current behavior

- Raw envelopes persist in the `V2Capture` document store.
- Duplicate IDs do not create duplicate records.
- Unknown classes persist in quarantine.
- Docker volume removal through the destructive uninstall path erases the base
  instance's ArcadeDB volume.
- No supported per-capture deletion or backup workflow exists yet.

## Retention design

Raw captures have no automatic age-based expiration by default. Storage
pressure must not silently discard provenance.

The planned inventory lets users browse and filter by time, surface, class,
session/object, size, mining state, salience, and last use. Before deletion it
previews:

- payload and vector bytes reclaimed;
- affected evidence; and
- claims that remain supported or become archived.

## Deletion lifecycle

The default local grace period is 30 days and is user-configurable, including
immediate deletion.

```text
active
  |
  | user deletion request
  v
pending_deletion ---- undo before deadline ----> active
  |
  | grace expires or explicit hard deletion
  v
payload/vector purge -> evidence close -> unsupported claim archive
```

While pending deletion, content disappears from normal queries, new mining,
sharing, and model egress immediately, but remains locally recoverable until
the deadline.

Finalization:

- purges the raw payload and every vector derived from it;
- closes its Evidence records, retaining only content-free deletion tombstones;
- archives a claim if it loses its final active evidence source; and
- leaves a claim active when independent evidence still supports it.

Provenance makes cleanup a deterministic traversal rather than semantic search.

## Inference preservation

Derived inference is user-paid work and remains available for audit and exact
reuse until superseded or removed by source deletion. Re-mining does not erase
older successful generations; normal reads prefer the newest successful active
generation.

Deletion is the exception. Content and vectors derived from a deleted source
must not survive merely because inference was expensive.

## Backup design

A recoverable backup is instance-consistent and contains:

- ArcadeDB data, including external payload properties;
- the Git-versioned schema repository and active commit;
- instance configuration and policy needed to interpret stored state; and
- a manifest with instance, format, and schema versions.

Backups exclude live provider credentials, device tokens, synchronization
tokens, and Bifrost secrets. After restore, the operator re-enters provider
keys and reauthorizes clients.

The coordinator must obtain a consistent database snapshot and matching schema
commit rather than independently copying a live volume and working tree. It
must support verification and a documented restore test.

Finalized deletions are absent from new backup sets. Older immutable backups
may retain pre-deletion bytes until their configured retention expires; restore
must identify backup age and reapply any available later deletion tombstones
before restored content becomes active.

V2 does not initially require redundant ArcadeDB or object storage. Backup
destinations may be local removable storage or an operator-selected remote
target; destination credentials remain outside the backup payload.

## Sharing between instances

Sharing is explicit disclosure, not automatic synchronization.

- The personal instance sends a selected raw session or mined subgraph slice.
- The embedded spine never routes captures to several instances.
- Receiving instances own independent copies.
- The origin cannot guarantee recall or remote deletion after disclosure.
- Instances do not auto-fuse overlapping entities; query surfaces may combine
  views without rewriting either graph.

Shared records preserve origin-namespaced identity:

```text
(origin_instance_id, record_id)
```

The receiver may resolve an imported entity to a local entity or mint a new
one without erasing origin identity and provenance. Re-sharing the same slice
is idempotent on those origin-namespaced IDs.

## Cross-boundary deletion

Deletion guarantees apply only inside the instance performing deletion.
Content already disclosed to another instance is outside the origin's control.
The product must state this at the share gesture and must not present
best-effort deletion notices as a privacy guarantee.

## Required implementation work

- capture and derived-artifact inventory APIs;
- storage accounting and impact preview;
- grace-period scheduler, undo, and hard-delete path;
- provenance traversal and claim archival;
- consistent snapshot and restore coordinator;
- backup format/version manifest and verification;
- instance identity and authenticated sharing transport;
- slice format with origin-preserving IDs; and
- explicit sharing UI and irreversible-disclosure language.
