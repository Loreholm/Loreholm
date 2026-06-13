"""ArcadeDB query executor for the local dashboard proxy.

Transport is ArcadeDB's HTTP API (`/api/v1/command/{database}`). We always
pass `language=cypher` — the authored-schema layer, the embedding-hook
placeholders, and the staging-write rewrites all produce Cypher.
SQL/Gremlin would need a language-guard change upstream.

Returns `(columns, rows)` with scalars/maps already normalized, matching
the shape `cypher._run_query` used to produce.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException

from ..core.config import (
    LOCAL_DASHBOARD_ARCADEDB_HOST,
    LOCAL_DASHBOARD_ARCADEDB_PORT,
    _load_arcadedb_root_password,
)


_ARCADEDB_QUERY_TIMEOUT_SECONDS = 30.0


def _resolve_host(record: Dict[str, Any]) -> str:
    # `record.host` is carried through for forward-compat (e.g. if a user
    # ever pointed at a remote ArcadeDB), but the single-server architecture
    # always terminates at the shared LOCAL_DASHBOARD_ARCADEDB_HOST.
    host = str(record.get("host") or "").strip()
    return host or LOCAL_DASHBOARD_ARCADEDB_HOST


def _resolve_port(record: Dict[str, Any]) -> int:
    # Legacy registries may still carry `port` — prefer the shared server
    # port unless someone intentionally overrode it per record.
    raw = record.get("port")
    try:
        if raw not in (None, ""):
            return int(raw)
    except (TypeError, ValueError):
        pass
    return LOCAL_DASHBOARD_ARCADEDB_PORT


def _resolve_auth(record: Dict[str, Any]) -> Tuple[str, str]:
    try:
        password = _load_arcadedb_root_password()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "ARCADEDB_NOT_CONFIGURED",
                    "message": str(exc),
                }
            },
        ) from exc
    return "root", password


def _coerce_scalar(value: Any) -> Any:
    """ArcadeDB returns rich documents (`@rid`, `@type`, `@cat`) that most
    callers don't want. Strip the metadata keys but preserve them under a
    neutral `_meta` bucket for debugging."""
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        meta: Dict[str, Any] = {}
        for key, sub in value.items():
            if key.startswith("@"):
                meta[key] = sub
            else:
                cleaned[key] = _coerce_scalar(sub)
        if meta:
            cleaned["_meta"] = meta
        return cleaned
    if isinstance(value, list):
        return [_coerce_scalar(item) for item in value]
    return value


def _flatten_row(row: Any, columns: List[str]) -> List[Any]:
    """Return a list aligned with `columns`.

    Cypher `RETURN a, b, c` in ArcadeDB produces a JSON object per row
    like `{"a": ..., "b": ..., "c": ...}`. `RETURN *` may produce a bare
    array or document depending on the statement — we normalize both.
    """
    if isinstance(row, dict):
        if columns:
            return [_coerce_scalar(row.get(col)) for col in columns]
        # No columns declared: preserve ordering from dict insertion.
        return [_coerce_scalar(v) for k, v in row.items() if not k.startswith("@")]
    if isinstance(row, list):
        return [_coerce_scalar(v) for v in row]
    return [_coerce_scalar(row)]


def _infer_columns(result_rows: List[Any]) -> List[str]:
    for row in result_rows:
        if isinstance(row, dict):
            return [k for k in row.keys() if not k.startswith("@")]
    return []


def run_arcadedb_query(
    record: Dict[str, Any],
    cypher: str,
    parameters: Optional[Dict[str, Any]],
    language: str = "cypher",
) -> Tuple[List[str], List[List[Any]]]:
    """Execute `cypher` against the ArcadeDB container recorded in `record`.

    Returns `(columns, rows)` in the same shape cypher.py produces, so
    the sync route can stitch responses without branching.

    `language` is "cypher" by default. Use "sql" for the few queries that
    must call ArcadeDB SQL functions Cypher doesn't expose (notably
    `vectorNeighbors`, which only exists as a SQL function in 26.x).
    """
    database_id = str(record.get("database_id") or "").strip()
    if not database_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_DATABASE_ID",
                    "message": "Registry record is missing database_id.",
                }
            },
        )
    host = _resolve_host(record)
    port = _resolve_port(record)
    auth = _resolve_auth(record)

    payload: Dict[str, Any] = {
        "language": language,
        "command": cypher,
    }
    if parameters:
        payload["params"] = parameters

    url = f"http://{host}:{port}/api/v1/command/{database_id}"
    try:
        with httpx.Client(timeout=_ARCADEDB_QUERY_TIMEOUT_SECONDS, auth=auth) as client:
            response = client.post(url, json=payload)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "DB_CONNECTION_FAILED",
                    "message": f"Could not connect to ArcadeDB at {host}:{port}: {exc}",
                }
            },
        ) from exc

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "DB_AUTH_FAILED",
                    "message": "ArcadeDB rejected dashboard credentials.",
                }
            },
        )
    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "DATABASE_NOT_FOUND",
                    "message": f"ArcadeDB reports database '{database_id}' not found.",
                }
            },
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "DB_INVALID_RESPONSE",
                    "message": f"ArcadeDB returned non-JSON (HTTP {response.status_code}).",
                }
            },
        ) from exc

    if response.status_code >= 400:
        message = body.get("detail") if isinstance(body, dict) else None
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "QUERY_FAILED",
                    "message": str(message or json.dumps(body)[:500]),
                }
            },
        )

    result_rows = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result_rows, list):
        result_rows = []

    columns = _infer_columns(result_rows)
    rows = [_flatten_row(row, columns) for row in result_rows]
    return columns, rows
