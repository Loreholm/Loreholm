"""API Keys management endpoints.

These endpoints require Auth0 JWT authentication (used from the dashboard).
They allow users to create, list, and revoke API keys for MCP client access.
"""

from __future__ import annotations

import os
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request

from app.api_keys.schemas import (
    CreateApiKeyRequest,
    CreateApiKeyResponse,
    ListApiKeysResponse,
    RevokeApiKeyResponse,
    ApiKeyInfo,
    ApiKeyDatabaseInfo,
)
from app.onboarding.router import get_current_user
from app.services.database_targets import (
    DatabaseTargetConflictError,
    get_database_target,
    upsert_database_target_from_sync,
)
from app.services.local_sync import (
    LocalSyncError,
    fetch_local_database_sync_payload,
)
from app.services.redis_client import get_api_key_store, MAX_KEYS_PER_USER


router = APIRouter(prefix="/api-keys", tags=["API Keys"])


def _to_database_info_from_target(target: dict | None) -> ApiKeyDatabaseInfo | None:
    if not target:
        return None

    return ApiKeyDatabaseInfo(
        target_id=target["target_id"],
        name=target["name"],
        database_id=target["database_id"],
    )


def _check_api_keys_configured():
    """Check if API keys feature is properly configured."""
    if not os.getenv("API_KEY_SIGNING_SECRET"):
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "NOT_CONFIGURED",
                    "message": "API keys feature is not configured. Missing API_KEY_SIGNING_SECRET.",
                }
            },
        )
    if not os.getenv("REDIS_HOST"):
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "NOT_CONFIGURED",
                    "message": "API keys feature is not configured. Missing REDIS_HOST.",
                }
            },
        )


@router.post(
    "",
    response_model=CreateApiKeyResponse,
    summary="Create a new API key",
    description="""
Create a new API key for MCP client authentication.

**Important:** The API key is only returned once. Users must copy it immediately.

Requires Auth0 JWT authentication (dashboard access).
""",
)
async def create_key(
    request: Request,
    payload: CreateApiKeyRequest,
) -> CreateApiKeyResponse:
    """Create a new API key for the authenticated user."""
    # Check configuration first
    _check_api_keys_configured()
    
    # Import here to avoid errors if not configured
    from app.services.api_key_auth import create_api_key
    
    # Authenticate user via Auth0 JWT
    user = await get_current_user(request)
    user_id = user["sub"]
    email = user.get("email", "")
    
    # Check key limit
    try:
        store = await get_api_key_store()
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "REDIS_ERROR",
                    "message": f"Failed to connect to Redis: {e}",
                }
            },
        )
    
    # Clean up expired keys first to free up slots
    await store.cleanup_expired_keys(user_id)
    
    if not await store.can_create_key(user_id):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "KEY_LIMIT_REACHED",
                    "message": f"You have reached the maximum of {MAX_KEYS_PER_USER} API keys. Please revoke an existing key first.",
                }
            },
        )
    
    database_target_id = payload.database_target_id
    resolved_target: dict | None = None

    if payload.database_sync:
        try:
            sync_payload = await fetch_local_database_sync_payload(
                user_id,
                payload.database_sync.database_id,
            )
            resolved_target = await upsert_database_target_from_sync(user_id, sync_payload)
        except LocalSyncError as e:
            raise HTTPException(
                status_code=e.status_code,
                detail={"error": {"code": e.code, "message": e.message}},
            ) from e
        except DatabaseTargetConflictError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "TARGET_NAME_CONFLICT",
                        "message": str(e),
                    }
                },
            ) from e
        except (ValueError, RuntimeError) as e:
            raise HTTPException(
                status_code=400 if isinstance(e, ValueError) else 503,
                detail={
                    "error": {
                        "code": (
                            "PROFILE_VALIDATION_FAILED"
                            if isinstance(e, ValueError)
                            else "TARGET_UPSERT_FAILED"
                        ),
                        "message": str(e),
                    }
                },
            ) from e
        database_target_id = resolved_target["target_id"]
    elif database_target_id:
        try:
            resolved_target = await get_database_target(user_id, database_target_id)
        except RuntimeError as e:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "DATABASE_TARGETS_UNAVAILABLE",
                        "message": str(e),
                    }
                },
            ) from e
        if not resolved_target:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "code": "TARGET_NOT_FOUND",
                        "message": "database_target_id was not found for this user.",
                    }
                },
            )
    # Create the API key
    try:
        key_data = create_api_key(
            user_id=user_id,
            email=email,
            name=payload.name,
            expires_days=payload.expires_days,
            database_target_id=database_target_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "KEY_CREATION_FAILED",
                    "message": f"Failed to create API key: {e}",
                }
            },
        )
    
    # Store metadata in Redis (not the actual key)
    expires_at = datetime.fromisoformat(key_data["expires_at"])
    
    await store.store_key_metadata(
        user_id=user_id,
        key_id=key_data["key_id"],
        name=payload.name,
        expires_at=expires_at,
        database_target_id=database_target_id,
    )

    key_data["database"] = _to_database_info_from_target(resolved_target)
    return CreateApiKeyResponse(**key_data)


@router.get(
    "",
    response_model=ListApiKeysResponse,
    summary="List all API keys",
    description="List all API keys for the authenticated user. Does not return the actual key values.",
)
async def list_keys(request: Request) -> ListApiKeysResponse:
    """List all API keys for the authenticated user."""
    _check_api_keys_configured()
    
    user = await get_current_user(request)
    user_id = user["sub"]
    
    try:
        store = await get_api_key_store()
        keys = await store.list_user_keys(user_id)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "REDIS_ERROR",
                    "message": f"Failed to connect to Redis: {e}",
                }
            },
        )
    
    target_cache: dict[str, dict | None] = {}
    mapped_keys: list[ApiKeyInfo] = []
    for key in keys:
        database_info: ApiKeyDatabaseInfo | None = None
        target_id = key.get("database_target_id")
        if target_id:
            if target_id not in target_cache:
                try:
                    target_cache[target_id] = await get_database_target(user_id, target_id)
                except RuntimeError:
                    target_cache[target_id] = None
            database_info = _to_database_info_from_target(target_cache[target_id])

        mapped_keys.append(
            ApiKeyInfo(
                **{
                    **key,
                    "database": database_info,
                }
            )
        )

    return ListApiKeysResponse(
        keys=mapped_keys,
        count=len(keys),
        max_keys=MAX_KEYS_PER_USER,
    )


@router.delete(
    "/{key_id}",
    response_model=RevokeApiKeyResponse,
    summary="Revoke an API key",
    description="Revoke an API key. The key will immediately stop working.",
)
async def revoke_key(
    request: Request,
    key_id: str,
) -> RevokeApiKeyResponse:
    """Revoke an API key."""
    _check_api_keys_configured()
    
    user = await get_current_user(request)
    user_id = user["sub"]
    
    try:
        store = await get_api_key_store()
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "REDIS_ERROR",
                    "message": f"Failed to connect to Redis: {e}",
                }
            },
        )
    
    # Verify the key belongs to this user
    metadata = await store.get_key_metadata(user_id, key_id)
    if not metadata:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "KEY_NOT_FOUND",
                    "message": "API key not found or does not belong to you.",
                }
            },
        )
    
    # Revoke the key
    success = await store.revoke_key(user_id, key_id)
    
    return RevokeApiKeyResponse(
        success=success,
        key_id=key_id,
        message="API key revoked successfully" if success else "Failed to revoke key",
    )
