from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from ..core.auth import _dashboard_sessions, _now_iso
from ..core.config import (
    LOCAL_DASHBOARD_DEV_MODE,
    LOCAL_DASHBOARD_DEV_SESSION_ID,
    LOCAL_DASHBOARD_SESSION_COOKIE,
    LOCAL_DASHBOARD_SESSION_COOKIE_SECURE,
    LOCAL_DASHBOARD_STATIC_DIR,
)

home_router = APIRouter()


def _local_dashboard_frontend_file(filename: str) -> Path:
    path = LOCAL_DASHBOARD_STATIC_DIR / filename
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "FRONTEND_ASSETS_MISSING",
                    "message": f"Missing local dashboard frontend asset: {path}",
                }
            },
        )
    return path


@home_router.get("/", response_class=FileResponse)
def local_dashboard_home() -> FileResponse:
    return FileResponse(_local_dashboard_frontend_file("index.html"))


@home_router.get("/dev/login")
def local_dashboard_dev_login() -> RedirectResponse:
    """Dev-loop convenience: set the session cookie to the pre-seeded dev
    session id and bounce the browser to /. Returns 404 unless
    LOCAL_DASHBOARD_DEV_MODE is enabled, so this is inert in production.
    """
    if not LOCAL_DASHBOARD_DEV_MODE:
        raise HTTPException(status_code=404, detail="Not Found")
    # Re-register in case _dashboard_sessions was pruned after a long idle.
    _dashboard_sessions[LOCAL_DASHBOARD_DEV_SESSION_ID] = (
        datetime.now(timezone.utc) + timedelta(days=365)
    )
    redirect = RedirectResponse(url="/", status_code=302)
    redirect.set_cookie(
        key=LOCAL_DASHBOARD_SESSION_COOKIE,
        value=LOCAL_DASHBOARD_DEV_SESSION_ID,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        secure=LOCAL_DASHBOARD_SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return redirect


@home_router.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "timestamp": _now_iso()}
