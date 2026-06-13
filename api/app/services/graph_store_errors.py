"""Graph-store error base classes raised by `arcadedb_store.py`."""
from __future__ import annotations


class GraphStoreUnavailableError(RuntimeError):
    """Transport-level failure reaching the graph-store backend.

    Raised when the local dashboard proxy is unreachable, the backing
    store is down, or the response is unparseable. Callers should treat
    this as retry-safe for read operations and surface it to the LLM as
    "the user's graph is temporarily unreachable."
    """


class GraphStorePolicyDeniedError(RuntimeError):
    """User-configured policy refused the query (read-only, rate limit).

    Carries `rule` and `reason` fields so MCP tool handlers can surface
    the specific rule back to the LLM.
    """

    def __init__(self, rule: str, reason: str) -> None:
        super().__init__(f"{rule}: {reason}")
        self.rule = rule
        self.reason = reason
