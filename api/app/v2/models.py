from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CaptureKind(str, Enum):
    event = "event"
    snapshot = "snapshot"


class CaptureMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(pattern=r"^2\.[0-9]+$")
    spine_version: str = Field(min_length=1, max_length=64)
    adapter_id: str = Field(min_length=1, max_length=128)
    adapter_version: str = Field(min_length=1, max_length=64)
    device_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    queue_age_seconds: int = Field(default=0, ge=0, le=31_536_000)
    policy_version: int = Field(ge=1)


class CaptureEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    kind: CaptureKind
    capture_class: str = Field(alias="class", pattern=r"^[a-z][a-z0-9_.-]{2,63}$")
    surface: str = Field(min_length=1, max_length=128)
    session_ref: str | None = Field(default=None, max_length=512)
    occurred_at: datetime
    payload: dict[str, Any]
    refs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    hints: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    meta: CaptureMeta

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_snapshot_identity(self) -> "CaptureEnvelope":
        if self.kind is CaptureKind.snapshot:
            digest = self.payload.get("content_hash")
            if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
                raise ValueError("snapshot payload requires content_hash as sha256:<64 hex chars>")
            try:
                bytes.fromhex(digest[7:])
            except ValueError as exc:
                raise ValueError("snapshot content_hash must be hexadecimal") from exc
        return self


class CaptureReceipt(BaseModel):
    capture_id: str
    status: Literal["accepted", "duplicate", "quarantined", "policy_blocked"]
    received_at: datetime
    normalized_at: datetime
    clock_adjusted: bool


class CaptureBatch(BaseModel):
    captures: list[CaptureEnvelope] = Field(min_length=1, max_length=250)


class CaptureBatchReceipt(BaseModel):
    receipts: list[CaptureReceipt]


class CaptureClassPolicy(BaseModel):
    capture: bool = True
    remote_processing: Literal["local_only", "sanitized_remote", "derived_only", "unrestricted"] = "sanitized_remote"


class InstancePolicy(BaseModel):
    version: int = Field(default=1, ge=1)
    contract_min: str = "2.0"
    contract_max: str = "2.0"
    classes: dict[str, CaptureClassPolicy] = Field(default_factory=lambda: {
        "transcript.message": CaptureClassPolicy(),
        "push.document": CaptureClassPolicy(),
        "push.screen": CaptureClassPolicy(),
    })
    mining_status: Literal["active", "paused"] = "paused"


class ModelEndpointConfig(BaseModel):
    base_url: str = Field(default="http://vllm:8000", pattern=r"^https?://[^\s]+$")
    model_name: str = Field(default="loreholm-local", min_length=1, max_length=256)
    provider_name: str = Field(default="vllm-local", pattern=r"^[a-z][a-z0-9-]{2,63}$")
    allow_private_network: bool = True
    processing_location: Literal["local", "remote"] = "local"


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=128)
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)


class AdminStatus(BaseModel):
    policy: InstancePolicy
    bifrost_ok: bool
    model_provider: dict[str, Any] | None = None
    model_endpoint: ModelEndpointConfig
    vllm_ok: bool
    bifrost_dashboard_url: str
    version: str = "1.0.0"


class MiningRunView(BaseModel):
    run_id: str
    salience_id: str
    scope_id: str
    generation: int
    stage: str
    miner_version: str
    parent_run_id: str | None
    status: Literal["succeeded", "failed"]
    output: dict[str, Any]
    covered_capture_ids: list[str]
    evidence_capture_ids: list[str]
    error: str | None
    created_at: datetime
    completed_at: datetime
