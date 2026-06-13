from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..core.auth import _now_iso, require_local_session
from ..core.config import (
    DEFAULT_SCHEMA,
    LOCAL_DASHBOARD_ARCADEDB_HOST,
    LOCAL_DASHBOARD_ARCADEDB_PORT,
    _DATABASE_ID_RE,
)
from ..db.cypher import _run_query, _safe_query
from ..db import arcadedb_server
from ..db.arcadedb_bootstrap import bootstrap_database
from ..db.graph import (
    _build_graph_query,
    _database_status,
    _database_summary,
    _rows_to_graph,
    _schema_payload,
)
from ..core.models import (
    AuthoredSchemaRenameRequest,
    AuthoredSchemaTypeRequest,
    CreateDatabaseRequest,
    GraphRequest,
    QueryRequest,
)
from ..db.registry import (
    _find_database,
    _load_registry,
    _rebuild_registry_from_server,
    _registry_lock,
    _resolve_arcadedb_host,
    _save_registry,
)
from ..db.schemas import (
    _compute_profile_hash,
    _delete_authored_type,
    _normalize_schema_block,
    _rename_authored_type,
    _upsert_authored_type,
)

router = APIRouter()


@router.get("/databases")
async def list_databases(_: None = Depends(require_local_session)) -> dict[str, Any]:
    registry = _load_registry()
    databases = [_database_summary(record) for record in registry.get("databases", [])]
    return {"databases": databases, "count": len(databases)}


@router.post("/databases")
def create_database(
    payload: CreateDatabaseRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    database_id = payload.database_id.strip().lower()
    if not _DATABASE_ID_RE.match(database_id):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_DATABASE_ID",
                    "message": (
                        "database_id must match ^[a-z0-9][a-z0-9_-]{0,99}$ "
                        "(lowercase letters, numbers, underscore, hyphen)."
                    ),
                }
            },
        )

    display_name = payload.name.strip()
    if not display_name:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_DATABASE_NAME",
                    "message": "name is required.",
                }
            },
        )

    sslmode = str(payload.sslmode).strip().lower() or "disable"
    backend = "arcadedb"

    with _registry_lock:
        registry = _load_registry()
        existing = [r for r in registry.get("databases", []) if r.get("database_id") == database_id]
        if existing:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "DATABASE_ALREADY_EXISTS",
                        "message": f"database_id '{database_id}' already exists.",
                    }
                },
            )

        try:
            arcadedb_server.wait_for_server_ready(timeout_s=30.0)
            arcadedb_server.create_database(database_id)
            bootstrap_meta = bootstrap_database(
                host=LOCAL_DASHBOARD_ARCADEDB_HOST,
                port=LOCAL_DASHBOARD_ARCADEDB_PORT,
                database_id=database_id,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": {
                        "code": "DATABASE_CREATE_FAILED",
                        "message": str(exc),
                    }
                },
            ) from exc

        now = _now_iso()
        record = {
            "database_id": database_id,
            "name": display_name,
            "profile_id": "memory-default",
            "profile_version": 1,
            "sslmode": sslmode,
            "schema": json.loads(json.dumps(DEFAULT_SCHEMA)),
            "tool_manifest": {},
            "backend": backend,
            "embedding_model": bootstrap_meta.get("embedding_model"),
            "embedding_dimension": bootstrap_meta.get("embedding_dimension"),
            "created_at": now,
            "updated_at": now,
        }
        # profile_hash is derived inside _save_registry via _ensure_registry_shape.
        registry.setdefault("databases", []).append(record)
        try:
            _save_registry(registry)
        except Exception as exc:
            # Best-effort rollback: drop the freshly-created database if we
            # couldn't persist the registry record.
            try:
                arcadedb_server.drop_database(database_id)
            except Exception:
                pass
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": "REGISTRY_WRITE_FAILED",
                        "message": f"Failed to persist databases registry: {exc}",
                    }
                },
            ) from exc

    return {
        "database": _database_summary(record),
        "database_created": True,
        "warnings": [],
    }


@router.post("/databases/rebuild")
def rebuild_registry_from_server(
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    """Manual recovery path: rebuild `databases.json` from the shared ArcadeDB
    server. Idempotent. Use when the registry file got corrupted or was
    deleted while the dashboard was running. Automatic startup recovery
    handles the common "file missing" case; this endpoint exists for the
    explicit operator flow.
    """
    recovered = _rebuild_registry_from_server()
    return {
        "recovered_count": len(recovered),
        "databases": [
            {
                "database_id": rec.get("database_id"),
                "recovery_status": rec.get("recovery_status"),
            }
            for rec in recovered
        ],
    }


@router.get("/databases/{database_id}")
def get_database(
    database_id: str,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)
    return _database_summary(record)


@router.delete("/databases/{database_id}")
def delete_database(
    database_id: str,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    with _registry_lock:
        registry = _load_registry()
        _find_database(registry, database_id)
        registry["databases"] = [
            r for r in registry.get("databases", []) if r.get("database_id") != database_id
        ]
        _save_registry(registry)
    try:
        arcadedb_server.drop_database(database_id)
    except RuntimeError as exc:
        # Surface the failure but don't put the registry record back — a
        # stale database on the server is recoverable manually, whereas a
        # lingering registry entry would block re-creation.
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "DATABASE_DROP_FAILED",
                    "message": str(exc),
                }
            },
        ) from exc
    return {"deleted": database_id}


@router.get("/databases/{database_id}/health")
def get_database_health(
    database_id: str,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)

    _, node_count_rows = _safe_query(record, "MATCH (n) RETURN count(n) AS node_count;")
    _, edge_count_rows = _safe_query(record, "MATCH ()-[r]->() RETURN count(r) AS edge_count;")

    connected = _database_status(record) == "online"
    return {
        "connected": connected,
        "database_id": database_id,
        "host": _resolve_arcadedb_host(record),
        "port": LOCAL_DASHBOARD_ARCADEDB_PORT,
        "node_count": int(node_count_rows[0][0]) if node_count_rows else 0,
        "edge_count": int(edge_count_rows[0][0]) if edge_count_rows else 0,
        "engine": "ArcadeDB",
    }


@router.get("/databases/{database_id}/schema")
def get_database_schema(
    database_id: str,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)
    return _schema_payload(record)


# --- Phase 6: authored-schema editor endpoints --------------------------
#
# These endpoints operate on the per-database `schema` block stored in
# `databases.json`, not the live ArcadeDB labels/relationships exposed by
# `/databases/{id}/schema` above. They are the backend for the schema editor
# UI tab. Writes go through `_save_registry`, which recomputes `profile_hash`
# so the cloud's next proxy call observes the new hash and pulls a refresh.


def _authored_schema_response(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": _normalize_schema_block(record.get("schema")),
        "profile_hash": _compute_profile_hash(record),
    }


@router.get("/databases/{database_id}/authored-schema")
def get_authored_schema(
    database_id: str,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)
    return _normalize_schema_block(record.get("schema"))


@router.put("/databases/{database_id}/authored-schema/entity-types")
def upsert_authored_entity_type(
    database_id: str,
    payload: AuthoredSchemaTypeRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    with _registry_lock:
        registry = _load_registry()
        record = _find_database(registry, database_id)
        result = _upsert_authored_type(
            record,
            kind="entity",
            name=payload.name,
            description=payload.description,
        )
        _save_registry(registry)
    return {"entity_type": result, **_authored_schema_response(record)}


@router.delete("/databases/{database_id}/authored-schema/entity-types/{name}")
def delete_authored_entity_type(
    database_id: str,
    name: str,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    with _registry_lock:
        registry = _load_registry()
        record = _find_database(registry, database_id)
        removed = _delete_authored_type(record, kind="entity", name=name)
        if not removed:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "code": "ENTITY_TYPE_NOT_FOUND",
                        "message": f"Entity type '{name}' not found.",
                    }
                },
            )
        _save_registry(registry)
    return {"deleted": name, **_authored_schema_response(record)}


@router.post("/databases/{database_id}/authored-schema/entity-types/rename")
def rename_authored_entity_type(
    database_id: str,
    payload: AuthoredSchemaRenameRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    with _registry_lock:
        registry = _load_registry()
        record = _find_database(registry, database_id)
        result = _rename_authored_type(
            record,
            kind="entity",
            old_name=payload.old_name,
            new_name=payload.new_name,
            description=payload.description,
        )
        _save_registry(registry)
    return {"entity_type": result, **_authored_schema_response(record)}


@router.put("/databases/{database_id}/authored-schema/relationship-types")
def upsert_authored_relationship_type(
    database_id: str,
    payload: AuthoredSchemaTypeRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    with _registry_lock:
        registry = _load_registry()
        record = _find_database(registry, database_id)
        result = _upsert_authored_type(
            record,
            kind="relationship",
            name=payload.name,
            description=payload.description,
        )
        _save_registry(registry)
    return {"relationship_type": result, **_authored_schema_response(record)}


@router.delete(
    "/databases/{database_id}/authored-schema/relationship-types/{name}"
)
def delete_authored_relationship_type(
    database_id: str,
    name: str,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    with _registry_lock:
        registry = _load_registry()
        record = _find_database(registry, database_id)
        removed = _delete_authored_type(record, kind="relationship", name=name)
        if not removed:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "code": "RELATIONSHIP_TYPE_NOT_FOUND",
                        "message": f"Relationship type '{name}' not found.",
                    }
                },
            )
        _save_registry(registry)
    return {"deleted": name, **_authored_schema_response(record)}


@router.post("/databases/{database_id}/authored-schema/relationship-types/rename")
def rename_authored_relationship_type(
    database_id: str,
    payload: AuthoredSchemaRenameRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    with _registry_lock:
        registry = _load_registry()
        record = _find_database(registry, database_id)
        result = _rename_authored_type(
            record,
            kind="relationship",
            old_name=payload.old_name,
            new_name=payload.new_name,
            description=payload.description,
        )
        _save_registry(registry)
    return {"relationship_type": result, **_authored_schema_response(record)}


@router.post("/databases/{database_id}/query")
def query_database(
    database_id: str,
    payload: QueryRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    # The previous v1 guard here rejected any query containing a mutation
    # keyword, which blocked the UI's Query Console and the wizard's
    # "Run generated query" button from doing any schema/data writes.
    # Auth is already enforced via require_local_session — anyone holding
    # a valid session cookie can already hit the wizard's run_query tool
    # with arbitrary Cypher, so there is no extra trust boundary to protect
    # here. Writes are allowed.
    if not (payload.cypher or "").strip():
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_QUERY", "message": "Query is empty."}},
        )
    registry = _load_registry()
    record = _find_database(registry, database_id)
    columns, rows = _run_query(record, payload.cypher, payload.params)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
    }


@router.post("/databases/{database_id}/graph")
def get_graph(
    database_id: str,
    payload: GraphRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)
    query, params = _build_graph_query(payload)
    _, rows = _run_query(record, query, params)
    return _rows_to_graph(rows, payload.limit_nodes)


@router.get("/databases/{database_id}/profile")
async def get_database_profile(
    database_id: str,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)
    return {
        "database_id": database_id,
        "profile_id": record.get("profile_id", "memory-default"),
        "profile_version": int(record.get("profile_version", 1)),
        "tool_schema_status": "deferred",
    }
