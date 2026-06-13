from __future__ import annotations

import errno
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from ..core.config import (
    DEFAULT_SCHEMA,
    LOCAL_DASHBOARD_ARCADEDB_HOST,
    LOCAL_DASHBOARD_ARCADEDB_PORT,
    LOCAL_DASHBOARD_REGISTRY_FILE,
    _DATABASE_ID_RE,
    _LOOPBACK_HOST_ALIASES,
)
from .schemas import (
    _compute_profile_hash,
    _normalize_reconciler_block,
    _normalize_schema_block,
)

_registry_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_host(value: Any) -> str:
    return str(value or "").strip()


def _is_local_host_alias(value: Any) -> bool:
    lowered = _normalized_host(value).lower()
    if lowered in _LOOPBACK_HOST_ALIASES:
        return True
    configured = LOCAL_DASHBOARD_ARCADEDB_HOST.strip().lower()
    if configured and lowered == configured:
        return True
    return lowered in {"tailscale", "loreholm-tailscale"}


def _resolve_arcadedb_host(record: dict[str, Any]) -> str:
    host = _normalized_host(record.get("host"))
    if _is_local_host_alias(host):
        return LOCAL_DASHBOARD_ARCADEDB_HOST
    return host or LOCAL_DASHBOARD_ARCADEDB_HOST


def _ensure_registry_shape(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"version": 1, "databases": []}
    version = raw.get("version", 1)
    databases = raw.get("databases", [])
    if not isinstance(version, int):
        version = 1
    if not isinstance(databases, list):
        databases = []
    normalized: list[dict[str, Any]] = []
    for item in databases:
        if not isinstance(item, dict):
            continue
        db_id = str(item.get("database_id", "")).strip()
        if not db_id:
            continue
        try:
            profile_version = int(item.get("profile_version", 1))
        except (TypeError, ValueError):
            profile_version = 1
        sslmode = str(item.get("sslmode", "disable")).strip().lower()
        if sslmode not in {"disable", "require"}:
            sslmode = "disable"
        raw_manifest = item.get("tool_manifest")
        tool_manifest = raw_manifest if isinstance(raw_manifest, dict) else {}
        schema_block = _normalize_schema_block(item.get("schema"))
        reconciler_block = _normalize_reconciler_block(item.get("reconciler"))
        raw_system_prompt = item.get("system_prompt", "")
        system_prompt = str(raw_system_prompt) if raw_system_prompt is not None else ""
        recovered_at = item.get("recovered_at")
        recovered_at = str(recovered_at).strip() if recovered_at else None
        recovery_status = item.get("recovery_status")
        recovery_status = str(recovery_status).strip() if recovery_status else None
        raw_backend = str(item.get("backend", "")).strip().lower()
        backend = raw_backend if raw_backend == "arcadedb" else None
        raw_embedding_model = item.get("embedding_model")
        embedding_model = (
            str(raw_embedding_model).strip().lower()
            if raw_embedding_model not in (None, "")
            else None
        )
        raw_embedding_dimension = item.get("embedding_dimension")
        try:
            embedding_dimension = (
                int(raw_embedding_dimension)
                if raw_embedding_dimension not in (None, "")
                else None
            )
        except (TypeError, ValueError):
            embedding_dimension = None
        entry: dict[str, Any] = {
            "database_id": db_id,
            "name": str(item.get("name", db_id)).strip() or db_id,
            "profile_id": (
                str(item.get("profile_id", "")).strip() or "memory-default"
            ),
            "profile_version": profile_version,
            "sslmode": sslmode,
            "schema": schema_block,
            "system_prompt": system_prompt,
            "tool_manifest": tool_manifest,
            "reconciler": reconciler_block,
            "created_at": str(item.get("created_at", _now_iso())),
            "updated_at": str(item.get("updated_at", _now_iso())),
        }
        if recovered_at:
            entry["recovered_at"] = recovered_at
        if recovery_status:
            entry["recovery_status"] = recovery_status
        if backend:
            entry["backend"] = backend
        if embedding_model:
            entry["embedding_model"] = embedding_model
        if embedding_dimension:
            entry["embedding_dimension"] = embedding_dimension
        # Always derive profile_hash from the normalized content so that no
        # caller can accidentally persist a stale hash. See _compute_profile_hash.
        entry["profile_hash"] = _compute_profile_hash(entry)
        normalized.append(entry)
    return {"version": version, "databases": normalized}


def _load_registry() -> dict[str, Any]:
    try:
        raw = json.loads(LOCAL_DASHBOARD_REGISTRY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "databases": []}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "REGISTRY_INVALID_JSON",
                    "message": f"Invalid registry JSON: {exc}",
                }
            },
        ) from exc
    return _ensure_registry_shape(raw)


def _save_registry(registry: dict[str, Any]) -> None:
    normalized = _ensure_registry_shape(registry)
    LOCAL_DASHBOARD_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = LOCAL_DASHBOARD_REGISTRY_FILE.with_suffix(
        f"{LOCAL_DASHBOARD_REGISTRY_FILE.suffix}.tmp-{secrets.token_hex(6)}"
    )
    payload = json.dumps(normalized, indent=2, ensure_ascii=True) + "\n"
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        try:
            os.replace(tmp_path, LOCAL_DASHBOARD_REGISTRY_FILE)
        except OSError as exc:
            if exc.errno != errno.EBUSY:
                raise
            # Bind-mounted files can behave like mount points and reject replace.
            with LOCAL_DASHBOARD_REGISTRY_FILE.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _backfill_registry_if_needed() -> None:
    """Startup helper: ensures every record in `databases.json` has a `schema`
    block and `profile_hash`. A record missing these is a pre-multi-schema
    install; we idempotently write `DEFAULT_SCHEMA` and recompute the hash.

    Also handles the "file missing entirely" case by attempting a rebuild from
    the shared ArcadeDB server (see `_rebuild_registry_from_server`). Silent
    on steady-state boots; logs on back-fill actions.
    """
    if not LOCAL_DASHBOARD_REGISTRY_FILE.exists():
        try:
            rebuilt = _rebuild_registry_from_server()
        except Exception as exc:  # pragma: no cover - best effort recovery
            print(
                f"[local-dashboard] registry rebuild failed: {exc}",
                flush=True,
            )
            return
        if rebuilt:
            print(
                f"[local-dashboard] Recovered {len(rebuilt)} database(s) from "
                "the shared ArcadeDB server. Review and update schema "
                "descriptions in the dashboard schema editor.",
                flush=True,
            )
        return

    try:
        raw = json.loads(LOCAL_DASHBOARD_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    dirty = False
    for item in raw.get("databases", []) or []:
        if not isinstance(item, dict):
            continue
        if not isinstance(item.get("schema"), dict):
            item["schema"] = json.loads(json.dumps(DEFAULT_SCHEMA))
            dirty = True
        if not str(item.get("profile_hash", "")).strip():
            dirty = True  # ensure_registry_shape will recompute it on save
    if dirty:
        try:
            _save_registry(raw)
            print(
                "[local-dashboard] Back-filled schema/profile_hash fields on "
                "pre-multi-schema registry records.",
                flush=True,
            )
        except Exception as exc:  # pragma: no cover
            print(f"[local-dashboard] registry back-fill failed: {exc}", flush=True)


def _rebuild_registry_from_server() -> list[dict[str, Any]]:
    """Reconstruct `databases.json` from the shared ArcadeDB server when the
    file is missing entirely. Lists databases via
    `arcadedb_server.list_databases()` and creates a skeleton record per
    database with a best-effort schema introspection.

    Returns the list of recovered records (empty if the server is
    unreachable or reports no databases).
    """
    # Lazy import to avoid circulars on cold module load.
    from .arcadedb_server import list_databases

    try:
        names = list_databases()
    except Exception:
        return []
    if not names:
        return []

    from .cypher import _run_query

    recovered: list[dict[str, Any]] = []
    for raw_name in names:
        database_id = str(raw_name or "").strip()
        if not database_id or not _DATABASE_ID_RE.match(database_id):
            continue

        now = _now_iso()
        record: dict[str, Any] = {
            "database_id": database_id,
            "name": database_id,
            "profile_id": "memory-default",
            "profile_version": 1,
            "sslmode": "disable",
            "schema": json.loads(json.dumps(DEFAULT_SCHEMA)),
            "tool_manifest": {},
            "backend": "arcadedb",
            "recovered_at": now,
            "created_at": now,
            "updated_at": now,
        }

        # Best-effort introspection: pull labels and relationship types
        # into a draft schema. Failures fall through to the "skeleton" state.
        recovery_status = "skeleton"
        try:
            _, label_rows = _run_query(
                record,
                "MATCH (n) UNWIND labels(n) AS label RETURN DISTINCT label ORDER BY label;",
            )
            for row in label_rows:
                if not row:
                    continue
                label = str(row[0])
                if not label or label in {"Memory", "Conversation", "Message"}:
                    continue
                record["schema"]["entity_types"].append(
                    {
                        "name": label,
                        "description": (
                            "Recovered from existing data — please add "
                            "a real description in the schema editor."
                        ),
                    }
                )
            _, rel_rows = _run_query(
                record,
                "MATCH ()-[r]->() RETURN DISTINCT type(r) AS relationship ORDER BY relationship;",
            )
            for row in rel_rows:
                if not row:
                    continue
                rel = str(row[0])
                if not rel:
                    continue
                record["schema"]["relationship_types"].append(
                    {
                        "name": rel,
                        "description": (
                            "Recovered from existing data — please add "
                            "a real description in the schema editor."
                        ),
                    }
                )
            recovery_status = "introspected"
        except Exception:
            recovery_status = "skeleton"
        record["recovery_status"] = recovery_status
        recovered.append(record)

    if not recovered:
        return []

    registry = {"version": 1, "databases": recovered}
    try:
        _save_registry(registry)
    except Exception:
        return []
    return recovered


def _find_database(registry: dict[str, Any], database_id: str) -> dict[str, Any]:
    for record in registry.get("databases", []):
        if record.get("database_id") == database_id:
            return record
    raise HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": "DATABASE_NOT_FOUND",
                "message": f"Unknown database_id '{database_id}'.",
            }
        },
    )


def _slugify_database_id(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", lowered)
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    if not slug:
        slug = "memory-db"
    if not re.match(r"^[a-z0-9]", slug):
        slug = f"db-{slug}"
    slug = slug[:100].strip("-_")
    if not slug:
        slug = "memory-db"
    if not _DATABASE_ID_RE.match(slug):
        slug = "memory-db"
    return slug
