from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class QueryRequest(BaseModel):
    cypher: str = Field(..., min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class GraphRequest(BaseModel):
    seed_label: Optional[str] = None
    seed_property: Optional[str] = None
    seed_value: Optional[str] = None
    depth: int = Field(default=1, ge=1, le=3)
    limit_nodes: int = Field(default=200, ge=1, le=500)


class SyncResolveRequest(BaseModel):
    database_id: str = Field(..., min_length=1, max_length=100)


class ProxyQueryRequest(BaseModel):
    database_id: str = Field(..., min_length=1, max_length=100)
    cypher: str = Field(..., min_length=1, max_length=100_000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = False
    api_key_id: Optional[str] = Field(default=None, max_length=256)
    language: Literal["cypher", "sql"] = "cypher"


class AuthoredSchemaTypeRequest(BaseModel):
    """Create-or-update body for entity/relationship types in the authored
    schema editor (Phase 6). `name` is normalized server-side to Title Case;
    the LLM-facing description is required because MCP tool parameter
    descriptions depend on it (see docs/06_ToolSchemas.md).
    """
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1, max_length=500)


class AuthoredSchemaRenameRequest(BaseModel):
    """Rename an entity/relationship type using the soft-alias strategy.

    Saves `old_name → new_name` in the alias map and (per the
    cumulative-never-chained rule) rewrites any existing `X → old_name`
    aliases to `X → new_name`. Existing graph nodes are not touched —
    the read path stays loose and stale labels surface gracefully.
    """
    old_name: str = Field(..., min_length=1, max_length=128)
    new_name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)


class CreateDatabaseRequest(BaseModel):
    database_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=100)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: Optional[str] = Field(default=None, max_length=128)
    password: Optional[str] = Field(default=None, max_length=512)
    sslmode: Literal["disable", "require"] = Field(default="disable")

    @model_validator(mode="after")
    def validate_auth_pair(self) -> "CreateDatabaseRequest":
        if (self.username and not self.password) or (self.password and not self.username):
            raise ValueError("username and password must be provided together.")
        return self


class WizardMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(..., min_length=1, max_length=6000)


class WizardChatRequest(BaseModel):
    messages: list[WizardMessage] = Field(default_factory=list)
    database_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)
    # Resume fields — populated by the frontend after the user approves/denies a pending tool call
    conversation_state: Optional[list[dict[str, Any]]] = None
    approved_tool_call_id: Optional[str] = None
    denied_tool_call_id: Optional[str] = None


class PromptDraftRequest(BaseModel):
    goal: str = Field(..., min_length=4, max_length=2000)
    audience: Optional[str] = Field(default=None, max_length=500)
    constraints: Optional[str] = Field(default=None, max_length=2000)
    context: Optional[str] = Field(default=None, max_length=4000)
    database_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)


class BifrostProvider(BaseModel):
    provider: str
    api_key: Optional[str] = None
    model: str
    base_url: Optional[str] = None


class BifrostConfigRequest(BaseModel):
    providers: list[BifrostProvider]


class SetupAccountRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=512)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=512)


class BifrostDiscoverModelsRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    preferred_model: Optional[str] = None
    base_url: Optional[str] = None


class BifrostDisconnectProviderRequest(BaseModel):
    provider: str


class CreateDashboardKeyRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=80)


class UpdatePreferencesRequest(BaseModel):
    favorite_wizard_model: Optional[str] = Field(default=None, max_length=200)
