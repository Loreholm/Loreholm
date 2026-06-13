"""Service layer package."""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from app.services.arcadedb_store import ArcadeDBConfig, ArcadeDBStore
from app.services.graph_store_errors import (
    GraphStorePolicyDeniedError,
    GraphStoreUnavailableError,
)


class StoreProtocol(Protocol):
    # ArcadeDB returns the staging envelope `{staged, message}` for upserts.
    # MCP handlers branch on `isinstance(..., dict)` to surface it.
    def upsert_entities(
        self, inputs: List[Dict[str, object]]
    ) -> "List[Dict[str, object]] | Dict[str, object]":
        ...

    def delete_entities(self, entity_ids: List[str]) -> Dict[str, object]:
        ...

    def delete_memories(self, memory_ids: List[str]) -> Dict[str, object]:
        ...

    def write_memory(self, payload: Dict[str, object]) -> Dict[str, object]:
        ...

    def link_entities(self, payload: Dict[str, object]) -> Dict[str, object]:
        ...

    def search(
        self,
        query: str,
        top_k: int,
        entity_types: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        since: Optional[str] = None,
        include_meta: bool = False,
    ) -> object:
        ...

    def search_similar_entities(
        self,
        query: str,
        top_k: int,
        type: Optional[str] = None,
        include_meta: bool = False,
    ) -> object:
        ...

    def context(self, entity_ids: List[str], depth: int, limit: int) -> Dict[str, List[Dict[str, object]]]:
        ...

    def recent(self, limit: int, since: Optional[str]) -> List[Dict[str, object]]:
        ...

    def stats(self) -> Dict[str, object]:
        ...


def get_store_class() -> type:
    """Return the configured store class (not an instance)."""
    return ArcadeDBStore


def get_store_config_class() -> type:
    return ArcadeDBConfig


# BYODB: per-user store functions
from app.services.user_store import (
    get_user_store,
    get_user_tailscale_ip,
    verify_user_connection,
    clear_user_store_cache,
    user_id_to_namespace,
)

__all__ = [
    "StoreProtocol",
    "ArcadeDBStore",
    "ArcadeDBConfig",
    "GraphStoreUnavailableError",
    "GraphStorePolicyDeniedError",
    "get_store_class",
    "get_store_config_class",
    # BYODB exports
    "get_user_store",
    "get_user_tailscale_ip",
    "verify_user_connection",
    "clear_user_store_cache",
    "user_id_to_namespace",
]
