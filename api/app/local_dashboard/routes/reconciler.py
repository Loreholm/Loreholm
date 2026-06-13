"""Reconciler dashboard endpoints.

The unmerge endpoint reverses a committed merge decision by
reconstructing the original `Staging` row from the audit-log payload,
tagging it with `skip_merge_target_id` so the next reconciler pass
promotes it into a new entity instead of re-merging back into the same
target, and marking the original `ReconcilerDecision` row as
`reversed=true`.

Phase 7 adds read-side feeds and a threshold PATCH so the dashboard can
render the reconciler tab: pending, needs_review, recent decisions, and
per-database threshold overrides.

The router is mounted under `/api/dashboard/reconciler/*`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.auth import require_local_auth
from ..db.registry import _find_database, _load_registry, _registry_lock, _save_registry
from ..db.schemas import _normalize_reconciler_block
from ..reconciler import _run_cypher, _strip_meta


_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/reconciler")


class UnmergeRequest(BaseModel):
    database_id: str = Field(..., min_length=1)
    decision_id: str = Field(..., min_length=1)


@router.post("/unmerge")
async def unmerge_decision(
    payload: UnmergeRequest,
    _: None = Depends(require_local_auth),
) -> Dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, payload.database_id)

    timeout = httpx.Timeout(20.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        decision_rows = await _run_cypher(
            client,
            record,
            """
            MATCH (d:ReconcilerDecision {id: $decision_id})
            RETURN d.id AS id,
                   d.staging_id AS staging_id,
                   d.decision AS decision,
                   d.target_id AS target_id,
                   d.payload AS payload,
                   d.reversed AS reversed
            LIMIT 1;
            """,
            {"decision_id": payload.decision_id},
        )
        if not decision_rows:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "code": "DECISION_NOT_FOUND",
                        "message": f"Unknown decision_id '{payload.decision_id}'.",
                    }
                },
            )
        decision = _strip_meta(decision_rows[0])
        if decision.get("decision") != "merge":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "DECISION_NOT_MERGE",
                        "message": (
                            "Unmerge only applies to merge decisions; got "
                            f"{decision.get('decision')!r}."
                        ),
                    }
                },
            )
        if decision.get("reversed"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "DECISION_ALREADY_REVERSED",
                        "message": "This merge has already been unmerged.",
                    }
                },
            )

        raw_payload = decision.get("payload")
        try:
            staging_payload = json.loads(raw_payload) if raw_payload else {}
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": "DECISION_PAYLOAD_CORRUPT",
                        "message": "Stored decision payload is not valid JSON.",
                    }
                },
            ) from exc

        staging_id = str(
            staging_payload.get("id")
            or decision.get("staging_id")
            or ""
        ).strip()
        if not staging_id:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": "DECISION_PAYLOAD_MISSING_ID",
                        "message": "Stored decision payload is missing the staging id.",
                    }
                },
            )

        # Reconstruct the staging row. If another tick has already put
        # something back at this id (shouldn't happen — ids are uuid4)
        # the MERGE acts as an upsert.
        await _run_cypher(
            client,
            record,
            """
            MERGE (s:Staging {id: $id})
            SET s.proposed_name = $proposed_name,
                s.proposed_name_norm = $proposed_name_norm,
                s.proposed_type = $proposed_type,
                s.aliases = $aliases,
                s.aliases_norm = $aliases_norm,
                s.embedding = $embedding,
                s.source = $source,
                s.status = 'pending',
                s.skip_merge_target_id = $skip_target,
                s.proposed_text = $proposed_text,
                s.proposed_confidence = $proposed_confidence,
                s.proposed_tags = $proposed_tags,
                s.proposed_about_entity_ids = $proposed_about_entity_ids,
                s.proposed_conversation_id = $proposed_conversation_id,
                s.proposed_conversation_platform = $proposed_conversation_platform,
                s.proposed_conversation_started_at = $proposed_conversation_started_at,
                s.proposed_message_ids = $proposed_message_ids,
                s.proposed_message_payload = $proposed_message_payload,
                s.proposed_from_id = $proposed_from_id,
                s.proposed_to_id = $proposed_to_id,
                s.proposed_relationship = $proposed_relationship,
                s.proposed_reason = $proposed_reason,
                s.created_at = coalesce(s.created_at, $now),
                s.updated_at = $now;
            """,
            {
                "id": staging_id,
                "proposed_name": staging_payload.get("proposed_name"),
                "proposed_name_norm": staging_payload.get("proposed_name_norm"),
                "proposed_type": staging_payload.get("proposed_type"),
                "aliases": staging_payload.get("aliases") or [],
                "aliases_norm": staging_payload.get("aliases_norm") or [],
                "embedding": staging_payload.get("embedding") or [],
                "source": staging_payload.get("source") or "upsert_entities",
                "skip_target": decision.get("target_id"),
                "proposed_text": staging_payload.get("proposed_text"),
                "proposed_confidence": staging_payload.get("proposed_confidence"),
                "proposed_tags": staging_payload.get("proposed_tags") or [],
                "proposed_about_entity_ids": staging_payload.get(
                    "proposed_about_entity_ids"
                )
                or [],
                "proposed_conversation_id": staging_payload.get(
                    "proposed_conversation_id"
                ),
                "proposed_conversation_platform": staging_payload.get(
                    "proposed_conversation_platform"
                ),
                "proposed_conversation_started_at": staging_payload.get(
                    "proposed_conversation_started_at"
                ),
                "proposed_message_ids": staging_payload.get(
                    "proposed_message_ids"
                )
                or [],
                "proposed_message_payload": staging_payload.get(
                    "proposed_message_payload"
                )
                or [],
                "proposed_from_id": staging_payload.get("proposed_from_id"),
                "proposed_to_id": staging_payload.get("proposed_to_id"),
                "proposed_relationship": staging_payload.get("proposed_relationship"),
                "proposed_reason": staging_payload.get("proposed_reason"),
                "now": _now_iso_local(),
            },
        )

        # Mark the audit row as reversed so a future unmerge on the same
        # decision is rejected (and so the UI can filter out reversed rows).
        await _run_cypher(
            client,
            record,
            """
            MATCH (d:ReconcilerDecision {id: $decision_id})
            SET d.reversed = true;
            """,
            {"decision_id": payload.decision_id},
        )

    return {
        "database_id": payload.database_id,
        "decision_id": payload.decision_id,
        "staging_id": staging_id,
        "skip_merge_target_id": decision.get("target_id"),
        "status": "pending",
    }


def _now_iso_local() -> str:
    # Defined inline (instead of importing from reconciler) so the unmerge
    # endpoint has no circular-import risk with `reconciler.py`. Imports
    # from `reconciler` are limited to the pure HTTP helpers.
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Phase 7.1 — data feeds for the dashboard's reconciler tab
# ---------------------------------------------------------------------------


def _compute_lag_seconds(oldest_iso: Optional[str]) -> Optional[float]:
    """Convert the oldest pending timestamp into a positive age in seconds.

    None when there are no pending rows (lag doesn't apply). Parse failures
    collapse to None rather than raising, because a malformed timestamp is
    not the reconciler-tab caller's problem to solve.
    """
    if not oldest_iso:
        return None
    try:
        dt = datetime.fromisoformat(str(oldest_iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


@router.get("/stats/{database_id}")
async def reconciler_stats(
    database_id: str,
    _: None = Depends(require_local_auth),
) -> Dict[str, Any]:
    """Top-line counters for the Reconciler tab header.

    Exposes two LLM-side dedup
    adoption counters derived from the `ReconcilerDecision` audit table:

      - `upsert_total`: total upsert proposals decided.
      - `upsert_with_merge_into_total`: decisions whose source proposal
        carried a `merge_into` hint (audit row's
        `requested_merge_target_id` is non-null).
      - `upsert_without_prior_search_total`: decisions whose source
        proposal was upserted without a prior `search_similar_entities`
        call from the same MCP user (per-session tracker, ≤5 min TTL).

    These are absolute counters scoped by what's still in the audit
    table, not in-process gauges — so they survive process restarts but
    do not include data from before this phase shipped.
    """
    registry = _load_registry()
    record = _find_database(registry, database_id)

    timeout = httpx.Timeout(20.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        rows = await _run_cypher(
            client,
            record,
            """
            MATCH (s:Staging)
            RETURN s.status AS status, count(s) AS count, min(s.created_at) AS oldest
            """,
            {},
        )
        # Adoption counters derived from the audit log. The reconciler now
        # writes `requested_merge_target_id` on every decision; the source
        # Staging vertex carries `had_prior_search`. We persist the latter
        # into the JSON `payload` blob, so we count from the staged shape
        # via the `s` materialized view: but Staging is deleted on
        # promote/merge. Instead, we count from `ReconcilerDecision` rows
        # for upsert proposals (source='upsert_entities' on the original
        # staging payload). The payload is JSON-encoded server-side, so
        # we filter via direct property accessors on the audit row that
        # we explicitly added in Phase 3.
        adoption_rows = await _run_cypher(
            client,
            record,
            """
            MATCH (d:ReconcilerDecision)
            WHERE d.source = 'upsert_entities'
            RETURN
              count(d) AS total,
              size([x IN collect(d) WHERE x.requested_merge_target_id IS NOT NULL])
                AS with_merge_into,
              size([x IN collect(d) WHERE x.had_prior_search <> true
                                       OR x.had_prior_search IS NULL])
                AS without_prior_search
            """,
            {},
        )
    counts: Dict[str, int] = {"pending": 0, "needs_review": 0, "rejected": 0}
    oldest_pending: Optional[str] = None
    for raw in rows or []:
        row = _strip_meta(raw)
        status = str(row.get("status") or "").strip()
        if not status:
            continue
        try:
            counts[status] = int(row.get("count") or 0)
        except (TypeError, ValueError):
            counts[status] = 0
        if status == "pending":
            oldest_pending = row.get("oldest")

    upsert_total = 0
    upsert_with_merge_into = 0
    upsert_without_prior_search = 0
    if adoption_rows:
        adoption = _strip_meta(adoption_rows[0])
        try:
            upsert_total = int(adoption.get("total") or 0)
        except (TypeError, ValueError):
            upsert_total = 0
        try:
            upsert_with_merge_into = int(adoption.get("with_merge_into") or 0)
        except (TypeError, ValueError):
            upsert_with_merge_into = 0
        try:
            upsert_without_prior_search = int(
                adoption.get("without_prior_search") or 0
            )
        except (TypeError, ValueError):
            upsert_without_prior_search = 0

    return {
        "database_id": database_id,
        "backend": "arcadedb",
        "pending_count": counts.get("pending", 0),
        "needs_review_count": counts.get("needs_review", 0),
        "rejected_count": counts.get("rejected", 0),
        "reconciler_lag_seconds": _compute_lag_seconds(oldest_pending),
        "upsert_total": upsert_total,
        "upsert_with_merge_into_total": upsert_with_merge_into,
        "upsert_without_prior_search_total": upsert_without_prior_search,
    }


def _staging_row_to_dict(raw: Any) -> Dict[str, Any]:
    """Flatten a Staging vertex result into a UI-friendly dict.

    Strips `embedding` (large + opaque to the UI) and the ArcadeDB `@rid`
    metadata. Keeps the full `proposed_*` field set so the tab can render
    what the LLM asked for without a second round trip.
    """
    node = _strip_meta(raw).get("s") if isinstance(raw, dict) else None
    if not isinstance(node, dict):
        return {}
    flat = dict(node)
    flat.pop("embedding", None)
    return flat


@router.get("/pending/{database_id}")
async def reconciler_pending(
    database_id: str,
    limit: int = 100,
    _: None = Depends(require_local_auth),
) -> Dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)
    safe_limit = max(1, min(int(limit or 100), 500))

    timeout = httpx.Timeout(20.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        rows = await _run_cypher(
            client,
            record,
            """
            MATCH (s:Staging {status: 'pending'})
            RETURN s
            ORDER BY s.created_at ASC
            LIMIT $limit;
            """,
            {"limit": safe_limit},
        )
    items = [_staging_row_to_dict(row) for row in rows or []]
    return {
        "database_id": database_id,
        "status": "pending",
        "count": len(items),
        "items": [item for item in items if item],
    }


@router.get("/needs-review/{database_id}")
async def reconciler_needs_review(
    database_id: str,
    limit: int = 100,
    _: None = Depends(require_local_auth),
) -> Dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)
    safe_limit = max(1, min(int(limit or 100), 500))

    timeout = httpx.Timeout(20.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        rows = await _run_cypher(
            client,
            record,
            """
            MATCH (s:Staging {status: 'needs_review'})
            RETURN s
            ORDER BY s.updated_at DESC
            LIMIT $limit;
            """,
            {"limit": safe_limit},
        )
    items = [_staging_row_to_dict(row) for row in rows or []]
    return {
        "database_id": database_id,
        "status": "needs_review",
        "count": len(items),
        "items": [item for item in items if item],
    }


@router.get("/decisions/{database_id}")
async def reconciler_decisions(
    database_id: str,
    limit: int = 100,
    _: None = Depends(require_local_auth),
) -> Dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)
    safe_limit = max(1, min(int(limit or 100), 500))

    timeout = httpx.Timeout(20.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        rows = await _run_cypher(
            client,
            record,
            """
            MATCH (d:ReconcilerDecision)
            RETURN d.id AS id,
                   d.staging_id AS staging_id,
                   d.decision AS decision,
                   d.distance AS distance,
                   d.target_id AS target_id,
                   d.reason AS reason,
                   d.requested_merge_target_id AS requested_merge_target_id,
                   d.decided_at AS decided_at,
                   d.reversed AS reversed
            ORDER BY d.decided_at DESC
            LIMIT $limit;
            """,
            {"limit": safe_limit},
        )
    items: List[Dict[str, Any]] = []
    for raw in rows or []:
        row = _strip_meta(raw)
        # Payload is deliberately omitted — it can be large and contains
        # the full staged vertex. The unmerge endpoint reads it on demand
        # from the audit row itself.
        items.append({
            "id": row.get("id"),
            "staging_id": row.get("staging_id"),
            "decision": row.get("decision"),
            "distance": row.get("distance"),
            "target_id": row.get("target_id"),
            "reason": row.get("reason"),
            "requested_merge_target_id": row.get("requested_merge_target_id"),
            "decided_at": row.get("decided_at"),
            "reversed": bool(row.get("reversed")),
        })
    return {
        "database_id": database_id,
        "count": len(items),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Phase 7.2 — per-database threshold read / write
# ---------------------------------------------------------------------------


class ThresholdPatch(BaseModel):
    merge_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    merge_embed_blend: Optional[float] = Field(default=None, ge=0.0, le=1.0)


def _thresholds_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    block = _normalize_reconciler_block(record.get("reconciler"))
    return {
        "database_id": record.get("database_id"),
        "reconciler": block,
        "profile_hash": record.get("profile_hash"),
    }


@router.get("/thresholds/{database_id}")
def reconciler_thresholds_get(
    database_id: str,
    _: None = Depends(require_local_auth),
) -> Dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)
    return _thresholds_payload(record)


@router.patch("/thresholds/{database_id}")
def reconciler_thresholds_patch(
    database_id: str,
    payload: ThresholdPatch,
    _: None = Depends(require_local_auth),
) -> Dict[str, Any]:
    with _registry_lock:
        registry = _load_registry()
        record = _find_database(registry, database_id)
        current = _normalize_reconciler_block(record.get("reconciler"))
        if payload.merge_threshold is not None:
            current["merge_threshold"] = float(payload.merge_threshold)
        if payload.review_threshold is not None:
            current["review_threshold"] = float(payload.review_threshold)
        if payload.merge_embed_blend is not None:
            current["merge_embed_blend"] = float(payload.merge_embed_blend)
        # Re-normalize to enforce the merge ≤ review invariant after the patch.
        record["reconciler"] = _normalize_reconciler_block(current)
        _save_registry(registry)
        # _save_registry normalizes through _ensure_registry_shape, which
        # recomputes profile_hash — reload so the response reflects the
        # persisted hash.
        registry = _load_registry()
        record = _find_database(registry, database_id)
    return _thresholds_payload(record)
