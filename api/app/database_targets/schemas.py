from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class DatabaseTargetInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class CreateDatabaseTargetRequest(DatabaseTargetInput):
    pass


class UpdateDatabaseTargetRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)


class DatabaseTargetInfo(BaseModel):
    target_id: str
    name: str
    database_id: str
    created_at: str
    updated_at: str


class ListDatabaseTargetsResponse(BaseModel):
    targets: List[DatabaseTargetInfo]
    count: int


class DeleteDatabaseTargetResponse(BaseModel):
    success: bool
    target_id: str
    message: str
