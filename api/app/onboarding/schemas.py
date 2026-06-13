from __future__ import annotations

from typing import Optional
from datetime import datetime

from pydantic import BaseModel, Field


class OnboardingStatus(BaseModel):
    """Response for GET /onboarding/status"""
    user_id: str
    email: str
    initialized: bool
    node_name: Optional[str] = None
    node_count: int = 0
    last_seen: Optional[datetime] = None
    status: str = "pending"  # pending, online, offline
    install_command: Optional[str] = None
    node_connected: bool = False
    database_connected: bool = False
    update_command: Optional[str] = None


class InitializeRequest(BaseModel):
    """Request for POST /onboarding/initialize (optional body)"""
    node_name: Optional[str] = None


class InitializeResponse(BaseModel):
    """Response for POST /onboarding/initialize"""
    user_id: str
    pre_auth_key: str
    install_command: str
    expires_at: datetime


class NodeSummary(BaseModel):
    """One row in the user's Devices table."""
    id: str
    name: str
    online: bool
    last_seen: Optional[datetime] = None
    tailscale_ip: Optional[str] = None


class NodesListResponse(BaseModel):
    """Response for GET /onboarding/nodes."""
    nodes: list[NodeSummary] = Field(default_factory=list)
    cap: int
    tier: str


class CreateNodePreauthRequest(BaseModel):
    """Request for POST /onboarding/nodes/preauth.

    `node_name` is baked into the returned install command as `--name <node_name>`
    so the Tailscale client registers with the user-chosen name on first connect.
    """
    node_name: Optional[str] = None


class CreateNodePreauthResponse(BaseModel):
    """Response for POST /onboarding/nodes/preauth."""
    pre_auth_key: str
    install_command: str
    expires_at: datetime


class RenameNodeRequest(BaseModel):
    """Request for PATCH /onboarding/nodes/{id}."""
    name: str
