from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Cookie, Depends, Header, HTTPException

from .config import (
    LOCAL_API_KEY_FILE,
    LOCAL_DASHBOARD_CREDENTIALS_FILE,
    LOCAL_DASHBOARD_DEV_MODE,
    LOCAL_DASHBOARD_DEV_SESSION_ID,
    LOCAL_DASHBOARD_KEYS_FILE,
    LOCAL_DASHBOARD_PREFERENCES_FILE,
    LOCAL_DASHBOARD_SESSION_COOKIE,
    LOCAL_DASHBOARD_SESSION_TTL_SECONDS,
    LOCAL_DASHBOARD_TOKEN_FILE,
    LOCAL_SYNC_TOKEN_FILE,
)

_dashboard_sessions: dict[str, datetime] = {}
if LOCAL_DASHBOARD_DEV_MODE:
    # Seed a long-lived dev session so the browser cookie keeps working across
    # uvicorn --reload restarts. Re-runs on every worker start.
    _dashboard_sessions[LOCAL_DASHBOARD_DEV_SESSION_ID] = (
        datetime.now(timezone.utc) + timedelta(days=365)
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_token() -> str:
    token = os.getenv("LOCAL_DASHBOARD_TOKEN", "").strip()
    if token:
        return token
    try:
        value = LOCAL_DASHBOARD_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "TOKEN_NOT_FOUND",
                    "message": f"Missing token file at {LOCAL_DASHBOARD_TOKEN_FILE}.",
                }
            },
        ) from exc
    if not value:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "TOKEN_EMPTY",
                    "message": f"Token file is empty: {LOCAL_DASHBOARD_TOKEN_FILE}.",
                }
            },
        )
    return value


def _load_sync_token() -> str:
    token = os.getenv("LOCAL_SYNC_TOKEN", "").strip()
    if token:
        return token
    try:
        value = LOCAL_SYNC_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "SYNC_TOKEN_NOT_FOUND",
                    "message": f"Missing sync token file at {LOCAL_SYNC_TOKEN_FILE}.",
                }
            },
        ) from exc
    if not value:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "SYNC_TOKEN_EMPTY",
                    "message": f"Sync token file is empty: {LOCAL_SYNC_TOKEN_FILE}.",
                }
            },
        )
    return value


def _prune_expired_sessions() -> None:
    now = datetime.now(timezone.utc)
    expired = [
        session_id
        for session_id, expires_at in _dashboard_sessions.items()
        if expires_at <= now
    ]
    for session_id in expired:
        _dashboard_sessions.pop(session_id, None)


def _create_session() -> tuple[str, datetime]:
    _prune_expired_sessions()
    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=max(60, LOCAL_DASHBOARD_SESSION_TTL_SECONDS)
    )
    _dashboard_sessions[session_id] = expires_at
    return session_id, expires_at


def _is_session_valid(session_id: str) -> bool:
    _prune_expired_sessions()
    expires_at = _dashboard_sessions.get(session_id)
    if not expires_at:
        return False
    return expires_at > datetime.now(timezone.utc)


def _load_dashboard_keys() -> list[dict[str, Any]]:
    try:
        data = json.loads(LOCAL_DASHBOARD_KEYS_FILE.read_text(encoding="utf-8"))
        return data.get("keys", []) if isinstance(data, dict) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_dashboard_keys(keys: list[dict[str, Any]]) -> None:
    LOCAL_DASHBOARD_KEYS_FILE.write_text(
        json.dumps({"version": 1, "keys": keys}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _generate_dashboard_key() -> str:
    return "colk_" + secrets.token_urlsafe(32)


def _hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(32)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 260000)
    return dk.hex(), salt


def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 260000)
    return secrets.compare_digest(dk.hex(), stored_hash)


def _load_credentials() -> Optional[dict[str, Any]]:
    try:
        data = json.loads(LOCAL_DASHBOARD_CREDENTIALS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("username") else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save_credentials(username: str, password: str) -> None:
    password_hash, salt = _hash_password(password)
    LOCAL_DASHBOARD_CREDENTIALS_FILE.write_text(
        json.dumps(
            {
                "version": 1,
                "username": username,
                "password_hash": password_hash,
                "salt": salt,
                "created_at": _now_iso(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _is_account_setup() -> bool:
    return _load_credentials() is not None


def _load_preferences() -> dict[str, Any]:
    try:
        data = json.loads(LOCAL_DASHBOARD_PREFERENCES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_preferences(prefs: dict[str, Any]) -> None:
    payload = {"version": 1, **{k: v for k, v in prefs.items() if k != "version"}}
    LOCAL_DASHBOARD_PREFERENCES_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _get_preferences_payload() -> dict[str, Any]:
    prefs = _load_preferences()
    return {
        "favorite_wizard_model": str(prefs.get("favorite_wizard_model") or ""),
    }


def _verify_local_token_header(x_local_token: Optional[str]) -> Optional[str]:
    """Verify token. Returns matched key_id for managed keys, None for bootstrap. Raises 401 if invalid."""
    if not x_local_token:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Missing or invalid local dashboard token."}},
        )
    candidate = x_local_token.strip()
    # Check primary bootstrap token file — only valid before an account has been created
    if not _is_account_setup():
        try:
            primary = _load_token()
            if secrets.compare_digest(candidate, primary):
                return None  # bootstrap — valid during initial setup only
        except HTTPException:
            pass
    # Check managed API keys (skip consumed one-time-use keys)
    for entry in _load_dashboard_keys():
        if not isinstance(entry, dict):
            continue
        if entry.get("used"):
            continue
        stored = str(entry.get("token", "")).strip()
        if stored and secrets.compare_digest(candidate, stored):
            return str(entry.get("key_id", ""))
    raise HTTPException(
        status_code=401,
        detail={"error": {"code": "UNAUTHORIZED", "message": "Missing or invalid local dashboard token."}},
    )


def _require_bootstrap_token(x_local_token: Optional[str]) -> None:
    """Accept only the bootstrap token (for CLI-level key management)."""
    if not x_local_token:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Bootstrap token required."}},
        )
    try:
        primary = _load_token()
        if secrets.compare_digest(x_local_token.strip(), primary):
            return
    except HTTPException:
        pass
    raise HTTPException(
        status_code=401,
        detail={"error": {"code": "UNAUTHORIZED", "message": "Bootstrap token required."}},
    )


def _verify_sync_bearer_token(authorization: Optional[str]) -> None:
    token = _load_sync_token()
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "SYNC_UNAUTHORIZED",
                    "message": "Missing Authorization header for sync endpoint.",
                }
            },
        )
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "SYNC_UNAUTHORIZED",
                    "message": "Authorization header must be Bearer token.",
                }
            },
        )
    if not secrets.compare_digest(value.strip(), token):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "SYNC_UNAUTHORIZED",
                    "message": "Invalid sync token.",
                }
            },
        )


def _load_api_key() -> str:
    token = os.getenv("LOCAL_API_KEY", "").strip()
    if token:
        return token
    try:
        value = LOCAL_API_KEY_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "API_KEY_NOT_FOUND",
                    "message": f"Missing agent API key file at {LOCAL_API_KEY_FILE}.",
                }
            },
        ) from exc
    if not value:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "API_KEY_EMPTY",
                    "message": f"Agent API key file is empty: {LOCAL_API_KEY_FILE}.",
                }
            },
        )
    return value


def _verify_api_key(authorization: Optional[str]) -> None:
    token = _load_api_key()
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "API_UNAUTHORIZED",
                    "message": "Missing Authorization header for agent endpoint.",
                }
            },
        )
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "API_UNAUTHORIZED",
                    "message": "Authorization header must be Bearer token.",
                }
            },
        )
    if not secrets.compare_digest(value.strip(), token):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "API_UNAUTHORIZED",
                    "message": "Invalid agent API key.",
                }
            },
        )


async def require_local_session(
    session_id: Optional[str] = Cookie(
        default=None,
        alias=LOCAL_DASHBOARD_SESSION_COOKIE,
    ),
) -> None:
    if not session_id or not _is_session_valid(session_id):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Missing or invalid local dashboard session.",
                }
            },
        )


def require_local_auth(
    session_id: Optional[str] = Cookie(default=None, alias=LOCAL_DASHBOARD_SESSION_COOKIE),
    x_local_token: Optional[str] = Header(default=None, alias="X-Local-Token"),
) -> None:
    """Accept a valid session cookie OR the bootstrap token directly (for CLI use)."""
    if session_id and _is_session_valid(session_id):
        return
    if x_local_token:
        try:
            _require_bootstrap_token(x_local_token)
            return
        except HTTPException:
            pass
    raise HTTPException(
        status_code=401,
        detail={"error": {"code": "UNAUTHORIZED", "message": "Authentication required."}},
    )


def require_sync_auth(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> None:
    _verify_sync_bearer_token(authorization)


def require_agent_auth(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> None:
    _verify_api_key(authorization)
