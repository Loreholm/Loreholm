"""Staging reconciler for ArcadeDB-backed databases.

The reconciler runs
inside the local dashboard process (pull-only constraint: cloud never
initiates to local). On an interval it sweeps every ArcadeDB database in
the registry, reads `Staging` rows with `status='pending'`, and decides
for each:

  * merge        — close to an existing `Entity`/`Memory`; fold the payload
                   into the existing vertex (weighted-avg embedding for
                   entities; dedup no-op for memories).
  * promote      — novel; re-materialize as an `Entity`/`Memory` vertex
                   and the associated edges.
  * needs_review — ambiguous; leave the staging vertex in place with
                   `status='needs_review'` so the dashboard UI can
                   surface it (Phase 7).
  * rejected     — payload-shape violation (bad entity type, missing
                   fields). Leaves a `rejection_reason` on the row.

Every decision lands in a `ReconcilerDecision` audit row, which the
unmerge endpoint (`api/app/local_dashboard/routes/reconciler.py`) reads
to reverse a merge.

The module is wired from the dashboard lifespan hook.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

import httpx

from .core.config import (
    _load_arcadedb_root_password,
    LOCAL_DASHBOARD_ARCADEDB_HOST,
    LOCAL_DASHBOARD_ARCADEDB_PORT,
    RECONCILER_BATCH_SIZE,
    RECONCILER_MERGE_EMBED_BLEND,
    RECONCILER_MERGE_THRESHOLD,
    RECONCILER_POLL_INTERVAL_SECONDS,
    RECONCILER_REVIEW_THRESHOLD,
    STAGING_MAX_AGE_SECONDS,
)
from .db.registry import _load_registry
from . import metrics as _metrics


_LOG = logging.getLogger(__name__)

# Arbitrary per-request timeout. ArcadeDB Cypher over HTTP is sub-second
# for the shapes we run; anything slower means the container is overloaded
# and we'd rather fail a tick than block the loop.
_RECONCILER_HTTP_TIMEOUT_SECONDS = 20.0


# ---------------------------------------------------------------------------
# HTTP client helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_auth(record: Dict[str, Any]) -> Tuple[str, str]:
    username = str(record.get("username") or "root").strip() or "root"
    password = str(record.get("password") or "").strip()
    if not password:
        try:
            password = _load_arcadedb_root_password()
        except RuntimeError:
            password = ""
    return username, password


def _arcade_url(record: Dict[str, Any]) -> str:
    # Mirrors `db/arcadedb_query._resolve_host`/`_resolve_port`: the
    # registry record's `host`/`port` are forward-compat fields, but the
    # single-server architecture always terminates at the shared
    # `LOCAL_DASHBOARD_ARCADEDB_HOST`/`_PORT`. The reconciler used to fall
    # back to `127.0.0.1:2480`, which silently failed in deployments where
    # ArcadeDB lives in a sidecar netns (Tailscale) — staged proposals
    # sat as `pending` forever because the reconciler couldn't reach the DB.
    host = str(record.get("host") or "").strip() or LOCAL_DASHBOARD_ARCADEDB_HOST
    raw_port = record.get("port")
    try:
        port = int(raw_port) if raw_port not in (None, "") else LOCAL_DASHBOARD_ARCADEDB_PORT
    except (TypeError, ValueError):
        port = LOCAL_DASHBOARD_ARCADEDB_PORT
    database_id = str(record.get("database_id") or "").strip()
    return f"http://{host}:{port}/api/v1/command/{database_id}"


async def _run_query(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    command: str,
    parameters: Optional[Dict[str, Any]] = None,
    *,
    language: str = "cypher",
) -> List[Dict[str, Any]]:
    """Execute a single statement against a database's ArcadeDB container.
    Returns the raw result list.

    `language="sql"` is required for the few statements that depend on
    SQL-only functions like `vectorNeighbors` (which has no Cypher form
    in ArcadeDB 26.x).

    Intentionally bypasses the sync-proxy path in `routes/sync.py`: the
    reconciler is inside the dashboard process and owns registry creds
    directly — the proxy is for cloud traffic.
    """
    auth = _resolve_auth(record)
    if not auth[1]:
        raise RuntimeError(
            "Reconciler cannot connect to ArcadeDB without a password "
            f"(database_id={record.get('database_id')})."
        )
    payload: Dict[str, Any] = {"language": language, "command": command}
    if parameters:
        payload["params"] = parameters
    response = await client.post(_arcade_url(record), json=payload, auth=auth)
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Reconciler received non-JSON from ArcadeDB (HTTP {response.status_code})"
        ) from exc
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, list):
        return []
    return result


async def _run_cypher(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    cypher: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    return await _run_query(client, record, cypher, parameters, language="cypher")


async def _run_sql(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    sql: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    return await _run_query(client, record, sql, parameters, language="sql")


def _strip_meta(row: Any) -> Any:
    """Recursively drop the `@rid`/`@type`/`@cat` metadata keys ArcadeDB
    sprinkles into response documents. Matches `arcadedb_query._coerce_scalar`
    but stays local to this module to keep the reconciler independent of the
    sync proxy's import graph."""
    if isinstance(row, dict):
        return {k: _strip_meta(v) for k, v in row.items() if not k.startswith("@")}
    if isinstance(row, list):
        return [_strip_meta(v) for v in row]
    return row


# ---------------------------------------------------------------------------
# Decision pipeline
# ---------------------------------------------------------------------------


def _blend_embedding(
    current: Sequence[float],
    proposed: Sequence[float],
    new_weight: float,
) -> List[float]:
    """Weighted average of two vectors; new_weight ∈ [0, 1] controls how
    much the proposed vector drags the centroid. The result is NOT
    re-normalized — ArcadeDB's cosine distance normalizes at query time,
    so a small magnitude drift is acceptable. We cap at the shorter length
    in case of dimension skew (shouldn't happen in steady state)."""
    if not current:
        return [float(v) for v in proposed]
    if not proposed:
        return [float(v) for v in current]
    n = min(len(current), len(proposed))
    blended = [
        (1.0 - new_weight) * float(current[i]) + new_weight * float(proposed[i])
        for i in range(n)
    ]
    return blended


def _thresholds_for(record: Dict[str, Any]) -> Tuple[float, float, float]:
    """Resolve the (merge, review, blend) triple for a database record.

    Per-database overrides live in the `reconciler` block of the registry
    record (normalized by `_normalize_reconciler_block`). If the block is
    missing or malformed, fall back to the process-wide env defaults.
    """
    block = record.get("reconciler") if isinstance(record, dict) else None
    if not isinstance(block, dict):
        return (
            RECONCILER_MERGE_THRESHOLD,
            RECONCILER_REVIEW_THRESHOLD,
            RECONCILER_MERGE_EMBED_BLEND,
        )
    try:
        merge = float(block.get("merge_threshold", RECONCILER_MERGE_THRESHOLD))
    except (TypeError, ValueError):
        merge = RECONCILER_MERGE_THRESHOLD
    try:
        review = float(block.get("review_threshold", RECONCILER_REVIEW_THRESHOLD))
    except (TypeError, ValueError):
        review = RECONCILER_REVIEW_THRESHOLD
    try:
        blend = float(block.get("merge_embed_blend", RECONCILER_MERGE_EMBED_BLEND))
    except (TypeError, ValueError):
        blend = RECONCILER_MERGE_EMBED_BLEND
    return merge, review, blend


def _classify_distance(
    distance: Optional[float],
    merge_threshold: float,
    review_threshold: float,
) -> str:
    """Three-band classification on cosine distance (0 = identical)."""
    if distance is None:
        return "promote"
    if distance < merge_threshold:
        return "merge"
    if distance < review_threshold:
        return "needs_review"
    return "promote"


# ---------------------------------------------------------------------------
# Per-source handlers (upsert_entities / write_memory / link_entities)
# ---------------------------------------------------------------------------


async def _decide_entity_proposal(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    staging: Dict[str, Any],
) -> Dict[str, Any]:
    """Classify a staged Entity proposal against the nearest existing
    Entity of the same type. Returns a decision dict:
    {decision, distance, target_id, reason, requested_merge_target_id}.

    LLM-side hint protocol:
    if the staging row carries `requested_merge_target_id` (set when the
    caller passed `merge_into` on `loreholm_upsert_entities`), compute
    distance to *that specific* Entity instead of taking the global
    nearest-neighbor. Decisions:

      - distance < `review_threshold` → merge into the requested target
        (LLM session context outranks pure vector nearest).
      - distance ≥ `review_threshold` → downgrade to `needs_review` with
        `reason='llm_hint_distance_exceeded'`. Audit row records both the
        requested target and the actual distance.
      - hinted target id doesn't resolve to an Entity → fall through to
        the normal vector-nearest path; audit `reason='llm_hint_unresolved'`.
    """
    embedding = staging.get("embedding") or []
    if not embedding:
        return {
            "decision": "rejected",
            "distance": None,
            "target_id": None,
            "reason": "missing embedding",
            "requested_merge_target_id": None,
        }
    skip_target = str(staging.get("skip_merge_target_id") or "").strip() or None
    requested_merge_target_id = (
        str(staging.get("requested_merge_target_id") or "").strip() or None
    )
    merge_threshold, review_threshold, _ = _thresholds_for(record)

    if requested_merge_target_id:
        # Compute distance to the *requested* target rather than the global
        # nearest. SQL because `vectorNeighbors` has no Cypher form in 26.x.
        # We pull the top-k neighborhood, then filter to the requested id —
        # if the hint resolves but is far from the proposal, we want the
        # actual cosine distance to drive the decision below.
        hint_rows = await _run_sql(
            client,
            record,
            """
            SELECT id AS entity_id, distance
            FROM (SELECT expand(vectorNeighbors('Entity[embedding]', :embedding, :k)))
            WHERE id = :target_id
            LIMIT 1
            """,
            {
                "embedding": embedding,
                "k": 200,
                "target_id": requested_merge_target_id,
            },
        )
        if hint_rows:
            row = _strip_meta(hint_rows[0])
            target_id = row.get("entity_id")
            distance_value = row.get("distance")
            distance = float(distance_value) if distance_value is not None else None
            if distance is not None and distance < review_threshold:
                if skip_target and str(target_id) == skip_target:
                    # Honor unmerge intent even on an explicit hint.
                    return {
                        "decision": "promote",
                        "distance": distance,
                        "target_id": None,
                        "reason": "llm_hint_skip_unmerged",
                        "requested_merge_target_id": requested_merge_target_id,
                    }
                return {
                    "decision": "merge",
                    "distance": distance,
                    "target_id": str(target_id) if target_id else None,
                    "reason": "llm_hint_within_review",
                    "requested_merge_target_id": requested_merge_target_id,
                }
            return {
                "decision": "needs_review",
                "distance": distance,
                "target_id": str(target_id) if target_id else None,
                "reason": "llm_hint_distance_exceeded",
                "requested_merge_target_id": requested_merge_target_id,
            }
        # Hint id didn't show up in the top-200 neighborhood — either the
        # entity was deleted/merged elsewhere, or the LLM passed a stale id.
        # Verify existence directly; if absent, fall through to the normal
        # nearest-neighbor decision and audit `llm_hint_unresolved`.
        exists_rows = await _run_cypher(
            client,
            record,
            "MATCH (e:Entity {id: $target_id}) RETURN e.id AS id LIMIT 1;",
            {"target_id": requested_merge_target_id},
        )
        if not exists_rows:
            # fall through with an unresolved-hint marker
            unresolved_marker = True
        else:
            # Entity exists but is far enough away that vectorNeighbors at
            # k=200 didn't reach it. Treat the same as "distance exceeded".
            return {
                "decision": "needs_review",
                "distance": None,
                "target_id": requested_merge_target_id,
                "reason": "llm_hint_distance_exceeded",
                "requested_merge_target_id": requested_merge_target_id,
            }
    else:
        unresolved_marker = False

    # SQL because `vectorNeighbors` is SQL-only in ArcadeDB 26.x. We filter
    # to the proposed type so two entities named "Apollo" (Mission vs
    # Program) don't collide across buckets.
    rows = await _run_sql(
        client,
        record,
        """
        SELECT id AS entity_id, distance
        FROM (SELECT expand(vectorNeighbors('Entity[embedding]', :embedding, :k)))
        WHERE type = :type
        ORDER BY distance ASC
        LIMIT 1
        """,
        {
            "embedding": embedding,
            "k": 10,
            "type": staging.get("proposed_type"),
        },
    )
    if not rows:
        return {
            "decision": "promote",
            "distance": None,
            "target_id": None,
            "reason": (
                "llm_hint_unresolved" if unresolved_marker else "no neighbors"
            ),
            "requested_merge_target_id": requested_merge_target_id,
        }
    row = _strip_meta(rows[0])
    target_id = row.get("entity_id")
    distance = row.get("distance")
    distance = float(distance) if distance is not None else None
    decision = _classify_distance(distance, merge_threshold, review_threshold)
    if decision == "merge" and skip_target and str(target_id) == skip_target:
        # Unmerge endpoint asked us not to re-merge into this target.
        # Fall through to promotion so the proposal lands as a new Entity.
        decision = "promote"
    return {
        "decision": decision,
        "distance": distance,
        "target_id": str(target_id) if target_id else None,
        "reason": (
            "llm_hint_unresolved" if unresolved_marker else "vectorNeighbors lookup"
        ),
        "requested_merge_target_id": requested_merge_target_id,
    }


async def _apply_entity_merge(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    staging: Dict[str, Any],
    target_id: str,
) -> None:
    """Fold a staged Entity proposal into `target_id`:
       - blend embeddings 90/10 (configurable via RECONCILER_MERGE_EMBED_BLEND)
       - union `aliases` / `aliases_norm`
       - bump `updated_at`
    """
    # Read the current Entity so we can blend its embedding server-side
    # instead of in Cypher (Cypher has no list arithmetic).
    existing_rows = await _run_cypher(
        client,
        record,
        """
        MATCH (e:Entity {id: $target_id})
        RETURN e.embedding AS embedding, e.aliases AS aliases, e.aliases_norm AS aliases_norm;
        """,
        {"target_id": target_id},
    )
    if not existing_rows:
        raise RuntimeError(
            f"merge target Entity {target_id!r} vanished between lookup and merge"
        )
    existing = _strip_meta(existing_rows[0])
    existing_embedding = existing.get("embedding") or []
    existing_aliases = existing.get("aliases") or []
    existing_aliases_norm = existing.get("aliases_norm") or []

    proposed_embedding = staging.get("embedding") or []
    _, _, blend_weight = _thresholds_for(record)
    blended = _blend_embedding(
        existing_embedding, proposed_embedding, blend_weight
    )
    new_alias = staging.get("proposed_name")
    proposed_aliases = list(staging.get("aliases") or [])
    if new_alias:
        proposed_aliases.append(new_alias)
    proposed_aliases_norm = list(staging.get("aliases_norm") or [])
    if staging.get("proposed_name_norm"):
        proposed_aliases_norm.append(staging["proposed_name_norm"])

    merged_aliases = list(
        dict.fromkeys(
            [a for a in existing_aliases if a] + [a for a in proposed_aliases if a]
        )
    )
    merged_aliases_norm = list(
        dict.fromkeys(
            [a for a in existing_aliases_norm if a]
            + [a for a in proposed_aliases_norm if a]
        )
    )

    await _run_cypher(
        client,
        record,
        """
        MATCH (e:Entity {id: $target_id})
        SET e.embedding = $embedding,
            e.aliases = $aliases,
            e.aliases_norm = $aliases_norm,
            e.updated_at = $now;
        """,
        {
            "target_id": target_id,
            "embedding": blended,
            "aliases": merged_aliases,
            "aliases_norm": merged_aliases_norm,
            "now": _now_iso(),
        },
    )


async def _promote_entity(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    staging: Dict[str, Any],
) -> str:
    """Re-materialize a staged Entity as a committed `Entity`. Returns the
    new Entity.id."""
    entity_id = uuid4().hex
    await _run_cypher(
        client,
        record,
        """
        CREATE (e:Entity {
          id: $entity_id,
          name: $name,
          name_norm: $name_norm,
          type: $type,
          aliases: $aliases,
          aliases_norm: $aliases_norm,
          embedding: $embedding,
          created_at: $now,
          updated_at: $now
        })
        RETURN e.id;
        """,
        {
            "entity_id": entity_id,
            "name": staging.get("proposed_name"),
            "name_norm": staging.get("proposed_name_norm"),
            "type": staging.get("proposed_type"),
            "aliases": staging.get("aliases") or [],
            "aliases_norm": staging.get("aliases_norm") or [],
            "embedding": staging.get("embedding") or [],
            "now": _now_iso(),
        },
    )
    return entity_id


async def _promote_memory(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    staging: Dict[str, Any],
    resolved_about_entity_ids: List[str],
) -> str:
    """Re-materialize a staged Memory payload into a committed `Memory`,
    plus the surrounding `Conversation` / `Message` scaffold and the
    `ABOUT` / `DERIVED_FROM` edges. Returns the new Memory.id.

    Runs async at reconciler time so the write path doesn't block on
    LLM-turn latency.
    """
    memory_id = uuid4().hex
    now = _now_iso()
    conversation_id = staging.get("proposed_conversation_id")
    # Create/merge Conversation.
    if conversation_id:
        await _run_cypher(
            client,
            record,
            """
            MERGE (c:Conversation {id: $conversation_id})
              ON CREATE SET c.platform = coalesce($platform, 'unknown'),
                            c.started_at = coalesce($started_at, $now),
                            c.created_at = $now
            RETURN c.id;
            """,
            {
                "conversation_id": conversation_id,
                "platform": staging.get("proposed_conversation_platform"),
                "started_at": staging.get("proposed_conversation_started_at"),
                "now": now,
            },
        )
        # Message scaffold.
        payload_messages = staging.get("proposed_message_payload") or []
        for msg in payload_messages:
            if not isinstance(msg, dict) or not msg.get("id"):
                continue
            await _run_cypher(
                client,
                record,
                """
                MATCH (c:Conversation {id: $conversation_id})
                MERGE (msg:Message {id: $message_id})
                  ON CREATE SET msg.role = coalesce($role, 'unknown'),
                                msg.text = coalesce($text, ''),
                                msg.timestamp = coalesce($timestamp, $now),
                                msg.created_at = $now
                MERGE (c)-[:HAS_MESSAGE]->(msg);
                """,
                {
                    "conversation_id": conversation_id,
                    "message_id": msg.get("id"),
                    "role": msg.get("role"),
                    "text": msg.get("text"),
                    "timestamp": msg.get("timestamp"),
                    "now": now,
                },
            )

    # The Memory itself + ABOUT/DERIVED_FROM edges.
    await _run_cypher(
        client,
        record,
        """
        CREATE (m:Memory {
          id: $memory_id,
          text: $text,
          embedding: $embedding,
          confidence: $confidence,
          tags: $tags,
          timestamp: $now,
          conversation_id: $conversation_id,
          created_at: $now
        })
        WITH m
        UNWIND coalesce($about_entity_ids, []) AS eid
        OPTIONAL MATCH (e:Entity {id: eid})
        FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END |
          MERGE (m)-[:ABOUT]->(e)
        )
        WITH m
        UNWIND coalesce($message_ids, []) AS mid
        OPTIONAL MATCH (msg:Message {id: mid})
        FOREACH (_ IN CASE WHEN msg IS NULL THEN [] ELSE [1] END |
          MERGE (m)-[:DERIVED_FROM]->(msg)
        )
        RETURN m.id;
        """,
        {
            "memory_id": memory_id,
            "text": staging.get("proposed_text"),
            "embedding": staging.get("embedding") or [],
            "confidence": staging.get("proposed_confidence"),
            "tags": staging.get("proposed_tags") or [],
            "now": now,
            "conversation_id": conversation_id,
            "about_entity_ids": resolved_about_entity_ids,
            "message_ids": staging.get("proposed_message_ids") or [],
        },
    )
    return memory_id


async def _promote_edge(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    staging: Dict[str, Any],
    resolved_from_id: str,
    resolved_to_id: str,
) -> Optional[str]:
    """Create a RELATED_TO edge between two committed entities. Returns
    the edge id, or None if the edge was a self-loop (dropped) or the
    pair already had a RELATED_TO with the same relationship label
    (deduped)."""
    if not resolved_from_id or not resolved_to_id:
        return None
    if resolved_from_id == resolved_to_id:
        # Self-loops arise when both endpoints merged into the same
        # existing entity — silently drop, per plan §4.5.
        return None
    edge_id = uuid4().hex
    rows = await _run_cypher(
        client,
        record,
        """
        MATCH (a:Entity {id: $from_id}), (b:Entity {id: $to_id})
        MERGE (a)-[r:RELATED_TO {relationship: $relationship}]->(b)
          ON CREATE SET r.id = $edge_id,
                        r.confidence = $confidence,
                        r.reason = $reason,
                        r.created_at = $now,
                        r.updated_at = $now
          ON MATCH SET  r.confidence = $confidence,
                        r.reason = $reason,
                        r.updated_at = $now
        RETURN r.id AS edge_id;
        """,
        {
            "from_id": resolved_from_id,
            "to_id": resolved_to_id,
            "relationship": staging.get("proposed_relationship"),
            "confidence": staging.get("proposed_confidence"),
            "reason": staging.get("proposed_reason"),
            "edge_id": edge_id,
            "now": _now_iso(),
        },
    )
    if not rows:
        return None
    row = _strip_meta(rows[0])
    return str(row.get("edge_id") or edge_id)


async def _resolve_endpoint(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    raw_id: str,
) -> Optional[str]:
    """Resolve a staging-or-committed id to the committed Entity.id it now
    points to. The staging row may have been merged into an existing
    Entity (look up via the audit log) or promoted to a new Entity.

    Returns None if the id can't be resolved (caller will leave the edge
    staged for a future tick)."""
    if not raw_id:
        return None
    rows = await _run_cypher(
        client,
        record,
        "MATCH (e:Entity {id: $id}) RETURN e.id AS id LIMIT 1;",
        {"id": raw_id},
    )
    if rows:
        row = _strip_meta(rows[0])
        if row.get("id"):
            return str(row["id"])
    audit_rows = await _run_cypher(
        client,
        record,
        """
        MATCH (d:ReconcilerDecision {staging_id: $id})
        WHERE d.decision IN ['merge', 'promote'] AND (d.reversed IS NULL OR d.reversed = false)
        RETURN d.target_id AS target_id
        ORDER BY d.decided_at DESC
        LIMIT 1;
        """,
        {"id": raw_id},
    )
    if audit_rows:
        row = _strip_meta(audit_rows[0])
        if row.get("target_id"):
            return str(row["target_id"])
    return None


# ---------------------------------------------------------------------------
# Audit log + staging status transitions
# ---------------------------------------------------------------------------


async def _write_audit(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    *,
    staging_id: str,
    decision: str,
    distance: Optional[float],
    target_id: Optional[str],
    reason: Optional[str],
    payload: Dict[str, Any],
    requested_merge_target_id: Optional[str] = None,
    had_prior_search: Optional[bool] = None,
) -> None:
    source = str(payload.get("source") or "").strip() or None
    await _run_cypher(
        client,
        record,
        """
        CREATE (d:ReconcilerDecision {
          id: $id,
          staging_id: $staging_id,
          decision: $decision,
          distance: $distance,
          target_id: $target_id,
          reason: $reason,
          source: $source,
          requested_merge_target_id: $requested_merge_target_id,
          had_prior_search: $had_prior_search,
          payload: $payload,
          decided_at: $now,
          reversed: false
        });
        """,
        {
            "id": uuid4().hex,
            "staging_id": staging_id,
            "decision": decision,
            "distance": distance,
            "target_id": target_id,
            "reason": reason,
            "source": source,
            "requested_merge_target_id": requested_merge_target_id,
            "had_prior_search": had_prior_search,
            # Payload stored as JSON string so ArcadeDB doesn't have to
            # know the shape — we only read it back on unmerge, which
            # reconstructs the staging row.
            "payload": json.dumps(payload, default=str),
            "now": _now_iso(),
        },
    )
    database_id = record.get("database_id")
    _metrics.inc_decision(database_id, decision)
    if source == "upsert_entities":
        _metrics.inc_upsert(
            database_id,
            had_prior_search=bool(had_prior_search),
            had_merge_into=bool(requested_merge_target_id),
        )
    # Structured per-decision log — single line, key=value pairs so log
    # aggregators can parse without a format module.
    _LOG.info(
        "reconciler_decision database_id=%s staging_id=%s decision=%s "
        "target_id=%s distance=%s reason=%s requested_merge_target_id=%s",
        database_id,
        staging_id,
        decision,
        target_id,
        "null" if distance is None else f"{distance:.4f}",
        (reason or "").replace(" ", "_") or "null",
        requested_merge_target_id or "null",
    )


async def _delete_staging(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    staging_id: str,
) -> None:
    await _run_cypher(
        client,
        record,
        "MATCH (s:Staging {id: $id}) DETACH DELETE s;",
        {"id": staging_id},
    )


async def _mark_staging_status(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    staging_id: str,
    status: str,
    reason: Optional[str] = None,
) -> None:
    params: Dict[str, Any] = {
        "id": staging_id,
        "status": status,
        "now": _now_iso(),
    }
    if reason is not None:
        params["reason"] = reason
        await _run_cypher(
            client,
            record,
            """
            MATCH (s:Staging {id: $id})
            SET s.status = $status,
                s.rejection_reason = $reason,
                s.updated_at = $now;
            """,
            params,
        )
    else:
        await _run_cypher(
            client,
            record,
            """
            MATCH (s:Staging {id: $id})
            SET s.status = $status, s.updated_at = $now;
            """,
            params,
        )


# ---------------------------------------------------------------------------
# Per-database sweep
# ---------------------------------------------------------------------------


async def _fetch_pending(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    batch_size: int,
) -> List[Dict[str, Any]]:
    rows = await _run_cypher(
        client,
        record,
        """
        MATCH (s:Staging {status: 'pending'})
        RETURN s
        ORDER BY s.created_at ASC
        LIMIT $batch;
        """,
        {"batch": batch_size},
    )
    # ArcadeDB inlines `RETURN s` as a flat row dict carrying the vertex's
    # properties + `@rid`/`@type`/`@cat` metadata — there is no enclosing
    # `{"s": {...}}` wrapper the way Neo4j/Memgraph would produce. The
    # earlier `row.get("s")` shape was a Memgraph-era leftover that
    # silently dropped every Staging row on every sweep.
    staged: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            staged.append(_strip_meta(row))
    return staged


async def _warn_stale_staging(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
) -> None:
    """Phase 3.6: emit a structured log when pending rows are older than
    STAGING_MAX_AGE_SECONDS — the operator-visible signal that the
    reconciler is backed up. Phase 7.4 extends this sweep to also refresh
    the Prometheus gauges so /metrics reflects the same counts the UI
    feeds report."""
    rows = await _run_cypher(
        client,
        record,
        """
        MATCH (s:Staging)
        RETURN s.status AS status,
               count(s) AS count,
               min(s.created_at) AS oldest
        """
        # Note: cannot filter by "now - age" server-side in portable Cypher;
        # we pull the oldest timestamp and compare client-side.
        ,
        {},
    )
    database_id = record.get("database_id")
    pending_count = 0
    oldest_pending: Optional[str] = None
    seen_statuses: set[str] = set()
    for raw in rows or []:
        row = _strip_meta(raw)
        status = str(row.get("status") or "").strip()
        if not status:
            continue
        try:
            count_value = int(row.get("count") or 0)
        except (TypeError, ValueError):
            count_value = 0
        _metrics.set_staging_count(database_id, status, count_value)
        seen_statuses.add(status)
        if status == "pending":
            pending_count = count_value
            oldest_pending = row.get("oldest")
    for status in ("pending", "needs_review", "rejected"):
        if status not in seen_statuses:
            _metrics.set_staging_count(database_id, status, 0)

    if not oldest_pending or pending_count == 0:
        _metrics.clear_lag(database_id)
        return
    try:
        oldest_dt = datetime.fromisoformat(str(oldest_pending).replace("Z", "+00:00"))
    except ValueError:
        _metrics.clear_lag(database_id)
        return
    age_seconds = (datetime.now(timezone.utc) - oldest_dt).total_seconds()
    _metrics.set_lag(database_id, age_seconds)
    if age_seconds > STAGING_MAX_AGE_SECONDS:
        _LOG.warning(
            "[reconciler] database=%s pending_staging_age_seconds=%.0f "
            "threshold=%s stale_count=%s",
            database_id,
            age_seconds,
            STAGING_MAX_AGE_SECONDS,
            pending_count,
        )


async def _reconcile_one(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
    staging: Dict[str, Any],
) -> None:
    """Run the decision pipeline for a single Staging row and apply the
    outcome. All exceptions are caught by the caller."""
    staging_id = staging.get("id")
    if not staging_id:
        return
    source = str(staging.get("source") or "").strip()

    if source == "upsert_entities":
        had_prior_search = staging.get("had_prior_search")
        if isinstance(had_prior_search, str):
            had_prior_search = had_prior_search.strip().lower() in {"true", "1", "yes"}
        elif had_prior_search is None:
            had_prior_search = None
        else:
            had_prior_search = bool(had_prior_search)

        if not staging.get("proposed_name") or not staging.get("proposed_type"):
            await _mark_staging_status(
                client, record, staging_id, "rejected", "missing proposed_name/type"
            )
            await _write_audit(
                client,
                record,
                staging_id=staging_id,
                decision="rejected",
                distance=None,
                target_id=None,
                reason="missing proposed_name/type",
                payload=staging,
                requested_merge_target_id=(
                    str(staging.get("requested_merge_target_id") or "").strip() or None
                ),
                had_prior_search=had_prior_search,
            )
            return

        decision = await _decide_entity_proposal(client, record, staging)
        requested_hint = decision.get("requested_merge_target_id")
        if decision["decision"] == "merge":
            await _apply_entity_merge(
                client, record, staging, decision["target_id"]
            )
            await _write_audit(
                client,
                record,
                staging_id=staging_id,
                decision="merge",
                distance=decision["distance"],
                target_id=decision["target_id"],
                reason=decision.get("reason"),
                payload=staging,
                requested_merge_target_id=requested_hint,
                had_prior_search=had_prior_search,
            )
            await _delete_staging(client, record, staging_id)
        elif decision["decision"] == "needs_review":
            await _mark_staging_status(client, record, staging_id, "needs_review")
            await _write_audit(
                client,
                record,
                staging_id=staging_id,
                decision="needs_review",
                distance=decision["distance"],
                target_id=decision["target_id"],
                reason=decision.get("reason"),
                payload=staging,
                requested_merge_target_id=requested_hint,
                had_prior_search=had_prior_search,
            )
        else:  # promote
            new_id = await _promote_entity(client, record, staging)
            await _write_audit(
                client,
                record,
                staging_id=staging_id,
                decision="promote",
                distance=decision["distance"],
                target_id=new_id,
                reason=decision.get("reason"),
                payload=staging,
                requested_merge_target_id=requested_hint,
                had_prior_search=had_prior_search,
            )
            await _delete_staging(client, record, staging_id)

    elif source == "write_memory":
        # Memories don't dedupe against existing memories (dedup is what
        # the reconciler is for on the *entity* side); we just promote and
        # resolve about_entity_ids against the committed graph plus the
        # audit log (which maps merged/promoted staging_ids → Entity.ids).
        raw_about_ids = list(staging.get("proposed_about_entity_ids") or [])
        resolved_about_ids: List[str] = []
        for raw in raw_about_ids:
            resolved = await _resolve_endpoint(client, record, str(raw))
            if resolved:
                resolved_about_ids.append(resolved)
        memory_id = await _promote_memory(
            client, record, staging, resolved_about_ids
        )
        await _write_audit(
            client,
            record,
            staging_id=staging_id,
            decision="promote",
            distance=None,
            target_id=memory_id,
            reason="memory promoted",
            payload=staging,
        )
        await _delete_staging(client, record, staging_id)

    elif source == "link_entities":
        from_id = str(staging.get("proposed_from_id") or "").strip()
        to_id = str(staging.get("proposed_to_id") or "").strip()
        resolved_from = await _resolve_endpoint(client, record, from_id)
        resolved_to = await _resolve_endpoint(client, record, to_id)
        if not resolved_from or not resolved_to:
            # Endpoints not resolved yet — leave for next tick. A genuinely
            # orphan edge eventually trips the TTL warning.
            return
        edge_id = await _promote_edge(
            client, record, staging, resolved_from, resolved_to
        )
        reason = "edge promoted" if edge_id else "edge dropped (self-loop or dedup)"
        await _write_audit(
            client,
            record,
            staging_id=staging_id,
            decision="promote" if edge_id else "rejected",
            distance=None,
            target_id=edge_id,
            reason=reason,
            payload=staging,
        )
        await _delete_staging(client, record, staging_id)

    else:
        await _mark_staging_status(
            client, record, staging_id, "rejected", f"unknown source {source!r}"
        )
        await _write_audit(
            client,
            record,
            staging_id=staging_id,
            decision="rejected",
            distance=None,
            target_id=None,
            reason=f"unknown source {source!r}",
            payload=staging,
        )


async def _sweep_database(
    client: httpx.AsyncClient,
    record: Dict[str, Any],
) -> None:
    await _warn_stale_staging(client, record)
    rows = await _fetch_pending(client, record, RECONCILER_BATCH_SIZE)
    for staging in rows:
        try:
            await _reconcile_one(client, record, staging)
        except Exception:  # pragma: no cover — per-row fail shouldn't kill sweep
            _LOG.exception(
                "[reconciler] database=%s staging_id=%s failed",
                record.get("database_id"),
                staging.get("id"),
            )


# ---------------------------------------------------------------------------
# Background task entry point
# ---------------------------------------------------------------------------


async def _run_loop(stop_event: asyncio.Event) -> None:
    timeout = httpx.Timeout(_RECONCILER_HTTP_TIMEOUT_SECONDS, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while not stop_event.is_set():
            try:
                registry = _load_registry()
            except Exception:  # pragma: no cover
                _LOG.exception("[reconciler] failed to load registry")
                registry = {"databases": []}
            for record in registry.get("databases", []) or []:
                if not isinstance(record, dict):
                    continue
                try:
                    await _sweep_database(client, record)
                except Exception:  # pragma: no cover
                    _LOG.exception(
                        "[reconciler] sweep failed database=%s",
                        record.get("database_id"),
                    )
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=RECONCILER_POLL_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                continue


class ReconcilerSupervisor:
    """Owns the reconciler asyncio task so the lifespan hook can start it
    on app boot and cancel it on shutdown. One instance per process; the
    `main.py` lifespan hook wires it up only when the ArcadeDB backend is
    active."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(_run_loop(self._stop_event))
        _LOG.info(
            "[reconciler] started (poll=%.1fs batch=%d merge<%.2f review<%.2f)",
            RECONCILER_POLL_INTERVAL_SECONDS,
            RECONCILER_BATCH_SIZE,
            RECONCILER_MERGE_THRESHOLD,
            RECONCILER_REVIEW_THRESHOLD,
        )

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        if self._stop_event is not None:
            self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # pragma: no cover
                pass
        _LOG.info("[reconciler] stopped")
        self._task = None
        self._stop_event = None


_supervisor = ReconcilerSupervisor()


def get_supervisor() -> ReconcilerSupervisor:
    return _supervisor
