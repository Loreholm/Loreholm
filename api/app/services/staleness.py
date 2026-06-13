"""Phase 5 staleness detection — schema cache freshness.

Every proxy response carries the observed `profile_hash` (see Phase 0
envelope). The cloud compares it against the value cached on the
`database_targets` row. On mismatch, we pull the full profile from the
local dashboard and update the cache so the next request sees the fresh
schema.

Two entry points:
- `ensure_schema_cached(user_id, target_id)` — blocking cold-start pull
  used when `schema_json` is NULL on the target row.
- `maybe_refresh_target_cache(...)` — fire-and-forget refresh triggered
  after a proxy call whose observed hash differs from the cached hash.

Writes aren't retried after a mismatch: by the time the cloud notices,
the proxy has already committed the write against the user's actual
schema. Re-running the Cypher would duplicate the write. Instead, we
refresh the cache so the *next* request's tool-surface composition and
write-validation reflect the new vocabulary. The user sees at most one
stale `tools/list` before the refresh completes — acceptable per the
migration plan's write-strict/read-loose tradeoff.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.services.database_targets import (
    get_database_target_for_routing,
    update_profile_hash_and_schema,
    upsert_database_target_from_sync,
)
from app.services.local_sync import LocalSyncError, fetch_local_database_sync_payload


logger = logging.getLogger(__name__)


async def ensure_schema_cached(
    user_id: str,
    target_id: str,
) -> Optional[dict]:
    """Blocking cold-start: ensure `schema_json` is populated for a target.

    Returns the hydrated target dict, or None if the local dashboard is
    unreachable. Callers should treat None as "unable to load schema,
    surface an error to the user" and not silently substitute a default.
    """
    target = await get_database_target_for_routing(user_id, target_id)
    if not target:
        return None
    if isinstance(target.get("schema_json"), dict):
        return target
    database_id = target.get("database_id")
    if not database_id:
        return None
    try:
        sync_payload = await fetch_local_database_sync_payload(user_id, database_id)
    except LocalSyncError as exc:
        logger.warning(
            "Cold-start schema sync failed for user=%s target=%s database=%s: %s",
            user_id,
            target_id,
            database_id,
            exc,
        )
        return target  # Return stale (possibly NULL-schema) record as-is.
    try:
        await upsert_database_target_from_sync(user_id, sync_payload)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Cold-start schema persistence failed for target=%s: %s",
            target_id,
            exc,
        )
        return target
    return await get_database_target_for_routing(user_id, target_id)


async def _refresh_target_cache_inner(
    user_id: str,
    target_id: str,
    database_id: str,
) -> None:
    try:
        sync_payload = await fetch_local_database_sync_payload(user_id, database_id)
    except LocalSyncError as exc:
        logger.info(
            "Background schema refresh skipped (local dashboard unreachable) "
            "user=%s target=%s: %s",
            user_id,
            target_id,
            exc,
        )
        return
    profile = sync_payload.get("profile") or {}
    new_hash = profile.get("profile_hash")
    new_schema = profile.get("schema")
    if not isinstance(new_hash, str) or not new_hash:
        logger.info(
            "Background schema refresh: payload missing profile_hash for target=%s",
            target_id,
        )
        return
    try:
        await update_profile_hash_and_schema(
            target_id,
            profile_hash=new_hash,
            schema_json=new_schema if isinstance(new_schema, dict) else None,
            tool_manifest=profile.get("tool_manifest"),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Background schema refresh write failed for target=%s: %s",
            target_id,
            exc,
        )
        return
    logger.info(
        "Background schema refresh ok target=%s hash=%s",
        target_id,
        new_hash[:12],
    )


def maybe_refresh_target_cache(
    *,
    user_id: str,
    target_id: Optional[str],
    database_id: Optional[str],
    observed_hash: Optional[str],
    cached_hash: Optional[str],
) -> None:
    """Fire-and-forget cache refresh when the observed hash diverges.

    Safe to call from a sync or async context. If there's no running event
    loop (unusual for the cloud API path), the refresh is dropped — the
    next request will re-detect the mismatch and try again.
    """
    if not target_id or not database_id or not observed_hash:
        return
    if cached_hash and observed_hash == cached_hash:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        _refresh_target_cache_inner(user_id, target_id, database_id)
    )
