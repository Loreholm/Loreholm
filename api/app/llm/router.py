"""LLM proxy endpoints.

These endpoints forward requests to upstream providers to avoid browser CORS issues.
Cloud mode: requires Auth0 JWT and Loreholm key ownership checks.
BYODB mode: if Auth0/Redis are not configured, local requests are accepted.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.onboarding.router import get_current_user
from app.services import get_user_tailscale_ip
from app.services.redis_client import get_api_key_store

router = APIRouter(prefix="/llm", tags=["LLM Proxy"])

OPENAI_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/responses")
ANTHROPIC_URL = os.getenv("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")
ANTHROPIC_VERSION = os.getenv("ANTHROPIC_VERSION", "2023-06-01")
GOOGLE_API_BASE = os.getenv(
    "GOOGLE_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


BYODB_BIFROST_PORT = _env_int("BYODB_BIFROST_PORT", 8080)


def _to_provider_error(detail: Any, status_code: int = 503) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": "PROVIDER_NOT_CONFIGURED",
                "message": detail if isinstance(detail, str) else str(detail),
            }
        },
    )


async def _proxy_post(url: str, headers: dict[str, str], payload: Any) -> Response:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            upstream = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}")

    content_type = upstream.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = upstream.json()
        except ValueError:
            return Response(
                content=upstream.text,
                status_code=upstream.status_code,
                media_type=content_type or "text/plain",
            )
        return JSONResponse(status_code=upstream.status_code, content=data)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=content_type or "application/octet-stream",
    )


def _extract_loreholm_key_id(payload: dict[str, Any], header_key_id: str | None) -> str:
    payload_key_id = payload.get("loreholm_key_id")
    selected_key_id = header_key_id or payload_key_id
    payload.pop("loreholm_key_id", None)
    if not selected_key_id:
        if not os.getenv("REDIS_HOST"):
            # BYODB local mode: key ownership checks are disabled without Redis.
            return "byodb-local-key"
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "KEY_ID_REQUIRED",
                    "message": "Missing X-Loreholm-Key-Id header.",
                }
            },
        )
    return str(selected_key_id)


async def _require_web_auth(request: Request, loreholm_key_id: str) -> dict:
    user = await get_current_user(request)
    user_id = user["sub"]

    # If Redis is not configured in this deployment, skip ownership validation.
    # This keeps BYODB-hosted API deployments functional while still requiring JWT auth.
    if not os.getenv("REDIS_HOST"):
        return user

    try:
        store = await get_api_key_store()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "REDIS_ERROR",
                    "message": f"Failed to connect to Redis: {exc}",
                }
            },
        )

    metadata = await store.get_key_metadata(user_id, loreholm_key_id)
    if not metadata:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "KEY_NOT_FOUND",
                    "message": "Loreholm API key not found or does not belong to you.",
                }
            },
        )

    expires_at = datetime.fromisoformat(metadata["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "KEY_EXPIRED",
                    "message": "Selected Loreholm API key has expired.",
                }
            },
        )

    if await store.is_key_revoked(loreholm_key_id):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "KEY_REVOKED",
                    "message": "Selected Loreholm API key has been revoked.",
                }
            },
        )

    return user


def _read_key_from_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            value = file_handle.read().strip()
    except OSError:
        return None
    return value or None


def _require_provider_key(provider: str, env_name: str) -> str:
    value = (os.getenv(env_name) or "").strip()
    if value:
        return value

    file_env_name = f"{env_name}_FILE"
    file_path = (os.getenv(file_env_name) or "").strip()
    if file_path:
        file_value = _read_key_from_file(file_path)
        if file_value:
            return file_value
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "PROVIDER_NOT_CONFIGURED",
                    "message": (
                        f"Missing {provider} API key on the server. "
                        f"{file_env_name} is set but unreadable/empty: {file_path}"
                    ),
                }
            },
        )

    raise HTTPException(
        status_code=503,
        detail={
            "error": {
                "code": "PROVIDER_NOT_CONFIGURED",
                "message": (
                    f"Missing {provider} API key on the server. "
                    f"Set {env_name} or {env_name}_FILE."
                ),
            }
        },
    )


async def _proxy_to_user_bifrost_messages(user_id: str, payload: dict[str, Any]) -> Response:
    tailscale_ip = await get_user_tailscale_ip(user_id)
    if not tailscale_ip:
        raise _to_provider_error(
            "No online BYODB node found for this user. Verify node connectivity first."
        )

    bifrost_payload = dict(payload)
    url = f"http://{tailscale_ip}:{BYODB_BIFROST_PORT}/anthropic/v1/messages"
    headers = {
        "Content-Type": "application/json",
    }
    return await _proxy_post(url, headers, bifrost_payload)


def _is_provider_not_configured(exc: HTTPException) -> bool:
    detail = exc.detail
    if not isinstance(detail, dict):
        return False
    error = detail.get("error")
    if not isinstance(error, dict):
        return False
    return error.get("code") == "PROVIDER_NOT_CONFIGURED"


@router.post("/openai")
async def proxy_openai(
    request: Request,
    payload: dict[str, Any],
    loreholm_key_id: str | None = Header(default=None, alias="X-Loreholm-Key-Id"),
) -> Response:
    selected_key_id = _extract_loreholm_key_id(payload, loreholm_key_id)
    await _require_web_auth(request, selected_key_id)
    api_key = _require_provider_key("OpenAI", "OPENAI_API_KEY")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    return await _proxy_post(OPENAI_URL, headers, payload)


@router.post("/anthropic")
async def proxy_anthropic(
    request: Request,
    payload: dict[str, Any],
    loreholm_key_id: str | None = Header(default=None, alias="X-Loreholm-Key-Id"),
) -> Response:
    selected_key_id = _extract_loreholm_key_id(payload, loreholm_key_id)
    user = await _require_web_auth(request, selected_key_id)
    try:
        api_key = _require_provider_key("Anthropic", "ANTHROPIC_API_KEY")
    except HTTPException as exc:
        if _is_provider_not_configured(exc):
            return await _proxy_to_user_bifrost_messages(user["sub"], payload)
        raise
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    return await _proxy_post(ANTHROPIC_URL, headers, payload)


@router.post("/google")
async def proxy_google(
    request: Request,
    payload: dict[str, Any],
    loreholm_key_id: str | None = Header(default=None, alias="X-Loreholm-Key-Id"),
) -> Response:
    selected_key_id = _extract_loreholm_key_id(payload, loreholm_key_id)
    await _require_web_auth(request, selected_key_id)
    api_key = _require_provider_key("Google", "GOOGLE_API_KEY")
    model = payload.get("model")
    if not model:
        raise HTTPException(status_code=400, detail="Missing model in request body.")

    payload = dict(payload)
    payload.pop("model", None)

    url = f"{GOOGLE_API_BASE}/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    return await _proxy_post(url, headers, payload)
