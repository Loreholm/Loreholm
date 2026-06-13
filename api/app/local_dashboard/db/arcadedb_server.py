"""HTTP client for the shared ArcadeDB server.

Under the single-server architecture
the installer brings up one long-lived `loreholm-arcadedb` container per host.
Per-database lifecycle is done through the server's REST endpoints rather
than spawning a container per database. This module is the thin wrapper
around those endpoints.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from ..core.config import (
    LOCAL_DASHBOARD_ARCADEDB_HOST,
    LOCAL_DASHBOARD_ARCADEDB_PORT,
    _load_arcadedb_root_password,
)

_LOG = logging.getLogger(__name__)


def _base_url(host: Optional[str] = None, port: Optional[int] = None) -> str:
    h = (host or LOCAL_DASHBOARD_ARCADEDB_HOST or "127.0.0.1").strip() or "127.0.0.1"
    p = int(port or LOCAL_DASHBOARD_ARCADEDB_PORT or 2480)
    return f"http://{h}:{p}"


def _auth() -> tuple[str, str]:
    return ("root", _load_arcadedb_root_password())


def _get(
    path: str,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    timeout: float = 5.0,
    auth: Optional[tuple[str, str]] = None,
) -> httpx.Response:
    url = f"{_base_url(host, port)}{path}"
    with httpx.Client(timeout=timeout, auth=auth) as client:
        return client.get(url)


def _post(
    path: str,
    payload: Dict[str, Any],
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    timeout: float = 10.0,
    auth: Optional[tuple[str, str]] = None,
) -> httpx.Response:
    url = f"{_base_url(host, port)}{path}"
    with httpx.Client(timeout=timeout, auth=auth) as client:
        return client.post(url, json=payload)


def wait_for_server_ready(
    *,
    timeout_s: float = 60.0,
    poll_interval_s: float = 1.0,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Poll `GET /api/v1/ready` until it returns 2xx or we time out."""
    deadline = time.monotonic() + max(1.0, timeout_s)
    last_error: Optional[str] = None
    while time.monotonic() < deadline:
        try:
            response = _get("/api/v1/ready", host=host, port=port, timeout=3.0)
        except httpx.RequestError as exc:
            last_error = f"request error: {exc}"
        else:
            if 200 <= response.status_code < 300:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        time.sleep(poll_interval_s)
    raise RuntimeError(
        f"ArcadeDB server at {_base_url(host, port)} not ready within "
        f"{timeout_s:.0f}s (last error: {last_error or 'unknown'})."
    )


def database_exists(
    database_id: str,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> bool:
    """Return True if the database exists on the shared server."""
    response = _get(
        f"/api/v1/exists/{database_id}",
        host=host,
        port=port,
        auth=_auth(),
    )
    if response.status_code == 404:
        return False
    if response.status_code >= 400:
        raise RuntimeError(
            f"ArcadeDB exists probe failed (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )
    try:
        body = response.json()
    except ValueError:
        return False
    # Response shape: {"result": true} per ArcadeDB docs.
    result = body.get("result") if isinstance(body, dict) else None
    if isinstance(result, bool):
        return result
    return bool(body)


def list_databases(
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> List[str]:
    """Return the list of database names known to the server."""
    response = _get("/api/v1/databases", host=host, port=port, auth=_auth())
    if response.status_code >= 400:
        raise RuntimeError(
            f"ArcadeDB list databases failed (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )
    try:
        body = response.json()
    except ValueError:
        return []
    result = body.get("result") if isinstance(body, dict) else body
    if not isinstance(result, list):
        return []
    return [str(item) for item in result if str(item).strip()]


def create_database(
    database_id: str,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Create a database on the shared server. Idempotent."""
    if not database_id or not database_id.strip():
        raise ValueError("database_id is required")
    payload = {"language": "sql", "command": f"CREATE DATABASE {database_id}"}
    response = _post(
        "/api/v1/server",
        payload,
        host=host,
        port=port,
        auth=_auth(),
    )
    if 200 <= response.status_code < 300:
        return
    # Treat "already exists" as success — verify via exists probe to be sure.
    if "already exist" in response.text.lower():
        if database_exists(database_id, host=host, port=port):
            return
    raise RuntimeError(
        f"ArcadeDB create database {database_id!r} failed "
        f"(HTTP {response.status_code}): {response.text[:200]}"
    )


def drop_database(
    database_id: str,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Drop a database on the shared server. Idempotent."""
    if not database_id or not database_id.strip():
        raise ValueError("database_id is required")
    payload = {"language": "sql", "command": f"DROP DATABASE {database_id}"}
    response = _post(
        "/api/v1/server",
        payload,
        host=host,
        port=port,
        auth=_auth(),
    )
    if 200 <= response.status_code < 300:
        return
    lowered = response.text.lower()
    if "not found" in lowered or "does not exist" in lowered:
        return
    if not database_exists(database_id, host=host, port=port):
        return
    raise RuntimeError(
        f"ArcadeDB drop database {database_id!r} failed "
        f"(HTTP {response.status_code}): {response.text[:200]}"
    )
