from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx

from app.services.sync_auth import (
    SyncAuthNotConfiguredError,
    derive_user_sync_token,
)
from app.services.user_store import get_user_tailscale_ip


LOCAL_SYNC_PORT = int(os.getenv("LOCAL_SYNC_PORT", "8081"))
LOCAL_SYNC_TIMEOUT_SECONDS = float(os.getenv("LOCAL_SYNC_TIMEOUT_SECONDS", "3.0"))


class LocalSyncError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _user_sync_token(user_id: str) -> str:
    """Derive the bearer token used when the cloud calls this user's local
    dashboard. See `app/services/sync_auth.py` for the derivation rules.
    A missing signing secret is surfaced as `LOCAL_SYNC_NOT_CONFIGURED` so
    the frontend can distinguish "deployment misconfigured" from "local
    node unreachable" and show an actionable message.
    """
    try:
        return derive_user_sync_token(user_id)
    except SyncAuthNotConfiguredError as exc:
        raise LocalSyncError(
            code="LOCAL_SYNC_NOT_CONFIGURED",
            message=str(exc),
            status_code=503,
        ) from exc


def _validate_sync_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise LocalSyncError(
            code="LOCAL_SYNC_INVALID_RESPONSE",
            message="Local sync endpoint returned a non-object payload.",
            status_code=502,
        )
    profile = payload.get("profile")
    database_id = str(payload.get("database_id", "")).strip()
    if not database_id or not isinstance(profile, dict):
        raise LocalSyncError(
            code="LOCAL_SYNC_INVALID_RESPONSE",
            message="Local sync payload missing required database_id/profile fields.",
            status_code=502,
        )
    return payload


async def fetch_local_database_sync_payload(
    user_id: str,
    database_id: str,
) -> dict[str, Any]:
    db_id = str(database_id or "").strip()
    if not db_id:
        raise LocalSyncError(
            code="LOCAL_SYNC_INVALID_REQUEST",
            message="database_id is required for local sync.",
            status_code=400,
        )

    tailscale_ip = await get_user_tailscale_ip(user_id)
    if not tailscale_ip:
        raise LocalSyncError(
            code="LOCAL_SYNC_UNREACHABLE",
            message="No online BYODB node found for this user.",
            status_code=503,
        )

    token = _user_sync_token(user_id)
    payload: dict[str, Any] = {"database_id": db_id}

    url = f"http://{tailscale_ip}:{LOCAL_SYNC_PORT}/api/sync/database-targets/resolve"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(LOCAL_SYNC_TIMEOUT_SECONDS, connect=1.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.RequestError as exc:
        raise LocalSyncError(
            code="LOCAL_SYNC_UNREACHABLE",
            message=f"Could not reach local sync endpoint: {exc}",
            status_code=502,
        ) from exc

    if response.status_code in {401, 403}:
        raise LocalSyncError(
            code="LOCAL_SYNC_UNAUTHORIZED",
            message="Local sync endpoint rejected cloud credentials.",
            status_code=502,
        )
    if response.status_code == 404:
        raise LocalSyncError(
            code="LOCAL_SYNC_DATABASE_NOT_FOUND",
            message=f"Database '{db_id}' was not found on the local dashboard.",
            status_code=404,
        )
    if response.status_code >= 500:
        raise LocalSyncError(
            code="LOCAL_SYNC_REMOTE_ERROR",
            message=f"Local sync endpoint failed with HTTP {response.status_code}.",
            status_code=502,
        )
    if response.status_code >= 400:
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {}
        error_message = (
            body.get("detail", {}).get("error", {}).get("message")
            if isinstance(body, dict)
            else None
        )
        raise LocalSyncError(
            code="LOCAL_SYNC_FAILED",
            message=error_message or f"Local sync request failed with HTTP {response.status_code}.",
            status_code=400,
        )

    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise LocalSyncError(
            code="LOCAL_SYNC_INVALID_RESPONSE",
            message="Local sync endpoint returned invalid JSON.",
            status_code=502,
        ) from exc

    return _validate_sync_response(body)


async def fetch_local_database_inventory(user_id: str) -> list[dict[str, Any]]:
    """Discovery path: ask the user's local dashboard for the list of
    databases it knows about so the cloud dashboard can offer a pick-list
    when binding a new API key.

    Returns a list of `{database_id, name, profile_id, profile_hash, status,
    last_seen_at}` dicts (same shape the local `/sync/databases` endpoint
    emits). Includes offline databases so the user sees their full inventory.
    """
    tailscale_ip = await get_user_tailscale_ip(user_id)
    if not tailscale_ip:
        raise LocalSyncError(
            code="LOCAL_SYNC_UNREACHABLE",
            message="No online BYODB node found for this user.",
            status_code=503,
        )

    token = _user_sync_token(user_id)
    url = f"http://{tailscale_ip}:{LOCAL_SYNC_PORT}/api/sync/databases"
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(LOCAL_SYNC_TIMEOUT_SECONDS, connect=1.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        raise LocalSyncError(
            code="LOCAL_SYNC_UNREACHABLE",
            message=f"Could not reach local sync endpoint: {exc}",
            status_code=502,
        ) from exc

    if response.status_code in {401, 403}:
        raise LocalSyncError(
            code="LOCAL_SYNC_UNAUTHORIZED",
            message="Local sync endpoint rejected cloud credentials.",
            status_code=502,
        )
    if response.status_code >= 500:
        raise LocalSyncError(
            code="LOCAL_SYNC_REMOTE_ERROR",
            message=f"Local sync endpoint failed with HTTP {response.status_code}.",
            status_code=502,
        )
    if response.status_code >= 400:
        raise LocalSyncError(
            code="LOCAL_SYNC_FAILED",
            message=f"Local sync inventory request failed with HTTP {response.status_code}.",
            status_code=400,
        )

    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise LocalSyncError(
            code="LOCAL_SYNC_INVALID_RESPONSE",
            message="Local sync endpoint returned invalid JSON.",
            status_code=502,
        ) from exc

    if not isinstance(body, dict):
        raise LocalSyncError(
            code="LOCAL_SYNC_INVALID_RESPONSE",
            message="Local sync inventory returned a non-object payload.",
            status_code=502,
        )
    raw_items = body.get("databases")
    if not isinstance(raw_items, list):
        raise LocalSyncError(
            code="LOCAL_SYNC_INVALID_RESPONSE",
            message="Local sync inventory missing 'databases' array.",
            status_code=502,
        )

    items: list[dict[str, Any]] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        database_id = str(entry.get("database_id") or "").strip()
        if not database_id:
            continue
        items.append(
            {
                "database_id": database_id,
                "name": str(entry.get("name") or database_id),
                "profile_id": str(entry.get("profile_id") or "memory-default"),
                "profile_hash": entry.get("profile_hash"),
                "status": str(entry.get("status") or "unknown"),
                "last_seen_at": entry.get("last_seen_at"),
            }
        )
    return items
