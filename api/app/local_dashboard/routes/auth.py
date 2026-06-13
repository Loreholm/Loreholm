from __future__ import annotations

import secrets
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response

from ..core.auth import (
    _create_session,
    _dashboard_sessions,
    _generate_dashboard_key,
    _get_preferences_payload,
    _is_account_setup,
    _is_session_valid,
    _load_credentials,
    _load_dashboard_keys,
    _load_preferences,
    _now_iso,
    _save_credentials,
    _save_dashboard_keys,
    _save_preferences,
    _verify_local_token_header,
    _verify_password,
    require_local_auth,
    require_local_session,
)
from ..core.config import (
    LOCAL_DASHBOARD_SESSION_COOKIE,
    LOCAL_DASHBOARD_SESSION_COOKIE_SECURE,
    LOCAL_DASHBOARD_SESSION_TTL_SECONDS,
)
from ..core.models import (
    CreateDashboardKeyRequest,
    LoginRequest,
    SetupAccountRequest,
    UpdatePreferencesRequest,
)

router = APIRouter()


@router.post("/auth/handshake")
async def local_dashboard_auth_handshake(
    response: Response,
    x_local_token: Optional[str] = Header(default=None, alias="X-Local-Token"),
) -> dict[str, Any]:
    matched_key_id = _verify_local_token_header(x_local_token)
    is_bootstrap = matched_key_id is None
    setup_required = is_bootstrap and not _is_account_setup()
    if matched_key_id:
        # One-time use: mark the managed key as consumed
        keys = _load_dashboard_keys()
        for k in keys:
            if isinstance(k, dict) and k.get("key_id") == matched_key_id:
                k["used"] = True
        _save_dashboard_keys(keys)
    session_id, expires_at = _create_session()
    response.set_cookie(
        key=LOCAL_DASHBOARD_SESSION_COOKIE,
        value=session_id,
        max_age=max(60, LOCAL_DASHBOARD_SESSION_TTL_SECONDS),
        httponly=True,
        secure=LOCAL_DASHBOARD_SESSION_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    return {"authenticated": True, "setup_required": setup_required, "expires_at": expires_at.isoformat()}


@router.get("/auth/status")
def local_dashboard_auth_status(
    session_id: Optional[str] = Cookie(
        default=None,
        alias=LOCAL_DASHBOARD_SESSION_COOKIE,
    ),
) -> dict[str, Any]:
    return {"authenticated": bool(session_id and _is_session_valid(session_id))}


@router.post("/auth/logout")
def local_dashboard_auth_logout(
    response: Response,
    session_id: Optional[str] = Cookie(
        default=None,
        alias=LOCAL_DASHBOARD_SESSION_COOKIE,
    ),
) -> dict[str, Any]:
    if session_id:
        _dashboard_sessions.pop(session_id, None)
    response.delete_cookie(
        key=LOCAL_DASHBOARD_SESSION_COOKIE,
        path="/",
    )
    return {"authenticated": False}


@router.get("/auth/setup-status")
def local_dashboard_setup_status() -> dict[str, Any]:
    return {"setup_complete": _is_account_setup()}


@router.post("/auth/setup")
def local_dashboard_setup_account(
    payload: SetupAccountRequest,
    response: Response,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    if _is_account_setup():
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "ALREADY_SETUP", "message": "Account already configured."}},
        )
    username = payload.username.strip()
    if not username:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "INVALID_INPUT", "message": "Username cannot be blank."}},
        )
    _save_credentials(username, payload.password)
    return {"setup_complete": True, "username": username}


@router.post("/auth/login")
def local_dashboard_login(
    payload: LoginRequest,
    response: Response,
) -> dict[str, Any]:
    creds = _load_credentials()
    if not creds:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "SETUP_REQUIRED", "message": "Account not yet configured. Use the bootstrap token to set up your account."}},
        )
    stored_username = str(creds.get("username", ""))
    stored_hash = str(creds.get("password_hash", ""))
    salt = str(creds.get("salt", ""))
    username_match = secrets.compare_digest(payload.username.strip(), stored_username)
    password_match = _verify_password(payload.password, stored_hash, salt)
    if not (username_match and password_match):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "INVALID_CREDENTIALS", "message": "Invalid username or password."}},
        )
    session_id, expires_at = _create_session()
    response.set_cookie(
        key=LOCAL_DASHBOARD_SESSION_COOKIE,
        value=session_id,
        max_age=max(60, LOCAL_DASHBOARD_SESSION_TTL_SECONDS),
        httponly=True,
        secure=LOCAL_DASHBOARD_SESSION_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    return {"authenticated": True, "expires_at": expires_at.isoformat()}


@router.get("/auth/keys")
def list_dashboard_api_keys(_: None = Depends(require_local_auth)) -> dict[str, Any]:
    keys = _load_dashboard_keys()
    masked = [
        {
            "key_id": entry.get("key_id", ""),
            "label": entry.get("label", ""),
            "created_at": entry.get("created_at", ""),
            "token_hint": str(entry.get("token", ""))[:12] + "...",
            "used": bool(entry.get("used", False)),
        }
        for entry in keys
        if isinstance(entry, dict)
    ]
    return {"keys": masked}


@router.post("/auth/keys")
def create_dashboard_api_key(
    payload: CreateDashboardKeyRequest,
    _: None = Depends(require_local_auth),
) -> dict[str, Any]:
    label = payload.label.strip()
    keys = _load_dashboard_keys()
    new_token = _generate_dashboard_key()
    entry: dict[str, Any] = {
        "key_id": "kid_" + secrets.token_urlsafe(12),
        "label": label,
        "token": new_token,
        "created_at": _now_iso(),
        "used": False,
    }
    keys.append(entry)
    _save_dashboard_keys(keys)
    return {
        "key_id": entry["key_id"],
        "label": entry["label"],
        "token": new_token,
        "created_at": entry["created_at"],
    }


@router.post("/auth/keys/{key_id}/rotate")
def rotate_dashboard_api_key(
    key_id: str,
    _: None = Depends(require_local_auth),
) -> dict[str, Any]:
    keys = _load_dashboard_keys()
    for k in keys:
        if isinstance(k, dict) and k.get("key_id") == key_id:
            new_token = _generate_dashboard_key()
            k["token"] = new_token
            k["used"] = False
            k["rotated_at"] = _now_iso()
            _save_dashboard_keys(keys)
            return {
                "key_id": k["key_id"],
                "label": k.get("label", ""),
                "token": new_token,
                "rotated_at": k["rotated_at"],
            }
    raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "API key not found."}})


@router.delete("/auth/keys/{key_id}")
def delete_dashboard_api_key(
    key_id: str,
    _: None = Depends(require_local_auth),
) -> dict[str, Any]:
    keys = _load_dashboard_keys()
    new_keys = [k for k in keys if isinstance(k, dict) and k.get("key_id") != key_id]
    if len(new_keys) == len(keys):
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "API key not found."}})
    _save_dashboard_keys(new_keys)
    return {"deleted": key_id}


@router.get("/preferences")
def get_dashboard_preferences(
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    return _get_preferences_payload()


@router.put("/preferences")
def update_dashboard_preferences(
    payload: UpdatePreferencesRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    prefs = _load_preferences()
    if payload.favorite_wizard_model is not None:
        value = payload.favorite_wizard_model.strip()
        if value:
            prefs["favorite_wizard_model"] = value
        else:
            prefs.pop("favorite_wizard_model", None)
    _save_preferences(prefs)
    return _get_preferences_payload()
