from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import HTTPException

from ..core.config import (
    _CYPHER_LEADING_NOISE_RE,
    _MUTATION_RE,
    _SCHEMA_DDL_RE,
)
from .arcadedb_query import run_arcadedb_query


def _is_schema_ddl_query(query: str) -> bool:
    text = query or ""
    while True:
        m = _CYPHER_LEADING_NOISE_RE.match(text)
        if not m:
            break
        text = text[m.end():]
    return bool(_SCHEMA_DDL_RE.match(text))


def _require_readonly_query(cypher: str) -> None:
    stmt = cypher.strip()
    if not stmt:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_QUERY", "message": "Query is empty."}},
        )
    if _MUTATION_RE.search(stmt):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "MUTATION_BLOCKED",
                    "message": "Only readonly queries are allowed in local dashboard v1.",
                }
            },
        )


def _jsonify(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    return str(value)


def _run_query(
    record: dict[str, Any], query: str, params: Optional[dict[str, Any]] = None
) -> tuple[list[str], list[list[Any]]]:
    return run_arcadedb_query(record, query, params or {})


def _safe_query(
    record: dict[str, Any], query: str, params: Optional[dict[str, Any]] = None
) -> tuple[list[str], list[list[Any]]]:
    try:
        return _run_query(record, query, params)
    except HTTPException:
        return [], []


def _wait_for_database_ready(
    record: dict[str, Any],
    *,
    timeout_s: float = 30.0,
    interval_s: float = 0.5,
) -> tuple[bool, float, Optional[str]]:
    """Poll an ArcadeDB container until it answers a trivial Cypher query.

    Used after `deploy_database` / `start_database` / `redeploy_database` to
    pause the wizard tool loop until the freshly-launched database is actually
    accepting queries — otherwise the agent's next tool call (e.g.
    `run_query`, `upsert_entity_type`) races container startup and fails with
    `DB_CONNECTION_FAILED`.

    Returns `(ready, elapsed_s, last_error)`. Never raises.
    """
    from .docker_ops import _http_exception_message

    deadline = time.monotonic() + max(timeout_s, 0.0)
    started = time.monotonic()
    last_error: Optional[str] = None
    while True:
        try:
            _run_query(record, "RETURN 1 AS ok;", {})
            return True, time.monotonic() - started, None
        except HTTPException as exc:
            last_error = _http_exception_message(exc)
        except Exception as exc:  # pragma: no cover - defensive
            last_error = str(exc)
        if time.monotonic() >= deadline:
            return False, time.monotonic() - started, last_error
        time.sleep(interval_s)
