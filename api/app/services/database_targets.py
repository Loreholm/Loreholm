from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote_plus
from uuid import uuid4

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised when psycopg isn't installed.
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]


class DatabaseTargetError(Exception):
    """Base exception for database-target operations."""


class DatabaseTargetConflictError(DatabaseTargetError):
    """Raised when a per-user target name already exists."""


# Cloud-side database_targets is purely a profile/schema/tool_manifest cache.
# Under the query-proxy topology the cloud never dials the user's ArcadeDB
# directly — every MCP request is forwarded to the user's local dashboard via
# `POST /api/sync/query`, and the local dashboard looks up credentials from
# its own `databases.json`. No host, username, password, port, or sslmode is
# stored here.
@dataclass
class DatabaseTargetRecord:
    target_id: str
    user_id: str
    name: str
    profile_id: str
    profile_hash: Optional[str]
    schema_json: Optional[dict[str, Any]]
    tool_manifest: dict[str, Any]
    published_at: Optional[str]
    created_at: str
    updated_at: str


_initialized = False
_init_lock = asyncio.Lock()
_unique_violation = getattr(getattr(psycopg, "errors", None), "UniqueViolation", None)


def _require_psycopg() -> None:
    if psycopg is None:
        raise RuntimeError("psycopg is required for database target operations.")


def _require_pg_config() -> dict[str, str | int]:
    db = (os.getenv("PG_DB") or "").strip()
    user = (os.getenv("PG_USER") or "").strip()
    password = (os.getenv("PG_PW") or "").strip()
    server = (os.getenv("PG_SERVER") or "").strip()
    port_raw = (os.getenv("PG_PORT") or "5432").strip()

    missing = []
    if not db:
        missing.append("PG_DB")
    if not user:
        missing.append("PG_USER")
    if not password:
        missing.append("PG_PW")
    if not server:
        missing.append("PG_SERVER")
    if missing:
        raise RuntimeError(
            f"Missing Postgres config for database targets: {', '.join(missing)}"
        )

    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        raise RuntimeError("PG_PORT must be an integer")
    if port < 1 or port > 65535:
        raise RuntimeError("PG_PORT must be in range 1..65535")

    return {
        "db": db,
        "user": user,
        "password": password,
        "server": server,
        "port": port,
    }


def _pg_dsn() -> str:
    cfg = _require_pg_config()
    return (
        "postgresql://"
        f"{quote_plus(str(cfg['user']))}:{quote_plus(str(cfg['password']))}"
        f"@{quote_plus(str(cfg['server']))}:{cfg['port']}/{quote_plus(str(cfg['db']))}"
    )


async def _connect():
    _require_psycopg()
    return await psycopg.AsyncConnection.connect(  # type: ignore[union-attr]
        _pg_dsn(),
        autocommit=True,
    )


async def ensure_database_target_schema() -> None:
    global _initialized
    if _initialized:
        return

    async with _init_lock:
        if _initialized:
            return
        async with await _connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS database_targets (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (user_id, name)
                    );
                    """
                )
                await cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_database_targets_user_id
                    ON database_targets (user_id);
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    ADD COLUMN IF NOT EXISTS profile_id TEXT NOT NULL DEFAULT 'memory-default';
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    ADD COLUMN IF NOT EXISTS schema_hash TEXT NULL;
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    ADD COLUMN IF NOT EXISTS tool_manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb;
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ NULL;
                    """
                )
                # Multi-schema migration (Phase 3):
                #   - rename `schema_hash` → `profile_hash` (now covers every
                #     column that affects MCP serving, not just schema).
                #   - add `schema_json` JSONB (nullable; NULL = cold-start).
                #   - drop `profile_version`; replaced by content-derived
                #     profile_hash as the sole staleness signal.
                # Migration is idempotent — the column checks below guard
                # against re-running on already-migrated databases.
                await cur.execute(
                    """
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'database_targets' AND column_name = 'schema_hash'
                        ) AND NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'database_targets' AND column_name = 'profile_hash'
                        ) THEN
                            ALTER TABLE database_targets RENAME COLUMN schema_hash TO profile_hash;
                        END IF;
                    END $$;
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    ADD COLUMN IF NOT EXISTS profile_hash TEXT NULL;
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    ADD COLUMN IF NOT EXISTS schema_json JSONB NULL;
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    DROP COLUMN IF EXISTS profile_version;
                    """
                )
                # Phase 0 query-proxy rip-out: drop every cloud-side
                # database connection field. The cloud never dials the
                # user's database directly; the local dashboard is the
                # client. Keeping these columns was a trust-footprint
                # anti-pattern (the whole BYODB pitch is that credentials
                # stay local) and post-pivot they were dead weight — no
                # read path consumed them.
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    DROP COLUMN IF EXISTS password_encrypted;
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    DROP COLUMN IF EXISTS username;
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    DROP COLUMN IF EXISTS host;
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    DROP COLUMN IF EXISTS port;
                    """
                )
                await cur.execute(
                    """
                    ALTER TABLE database_targets
                    DROP COLUMN IF EXISTS sslmode;
                    """
                )
        _initialized = True


def _normalize_target_payload(payload: dict[str, Any], *, require_name: bool) -> dict[str, Any]:
    name = payload.get("name")
    if name is None:
        name = payload.get("database_id")
    if isinstance(name, str):
        name = name.strip()
    else:
        name = ""
    if require_name and not name:
        raise ValueError("Database target name is required.")
    return {"name": name}


_SCHEMA_JSON_SENTINEL = object()


def _normalize_profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_profile_id = payload.get("profile_id", "memory-default")
    profile_id = str(raw_profile_id).strip() or "memory-default"

    raw_profile_hash = payload.get("profile_hash")
    profile_hash = (
        str(raw_profile_hash).strip() if raw_profile_hash is not None else None
    )
    profile_hash = profile_hash or None
    if profile_hash and len(profile_hash) > 255:
        raise ValueError("profile_hash must be at most 255 characters.")

    raw_manifest = payload.get("tool_manifest")
    if raw_manifest is None:
        tool_manifest: dict[str, Any] = {}
    elif isinstance(raw_manifest, dict):
        tool_manifest = raw_manifest
    else:
        raise ValueError("tool_manifest must be a JSON object.")
    try:
        json.dumps(tool_manifest)
    except (TypeError, ValueError) as exc:
        raise ValueError("tool_manifest must be JSON serializable.") from exc

    # schema_json may be explicitly omitted (sentinel -> don't touch column),
    # explicitly None (write NULL), or a dict (write JSON). This distinction
    # matters because callers updating profile metadata should not overwrite
    # a previously-synced schema.
    if "schema_json" in payload:
        raw_schema = payload["schema_json"]
        if raw_schema is None:
            schema_json: Any = None
        elif isinstance(raw_schema, dict):
            try:
                json.dumps(raw_schema)
            except (TypeError, ValueError) as exc:
                raise ValueError("schema_json must be JSON serializable.") from exc
            schema_json = raw_schema
        else:
            raise ValueError("schema_json must be a JSON object or null.")
    else:
        schema_json = _SCHEMA_JSON_SENTINEL

    return {
        "profile_id": profile_id,
        "profile_hash": profile_hash,
        "schema_json": schema_json,
        "tool_manifest": tool_manifest,
    }


def _row_to_record(row: dict[str, Any]) -> DatabaseTargetRecord:
    schema_value = row.get("schema_json")
    if schema_value is None:
        schema_json: Optional[dict[str, Any]] = None
    elif isinstance(schema_value, dict):
        schema_json = schema_value
    elif isinstance(schema_value, (str, bytes)):
        try:
            parsed = json.loads(schema_value)
        except (TypeError, ValueError):
            parsed = None
        schema_json = parsed if isinstance(parsed, dict) else None
    else:
        schema_json = None
    return DatabaseTargetRecord(
        target_id=str(row["id"]),
        user_id=str(row["user_id"]),
        name=str(row["name"]),
        profile_id=str(row.get("profile_id") or "memory-default"),
        profile_hash=row.get("profile_hash"),
        schema_json=schema_json,
        tool_manifest=row.get("tool_manifest_json")
        if isinstance(row.get("tool_manifest_json"), dict)
        else {},
        published_at=(
            row["published_at"].isoformat()
            if row.get("published_at") is not None
            else None
        ),
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


def _record_to_public(record: DatabaseTargetRecord) -> dict[str, Any]:
    return {
        "target_id": record.target_id,
        "name": record.name,
        "database_id": record.name,
        "profile_id": record.profile_id,
        "profile_hash": record.profile_hash,
        "schema_json": record.schema_json,
        "published_at": record.published_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


async def _fetch_one(query: str, params: tuple[Any, ...]) -> Optional[dict[str, Any]]:
    await ensure_database_target_schema()
    async with await _connect() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[arg-type]
            await cur.execute(query, params)
            row = await cur.fetchone()
            return dict(row) if row else None


async def _fetch_all(query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    await ensure_database_target_schema()
    async with await _connect() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[arg-type]
            await cur.execute(query, params)
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def create_database_target(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_target_payload(payload, require_name=True)
    profile = _normalize_profile_payload(payload)

    target_id = f"dt_{uuid4().hex}"
    # schema_json defaults to NULL on insert when not provided so the cold-start
    # path can distinguish "never synced" from "synced and empty".
    schema_json_param: Optional[str]
    if profile["schema_json"] is _SCHEMA_JSON_SENTINEL or profile["schema_json"] is None:
        schema_json_param = None
    else:
        schema_json_param = json.dumps(profile["schema_json"])
    query = """
        INSERT INTO database_targets (
            id, user_id, name,
            profile_id, profile_hash, schema_json, tool_manifest_json, published_at
        )
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, NOW())
        RETURNING id, user_id, name,
                  profile_id, profile_hash, schema_json, tool_manifest_json, published_at,
                  created_at, updated_at;
    """

    try:
        row = await _fetch_one(
            query,
            (
                target_id,
                user_id,
                normalized["name"],
                profile["profile_id"],
                profile["profile_hash"],
                schema_json_param,
                json.dumps(profile["tool_manifest"]),
            ),
        )
    except Exception as exc:
        if _unique_violation and isinstance(exc, _unique_violation):
            raise DatabaseTargetConflictError(
                f"Database target '{normalized['name']}' already exists."
            ) from exc
        if "duplicate key value violates unique constraint" in str(exc).lower():
            raise DatabaseTargetConflictError(
                f"Database target '{normalized['name']}' already exists."
            ) from exc
        raise
    if not row:
        raise RuntimeError("Failed to create database target.")
    return _record_to_public(_row_to_record(row))


async def list_database_targets(user_id: str) -> list[dict[str, Any]]:
    rows = await _fetch_all(
        """
        SELECT id, user_id, name,
               profile_id, profile_hash, schema_json, tool_manifest_json, published_at,
               created_at, updated_at
        FROM database_targets
        WHERE user_id = %s
        ORDER BY created_at DESC;
        """,
        (user_id,),
    )
    return [_record_to_public(_row_to_record(row)) for row in rows]


async def get_database_target(user_id: str, target_id: str) -> Optional[dict[str, Any]]:
    row = await _fetch_one(
        """
        SELECT id, user_id, name,
               profile_id, profile_hash, schema_json, tool_manifest_json, published_at,
               created_at, updated_at
        FROM database_targets
        WHERE user_id = %s AND id = %s;
        """,
        (user_id, target_id),
    )
    if not row:
        return None
    return _record_to_public(_row_to_record(row))


async def get_database_target_by_name(user_id: str, name: str) -> Optional[dict[str, Any]]:
    row = await _fetch_one(
        """
        SELECT id, user_id, name,
               profile_id, profile_hash, schema_json, tool_manifest_json, published_at,
               created_at, updated_at
        FROM database_targets
        WHERE user_id = %s AND name = %s;
        """,
        (user_id, name),
    )
    if not row:
        return None
    return _record_to_public(_row_to_record(row))


async def update_database_target(
    user_id: str,
    target_id: str,
    payload: dict[str, Any],
) -> Optional[dict[str, Any]]:
    existing = await _fetch_one(
        """
        SELECT id, user_id, name,
               profile_id, profile_hash, schema_json, tool_manifest_json, published_at,
               created_at, updated_at
        FROM database_targets
        WHERE user_id = %s AND id = %s;
        """,
        (user_id, target_id),
    )
    if not existing:
        return None

    existing_schema_json = existing.get("schema_json")
    if isinstance(existing_schema_json, (str, bytes)):
        try:
            existing_schema_json = json.loads(existing_schema_json)
        except (TypeError, ValueError):
            existing_schema_json = None
    merged = {
        "name": existing["name"],
        "profile_id": existing.get("profile_id") or "memory-default",
        "profile_hash": existing.get("profile_hash"),
        "tool_manifest": (
            existing.get("tool_manifest_json")
            if isinstance(existing.get("tool_manifest_json"), dict)
            else {}
        ),
    }
    # schema_json is only carried over if the caller didn't explicitly set it.
    if "schema_json" in payload:
        merged["schema_json"] = payload["schema_json"]
    elif existing_schema_json is not None:
        merged["schema_json"] = existing_schema_json
    merged.update({k: v for k, v in payload.items() if k != "schema_json"})
    normalized = _normalize_target_payload(merged, require_name=True)
    profile = _normalize_profile_payload(merged)

    if profile["schema_json"] is _SCHEMA_JSON_SENTINEL:
        schema_json_param: Optional[str] = (
            json.dumps(existing_schema_json) if existing_schema_json is not None else None
        )
    elif profile["schema_json"] is None:
        schema_json_param = None
    else:
        schema_json_param = json.dumps(profile["schema_json"])

    query = """
        UPDATE database_targets
        SET
            name = %s,
            profile_id = %s,
            profile_hash = %s,
            schema_json = %s::jsonb,
            tool_manifest_json = %s::jsonb,
            published_at = NOW(),
            updated_at = NOW()
        WHERE user_id = %s AND id = %s
        RETURNING id, user_id, name,
                  profile_id, profile_hash, schema_json, tool_manifest_json, published_at,
                  created_at, updated_at;
    """

    try:
        row = await _fetch_one(
            query,
            (
                normalized["name"],
                profile["profile_id"],
                profile["profile_hash"],
                schema_json_param,
                json.dumps(profile["tool_manifest"]),
                user_id,
                target_id,
            ),
        )
    except Exception as exc:
        if _unique_violation and isinstance(exc, _unique_violation):
            raise DatabaseTargetConflictError(
                f"Database target '{normalized['name']}' already exists."
            ) from exc
        if "duplicate key value violates unique constraint" in str(exc).lower():
            raise DatabaseTargetConflictError(
                f"Database target '{normalized['name']}' already exists."
            ) from exc
        raise
    if not row:
        return None
    return _record_to_public(_row_to_record(row))


async def delete_database_target(user_id: str, target_id: str) -> bool:
    row = await _fetch_one(
        """
        DELETE FROM database_targets
        WHERE user_id = %s AND id = %s
        RETURNING id;
        """,
        (user_id, target_id),
    )
    return bool(row)


async def get_database_target_for_routing(
    user_id: str,
    target_id: str,
) -> Optional[dict[str, Any]]:
    row = await _fetch_one(
        """
        SELECT id, user_id, name,
               profile_id, profile_hash, schema_json, tool_manifest_json, published_at
        FROM database_targets
        WHERE user_id = %s AND id = %s;
        """,
        (user_id, target_id),
    )
    if not row:
        return None
    schema_json = row.get("schema_json")
    if isinstance(schema_json, (str, bytes)):
        try:
            schema_json = json.loads(schema_json)
        except (TypeError, ValueError):
            schema_json = None
    if not isinstance(schema_json, dict):
        schema_json = None
    return {
        "target_id": row["id"],
        "database_id": row["name"],
        "profile_id": row.get("profile_id") or "memory-default",
        "profile_hash": row.get("profile_hash"),
        "schema_json": schema_json,
        "tool_manifest": (
            row.get("tool_manifest_json")
            if isinstance(row.get("tool_manifest_json"), dict)
            else {}
        ),
        "published_at": (
            row["published_at"].isoformat()
            if row.get("published_at") is not None
            else None
        ),
    }


async def get_schema_for_target(target_id: str) -> Optional[dict[str, Any]]:
    """Read-side accessor for the cached authored schema on a database target.

    Returns the parsed `schema_json` dict if present, or None if the column is
    NULL (meaning the target has never been hydrated via a sync resolve — the
    cold-start path should trigger a blocking sync pull).
    """
    row = await _fetch_one(
        """
        SELECT schema_json
        FROM database_targets
        WHERE id = %s;
        """,
        (target_id,),
    )
    if not row:
        return None
    value = row.get("schema_json")
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


async def update_profile_hash_and_schema(
    target_id: str,
    *,
    profile_hash: str,
    schema_json: Optional[dict[str, Any]],
    tool_manifest: Optional[dict[str, Any]] = None,
) -> None:
    """Write-side helper used by the staleness-detection flow (Phase 5) to
    atomically update the profile_hash + schema_json on a target in place
    after a resolve pull.
    """
    schema_param = json.dumps(schema_json) if schema_json is not None else None
    manifest_param = (
        json.dumps(tool_manifest) if isinstance(tool_manifest, dict) else None
    )
    await _fetch_one(
        """
        UPDATE database_targets
        SET
            profile_hash = %s,
            schema_json = %s::jsonb,
            tool_manifest_json = COALESCE(%s::jsonb, tool_manifest_json),
            updated_at = NOW()
        WHERE id = %s
        RETURNING id;
        """,
        (profile_hash, schema_param, manifest_param, target_id),
    )


async def upsert_database_target_from_sync(
    user_id: str,
    sync_payload: dict[str, Any],
) -> dict[str, Any]:
    database_id = str(sync_payload.get("database_id", "")).strip()
    profile = sync_payload.get("profile")
    if not database_id:
        raise ValueError("Local sync payload missing database_id.")
    if not isinstance(profile, dict):
        raise ValueError("Local sync payload missing profile object.")

    raw_schema = profile.get("schema")
    schema_json: Optional[dict[str, Any]] = (
        raw_schema if isinstance(raw_schema, dict) else None
    )

    payload = {
        "name": database_id,
        "profile_id": profile.get("profile_id", "memory-default"),
        "profile_hash": profile.get("profile_hash"),
        "schema_json": schema_json,
        "tool_manifest": profile.get("tool_manifest", {}),
    }

    existing = await get_database_target_by_name(user_id, database_id)
    if existing:
        updated = await update_database_target(user_id, existing["target_id"], payload)
        if not updated:
            raise RuntimeError("Failed to update synced database target.")
        return updated
    return await create_database_target(user_id, payload)
