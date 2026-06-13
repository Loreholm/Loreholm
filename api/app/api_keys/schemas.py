"""Pydantic schemas for API key endpoints."""

from __future__ import annotations

from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator


class ApiKeyDatabaseInfo(BaseModel):
    """Database routing metadata returned from API-key endpoints (safe for UI).

    Under the query-proxy topology the cloud never dials the user's
    ArcadeDB directly — every query is forwarded to the user's local
    dashboard over the Tailscale mesh, and the local dashboard is the
    only database client. As a result this schema carries only the
    logical identifiers needed to label the key in the UI; no database
    connection fields (host/port/username/password/sslmode) are stored
    or returned.
    """

    target_id: Optional[str] = Field(
        default=None,
        description="Stable target identifier when the key uses server-side target references.",
    )
    name: str = Field(..., description="Database target name")
    database_id: str = Field(..., description="Stable identifier for this database target")


class ApiKeyDatabaseSyncRequest(BaseModel):
    """Request cloud-side sync from local dashboard over Tailnet before key creation."""

    database_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Database identifier to resolve from the local dashboard registry.",
    )
    sync_mode: Literal["tailnet_pull"] = Field(
        default="tailnet_pull",
        description="Sync mode. Only tailnet_pull is supported currently.",
    )


class CreateApiKeyRequest(BaseModel):
    """Request to create a new API key."""
    
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="A friendly name for this key (e.g., 'Claude Desktop')",
        examples=["Claude Desktop", "Cursor IDE", "Production Server"],
    )
    expires_days: int = Field(
        default=365,
        ge=1,
        le=730,
        description="Number of days until the key expires (1-730)",
    )
    database_target_id: Optional[str] = Field(
        default=None,
        min_length=1,
        description=(
            "Preferred database target reference. "
            "When provided, the API key routes via this saved target."
        ),
    )
    database_sync: Optional[ApiKeyDatabaseSyncRequest] = Field(
        default=None,
        description=(
            "Optional atomic sync request. "
            "When provided, the API fetches profile metadata from the user's local dashboard "
            "over Tailnet and binds the key to the resolved target."
        ),
    )

    @model_validator(mode="after")
    def validate_create_mode(self) -> "CreateApiKeyRequest":
        if self.database_sync and self.database_target_id:
            raise ValueError("database_sync cannot be combined with database_target_id.")
        return self


class CreateApiKeyResponse(BaseModel):
    """Response after creating an API key.
    
    IMPORTANT: The `api_key` field is only returned once at creation time.
    Users must copy it immediately as it cannot be retrieved later.
    """
    
    api_key: str = Field(
        ...,
        description="The API key token. Copy this now - it won't be shown again!",
    )
    key_id: str = Field(
        ...,
        description="Unique identifier for this key (used for revocation)",
    )
    name: str = Field(
        ...,
        description="The friendly name for this key",
    )
    created_at: str = Field(
        ...,
        description="ISO 8601 timestamp when the key was created",
    )
    expires_at: str = Field(
        ...,
        description="ISO 8601 timestamp when the key will expire",
    )
    database: Optional[ApiKeyDatabaseInfo] = Field(
        default=None,
        description="Database target metadata bound to this key, if configured.",
    )


class ApiKeyInfo(BaseModel):
    """Information about an API key (does not include the actual key)."""
    
    key_id: str = Field(
        ...,
        description="Unique identifier for this key",
    )
    name: str = Field(
        ...,
        description="The friendly name for this key",
    )
    created_at: str = Field(
        ...,
        description="ISO 8601 timestamp when the key was created",
    )
    expires_at: str = Field(
        ...,
        description="ISO 8601 timestamp when the key will expire",
    )
    is_expired: bool = Field(
        ...,
        description="Whether the key has expired",
    )
    is_revoked: bool = Field(
        ...,
        description="Whether the key has been revoked",
    )
    is_active: bool = Field(
        ...,
        description="Whether the key is currently usable (not expired or revoked)",
    )
    database: Optional[ApiKeyDatabaseInfo] = Field(
        default=None,
        description="Database target metadata bound to this key, if configured.",
    )


class ListApiKeysResponse(BaseModel):
    """Response listing all API keys for a user."""
    
    keys: List[ApiKeyInfo] = Field(
        ...,
        description="List of API keys (metadata only)",
    )
    count: int = Field(
        ...,
        description="Total number of keys",
    )
    max_keys: int = Field(
        ...,
        description="Maximum number of keys allowed per user",
    )


class RevokeApiKeyResponse(BaseModel):
    """Response after revoking an API key."""
    
    success: bool = Field(
        ...,
        description="Whether the revocation was successful",
    )
    key_id: str = Field(
        ...,
        description="The ID of the revoked key",
    )
    message: str = Field(
        ...,
        description="Human-readable status message",
    )
