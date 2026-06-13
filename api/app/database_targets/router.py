from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request

from app.database_targets.schemas import (
    CreateDatabaseTargetRequest,
    DatabaseTargetInfo,
    DeleteDatabaseTargetResponse,
    ListDatabaseTargetsResponse,
    UpdateDatabaseTargetRequest,
)
from app.onboarding.router import get_current_user
from app.services.database_targets import (
    DatabaseTargetConflictError,
    create_database_target,
    delete_database_target,
    get_database_target,
    list_database_targets,
    update_database_target,
)
from app.services.local_sync import (
    LocalSyncError,
    fetch_local_database_inventory,
)
from app.services.redis_client import get_api_key_store


router = APIRouter(prefix="/database-targets", tags=["Database Targets"])


def _service_unavailable(message: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": {"code": "SERVICE_UNAVAILABLE", "message": message}},
    )


@router.post("", response_model=DatabaseTargetInfo)
async def create_target(
    request: Request,
    payload: CreateDatabaseTargetRequest,
) -> DatabaseTargetInfo:
    user = await get_current_user(request)
    user_id = user["sub"]

    try:
        target = await create_database_target(
            user_id,
            payload.model_dump(exclude_none=True),
        )
    except DatabaseTargetConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "TARGET_NAME_CONFLICT", "message": str(exc)}},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_TARGET", "message": str(exc)}},
        ) from exc
    except RuntimeError as exc:
        raise _service_unavailable(str(exc)) from exc

    return DatabaseTargetInfo(**target)


@router.get("", response_model=ListDatabaseTargetsResponse)
async def list_targets(request: Request) -> ListDatabaseTargetsResponse:
    user = await get_current_user(request)
    user_id = user["sub"]

    try:
        targets = await list_database_targets(user_id)
    except RuntimeError as exc:
        raise _service_unavailable(str(exc)) from exc

    return ListDatabaseTargetsResponse(
        targets=[DatabaseTargetInfo(**target) for target in targets],
        count=len(targets),
    )


@router.get("/discover")
async def discover_local_databases(request: Request) -> dict:
    """Pull the user's local-dashboard database inventory over Tailnet so the
    cloud dashboard can show a pick-list when binding an API key to a
    database. Returns `{databases: [...], count}`. Fails soft: a local node
    that is offline or unreachable surfaces as a `LocalSyncError` with the
    upstream status, which the UI renders as a warning rather than blocking
    key creation entirely.
    """
    user = await get_current_user(request)
    user_id = user["sub"]

    try:
        items = await fetch_local_database_inventory(user_id)
    except LocalSyncError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": {"code": exc.code, "message": exc.message}},
        ) from exc

    return {"databases": items, "count": len(items)}


@router.patch("/{target_id}", response_model=DatabaseTargetInfo)
async def update_target(
    request: Request,
    target_id: str,
    payload: UpdateDatabaseTargetRequest,
) -> DatabaseTargetInfo:
    user = await get_current_user(request)
    user_id = user["sub"]
    updates = payload.model_dump(exclude_unset=True)

    if not updates:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_TARGET", "message": "No fields to update."}},
        )

    try:
        updated = await update_database_target(user_id, target_id, updates)
    except DatabaseTargetConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "TARGET_NAME_CONFLICT", "message": str(exc)}},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_TARGET", "message": str(exc)}},
        ) from exc
    except RuntimeError as exc:
        raise _service_unavailable(str(exc)) from exc

    if not updated:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "TARGET_NOT_FOUND", "message": "Database target not found."}},
        )
    return DatabaseTargetInfo(**updated)


@router.delete("/{target_id}", response_model=DeleteDatabaseTargetResponse)
async def remove_target(
    request: Request,
    target_id: str,
) -> DeleteDatabaseTargetResponse:
    user = await get_current_user(request)
    user_id = user["sub"]

    try:
        existing = await get_database_target(user_id, target_id)
    except RuntimeError as exc:
        raise _service_unavailable(str(exc)) from exc
    if not existing:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "TARGET_NOT_FOUND", "message": "Database target not found."}},
        )

    if os.getenv("REDIS_HOST"):
        try:
            store = await get_api_key_store()
            in_use = await store.count_active_keys_for_target(user_id, target_id)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": {"code": "REDIS_ERROR", "message": f"Failed to connect to Redis: {exc}"}},
            ) from exc
        if in_use > 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "TARGET_IN_USE",
                        "message": "Cannot delete database target while active API keys reference it.",
                    }
                },
            )

    try:
        deleted = await delete_database_target(user_id, target_id)
    except RuntimeError as exc:
        raise _service_unavailable(str(exc)) from exc
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "TARGET_NOT_FOUND", "message": "Database target not found."}},
        )

    return DeleteDatabaseTargetResponse(
        success=True,
        target_id=target_id,
        message="Database target deleted successfully",
    )

