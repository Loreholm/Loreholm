"""Cloud chat proxy — relays chat requests to the user's local dashboard.

All endpoints require Auth0 JWT authentication. The cloud discovers the
user's Tailscale IP, derives their sync token, and proxies requests to
the local dashboard's ``/api/chat/*`` endpoints. For streaming, SSE
events are passed through directly.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.onboarding.router import get_current_user
from app.services.user_store import get_user_tailscale_ip
from app.services.sync_auth import SyncAuthNotConfiguredError, derive_user_sync_token

router = APIRouter(prefix="/chat", tags=["Chat Proxy"])

LOCAL_SYNC_PORT = int(os.getenv("LOCAL_SYNC_PORT", "8081"))
CHAT_PROXY_TIMEOUT_SECONDS = float(os.getenv("CHAT_PROXY_TIMEOUT_SECONDS", "120.0"))


def _sync_token(user_id: str) -> str:
    try:
        return derive_user_sync_token(user_id)
    except SyncAuthNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "SYNC_NOT_CONFIGURED", "message": str(exc)}},
        ) from exc


async def _resolve_user(request: Request) -> tuple[str, str]:
    """Return (user_id, tailscale_ip) or raise."""
    user = await get_current_user(request)
    user_id = user["sub"]
    tailscale_ip = await get_user_tailscale_ip(user_id)
    if not tailscale_ip:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "NODE_UNREACHABLE",
                    "message": "No online BYODB node found. Verify your node is running.",
                }
            },
        )
    return user_id, tailscale_ip


def _local_url(tailscale_ip: str, path: str) -> str:
    return f"http://{tailscale_ip}:{LOCAL_SYNC_PORT}/api/chat{path}"


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def _proxy_json(
    method: str,
    tailscale_ip: str,
    token: str,
    path: str,
    payload: Any = None,
    params: dict[str, str] | None = None,
) -> Response:
    url = _local_url(tailscale_ip, path)
    headers = _auth_headers(token)
    timeout = httpx.Timeout(CHAT_PROXY_TIMEOUT_SECONDS, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            if method == "GET":
                resp = await client.get(url, headers=headers, params=params)
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers)
            else:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": {"code": "LOCAL_UNREACHABLE", "message": f"Could not reach local dashboard: {exc}"}},
            ) from exc

    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return JSONResponse(status_code=resp.status_code, content=resp.json())
        except ValueError:
            pass
    return Response(content=resp.content, status_code=resp.status_code, media_type=content_type or "text/plain")


# ------------------------------------------------------------------
# Conversation CRUD
# ------------------------------------------------------------------

@router.get("/conversations")
async def list_conversations(request: Request) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    params = {}
    for key in ("source", "database_id", "limit", "offset"):
        val = request.query_params.get(key)
        if val is not None:
            params[key] = val
    return await _proxy_json("GET", ip, token, "/conversations", params=params)


@router.post("/conversations")
async def create_conversation(request: Request, payload: dict[str, Any]) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    return await _proxy_json("POST", ip, token, "/conversations", payload=payload)


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    return await _proxy_json("GET", ip, token, f"/conversations/{conversation_id}")


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    return await _proxy_json("DELETE", ip, token, f"/conversations/{conversation_id}")


# ------------------------------------------------------------------
# Per-database system prompt
# ------------------------------------------------------------------

@router.get("/databases/{database_id}/system-prompt")
async def get_system_prompt(database_id: str, request: Request) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    return await _proxy_json("GET", ip, token, f"/databases/{database_id}/system-prompt")


@router.post("/databases/{database_id}/system-prompt/draft")
async def draft_system_prompt(database_id: str, request: Request, payload: dict[str, Any]) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    return await _proxy_json(
        "POST",
        ip,
        token,
        f"/databases/{database_id}/system-prompt/draft",
        payload=payload,
    )


@router.put("/databases/{database_id}/system-prompt")
async def set_system_prompt(database_id: str, request: Request, payload: dict[str, Any]) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    # httpx doesn't have a typed `put` wrapper in _proxy_json, so inline it.
    url = _local_url(ip, f"/databases/{database_id}/system-prompt")
    headers = _auth_headers(token)
    timeout = httpx.Timeout(CHAT_PROXY_TIMEOUT_SECONDS, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.put(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": {"code": "LOCAL_UNREACHABLE", "message": f"Could not reach local dashboard: {exc}"}},
            ) from exc
    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return JSONResponse(status_code=resp.status_code, content=resp.json())
        except ValueError:
            pass
    return Response(content=resp.content, status_code=resp.status_code, media_type=content_type or "text/plain")


# ------------------------------------------------------------------
# Usage
# ------------------------------------------------------------------

@router.get("/usage")
async def get_usage(request: Request) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    params = {}
    for key in ("conversation_id", "source"):
        val = request.query_params.get(key)
        if val is not None:
            params[key] = val
    return await _proxy_json("GET", ip, token, "/usage", params=params)


# ------------------------------------------------------------------
# Preferences
# ------------------------------------------------------------------

@router.get("/preferences")
async def get_preferences(request: Request) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    return await _proxy_json("GET", ip, token, "/preferences")


@router.put("/preferences")
async def update_preferences(request: Request, payload: dict[str, Any]) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    url = _local_url(ip, "/preferences")
    headers = _auth_headers(token)
    timeout = httpx.Timeout(CHAT_PROXY_TIMEOUT_SECONDS, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.put(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": {"code": "LOCAL_UNREACHABLE", "message": f"Could not reach local dashboard: {exc}"}},
            ) from exc
    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return JSONResponse(status_code=resp.status_code, content=resp.json())
        except ValueError:
            pass
    return Response(content=resp.content, status_code=resp.status_code, media_type=content_type or "text/plain")


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------

@router.get("/models")
async def list_models(request: Request) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    return await _proxy_json("GET", ip, token, "/models")


# ------------------------------------------------------------------
# Streaming chat
# ------------------------------------------------------------------

@router.post("/stream")
async def chat_stream(request: Request, payload: dict[str, Any]) -> StreamingResponse:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    url = _local_url(ip, "/stream")
    headers = _auth_headers(token)
    timeout = httpx.Timeout(CHAT_PROXY_TIMEOUT_SECONDS, connect=5.0)

    client = httpx.AsyncClient(timeout=timeout)

    try:
        req = client.build_request("POST", url, headers=headers, json=payload)
        resp = await client.send(req, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail={"error": {"code": "LOCAL_UNREACHABLE", "message": f"Could not reach local dashboard: {exc}"}},
        ) from exc

    if resp.status_code != 200:
        body = await resp.aread()
        await resp.aclose()
        await client.aclose()
        return Response(content=body, status_code=resp.status_code, media_type=resp.headers.get("content-type", "text/plain"))

    async def stream_events():
        try:
            async for line in resp.aiter_lines():
                yield line + "\n"
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/abort")
async def chat_abort(request: Request) -> Response:
    user_id, ip = await _resolve_user(request)
    token = _sync_token(user_id)
    return await _proxy_json("POST", ip, token, "/abort")
