from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..core.auth import _now_iso, require_sync_auth
from ..db.arcadedb_query import run_arcadedb_query
from ..db.embedding_hook import rewrite_embed_placeholders
from ..db.graph import _database_status
from ..core.models import ProxyQueryRequest, SyncResolveRequest
from ..db.policies import _evaluate_policy
from ..db.registry import _find_database, _load_registry
from ..db.schemas import _compute_profile_hash, _normalize_schema_block

router = APIRouter()


@router.post("/sync/database-targets/resolve")
def sync_resolve_database_target(
    payload: SyncResolveRequest,
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    """Return profile/schema/tool_manifest metadata for a local database.

    Under the query-proxy topology the cloud never dials the database
    directly — every query flows back through `/sync/query` on this local
    dashboard, and the local side owns the ArcadeDB connection details. As a
    result the resolve response carries only logical identifiers and the
    profile payload the cloud caches as staleness metadata; no host, port,
    username, password, or sslmode leaves the local process.
    """
    registry = _load_registry()
    record = _find_database(registry, payload.database_id)

    profile_id = str(record.get("profile_id", "memory-default")).strip() or "memory-default"
    try:
        profile_version = int(record.get("profile_version", 1))
    except (TypeError, ValueError):
        profile_version = 1
    raw_manifest = record.get("tool_manifest")
    tool_manifest = raw_manifest if isinstance(raw_manifest, dict) else {}
    schema_block = _normalize_schema_block(record.get("schema"))
    profile_hash = str(record.get("profile_hash", "")).strip() or _compute_profile_hash(record)

    return {
        "database_id": record["database_id"],
        "profile": {
            "profile_id": profile_id,
            "profile_version": profile_version,
            "profile_hash": profile_hash,
            "schema": schema_block,
            "tool_manifest": tool_manifest,
        },
    }


@router.post("/sync/query")
def sync_proxy_query(
    payload: ProxyQueryRequest,
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    """Proxy endpoint for cloud->local Cypher execution. Every cloud-side
    ArcadeDB access flows through this endpoint over HTTP.

    Each response carries `profile_hash` so the cloud can cheaply detect
    schema staleness and refresh without a separate sync round-trip.
    """
    registry = _load_registry()
    record = _find_database(registry, payload.database_id)

    decision = _evaluate_policy(payload, database_record=record)
    if decision is not None:
        return {"error": decision}

    # Resolve any `{{embed:<param>}}` placeholders into inline embedding
    # vectors before handing the query off to ArcadeDB. The hook produces
    # `$name__embedding` for Cypher and `:name__embedding` for SQL.
    try:
        rewritten_cypher, rewritten_parameters = rewrite_embed_placeholders(
            payload.cypher, payload.parameters or {}, language=payload.language
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "EMBEDDING_HOOK_FAILED",
                    "message": f"Embedding hook failed: {exc}",
                }
            },
        ) from exc

    columns, rows = run_arcadedb_query(
        record, rewritten_cypher, rewritten_parameters, language=payload.language
    )

    profile_hash = (
        str(record.get("profile_hash", "")).strip()
        or _compute_profile_hash(record)
    )

    return {
        "database_id": record["database_id"],
        "profile_hash": profile_hash,
        "columns": columns,
        "rows": rows,
        "summary": {
            "row_count": len(rows),
        },
    }


@router.get("/sync/databases")
def sync_list_databases(
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    """Discovery endpoint: returns the full inventory of databases on this
    device. Includes offline databases so the cloud dashboard can show users
    their full choice list when binding an API key to a database.
    """
    registry = _load_registry()
    items: list[dict[str, Any]] = []
    for record in registry.get("databases", []):
        if not isinstance(record, dict):
            continue
        status = _database_status(record)
        items.append(
            {
                "database_id": record.get("database_id"),
                "name": record.get("name", record.get("database_id")),
                "profile_id": record.get("profile_id", "memory-default"),
                "profile_hash": record.get("profile_hash"),
                "status": status,
                "last_seen_at": _now_iso() if status == "online" else None,
                "recovered_at": record.get("recovered_at"),
                "recovery_status": record.get("recovery_status"),
            }
        )
    return {"databases": items, "count": len(items)}


@router.get("/sync/databases/{database_id}/schema")
def sync_get_database_schema(
    database_id: str,
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    """Returns the authored schema for one database (no connection info).

    Useful for re-fetching just the schema from the cloud dashboard's
    schema-edit view without re-pulling credentials.
    """
    registry = _load_registry()
    record = _find_database(registry, database_id)
    return {
        "database_id": record["database_id"],
        "profile_hash": record.get("profile_hash"),
        "schema": _normalize_schema_block(record.get("schema")),
    }
