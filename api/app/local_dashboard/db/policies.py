from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional

from ..core.config import (
    LOCAL_DASHBOARD_POLICIES_FILE,
    POLICY_RATE_LIMIT_BURST,
    POLICY_RATE_LIMIT_PER_MINUTE,
)
from ..core.models import ProxyQueryRequest


# ---------------------------------------------------------------------------
# Query proxy: policy hook (Step 0.5)
# ---------------------------------------------------------------------------
# Enforcement happens inside the proxy, on the user's own machine, because:
#   1. Defense in depth against a cloud-side compromise.
#   2. User-authored policy belongs on the user's device.
#
# The proxy ships with two rules in v1:
#   - read-only enforcement (string/comment-safe Cypher keyword scan)
#   - per-api_key_id token-bucket rate limit
# Additional rules are additive — keep the return shape stable.

_POLICY_WRITE_KEYWORDS = (
    "create",
    "merge",
    "delete",
    "detach",
    "set",
    "drop",
    "remove",
    "foreach",
    "call",
)
_POLICY_WRITE_CALL_PROCS = (
    "refactor.",
    "migrate.",
    "create.",
    "mg.",
)

# Language guard (Phase 2.5): the proxy only speaks Cypher. If a caller
# submits obviously-SQL (SELECT/INSERT/UPDATE/... as first keyword) or a
# Gremlin traversal (`g.V()`, `g.E()`), reject it before routing. Cypher
# has no SELECT/FROM/JOIN keywords, so an opening SELECT is always a
# language mismatch, not a legitimate query Cypher just happens to
# support. Gremlin's `g.` prefix is equally unambiguous.
_NON_CYPHER_LEADING_KEYWORDS = (
    "select",
    "insert",
    "update",
    "alter",
    "truncate",
    "grant",
    "revoke",
)
_GREMLIN_SIGNATURE = re.compile(r"\bg\s*\.\s*(v|e|addv|adde)\s*\(", re.IGNORECASE)


def _cypher_language_mismatch(cypher: str) -> Optional[str]:
    cleaned = _strip_cypher_literals_and_comments(cypher).strip()
    if not cleaned:
        return None
    first_token = cleaned.split(None, 1)[0].lower()
    if first_token in _NON_CYPHER_LEADING_KEYWORDS:
        return first_token.upper()
    if _GREMLIN_SIGNATURE.search(cleaned):
        return "GREMLIN"
    return None


def _strip_cypher_literals_and_comments(cypher: str) -> str:
    """Remove string literals, block comments, and line comments from cypher
    so keyword scans don't false-positive on `RETURN "CREATE INDEX"` or
    `// CREATE`. Not a full parser, but handles the obvious footguns the
    migration plan calls out explicitly.
    """

    out: list[str] = []
    i = 0
    n = len(cypher)
    while i < n:
        ch = cypher[i]
        nxt = cypher[i + 1] if i + 1 < n else ""
        # Block comment
        if ch == "/" and nxt == "*":
            end = cypher.find("*/", i + 2)
            if end == -1:
                return "".join(out)
            i = end + 2
            out.append(" ")
            continue
        # Line comment
        if ch == "/" and nxt == "/":
            end = cypher.find("\n", i + 2)
            if end == -1:
                return "".join(out)
            i = end
            out.append(" ")
            continue
        # String literals
        if ch in ("'", '"', "`"):
            quote = ch
            j = i + 1
            while j < n:
                if cypher[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if cypher[j] == quote:
                    j += 1
                    break
                j += 1
            i = j
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _cypher_has_write_clause(cypher: str) -> Optional[str]:
    """Return the offending write clause, or None if read-only."""
    cleaned = _strip_cypher_literals_and_comments(cypher).lower()
    # Detect obvious keywords with word boundaries.
    for keyword in _POLICY_WRITE_KEYWORDS:
        pattern = rf"\b{re.escape(keyword)}\b"
        match = re.search(pattern, cleaned)
        if not match:
            continue
        if keyword == "call":
            # `CALL` is only a write when it invokes a known write-stored proc.
            # Otherwise `CALL vector_search.search(...)` would be rejected.
            tail = cleaned[match.end():].lstrip()
            if any(tail.startswith(proc) for proc in _POLICY_WRITE_CALL_PROCS):
                return "CALL"
            continue
        return keyword.upper()
    return None


_policy_file_cache: dict[str, Any] = {"mtime": 0.0, "data": None}


def _load_policies_file() -> dict[str, Any]:
    """Read `~/.loreholm/policies.json` (bind-mounted in the container) with
    mtime-based cache invalidation so edits take effect without a restart.
    A missing file is treated as an empty-but-valid policy set."""
    try:
        if not LOCAL_DASHBOARD_POLICIES_FILE.exists():
            _policy_file_cache["mtime"] = 0.0
            _policy_file_cache["data"] = {
                "rate_limits": {
                    "default": {
                        "per_minute": POLICY_RATE_LIMIT_PER_MINUTE,
                        "burst": POLICY_RATE_LIMIT_BURST,
                    }
                },
                "read_only_keys": [],
                "query_allowlist": None,
            }
            return _policy_file_cache["data"]
        stat = LOCAL_DASHBOARD_POLICIES_FILE.stat()
        mtime = stat.st_mtime
        if mtime == _policy_file_cache["mtime"] and _policy_file_cache["data"] is not None:
            return _policy_file_cache["data"]
        raw = LOCAL_DASHBOARD_POLICIES_FILE.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
        _policy_file_cache["mtime"] = mtime
        _policy_file_cache["data"] = data
        return data
    except Exception as exc:  # pragma: no cover - best-effort fallback
        print(f"[local-dashboard] failed to load policies.json: {exc}", flush=True)
        return _policy_file_cache["data"] or {}


# Token-bucket rate limiter keyed by api_key_id (or "__anonymous__" when the
# cloud didn't pass one). Simple in-memory state — there's only one dashboard
# process per device, so no need for cross-process synchronization.
_rate_limit_state: dict[str, tuple[float, float]] = {}
_rate_limit_lock = threading.Lock()


def _rate_limit_check(key: str, per_minute: int, burst: int) -> bool:
    import time
    now = time.monotonic()
    refill_per_sec = max(per_minute / 60.0, 0.0)
    capacity = max(float(burst), 1.0)
    with _rate_limit_lock:
        tokens, last = _rate_limit_state.get(key, (capacity, now))
        elapsed = max(now - last, 0.0)
        tokens = min(capacity, tokens + elapsed * refill_per_sec)
        if tokens < 1.0:
            _rate_limit_state[key] = (tokens, now)
            return False
        _rate_limit_state[key] = (tokens - 1.0, now)
        return True


def _evaluate_policy(
    request: ProxyQueryRequest,
    *,
    database_record: dict[str, Any],
) -> Optional[dict[str, str]]:
    """Run the policy hook before executing a proxy query. Returns None on
    allow, or a `{code, rule, reason}` dict on deny."""
    policies = _load_policies_file()

    # Language guard: reject Gremlin (and SQL when the caller didn't opt in
    # via the `language` field) before any other rule runs so a caller pasting
    # the wrong dialect gets a specific error instead of a parser-level
    # failure downstream. SQL is allowed only when explicitly requested,
    # because a few cloud-side queries need ArcadeDB SQL functions Cypher
    # doesn't expose (notably `vectorNeighbors`, SQL-only in 26.x).
    if request.language == "cypher":
        language_offender = _cypher_language_mismatch(request.cypher)
        if language_offender:
            return {
                "code": "POLICY_DENIED",
                "rule": "language_guard",
                "reason": (
                    f"Proxy accepts Cypher only; received {language_offender}."
                ),
            }

    # Read-only enforcement. Either the request flagged itself read_only, or
    # the api_key is on the read_only_keys list.
    read_only_keys = policies.get("read_only_keys") or []
    key_is_read_only = bool(
        request.api_key_id
        and isinstance(read_only_keys, list)
        and request.api_key_id in read_only_keys
    )
    if request.read_only or key_is_read_only:
        offending = _cypher_has_write_clause(request.cypher)
        if offending:
            return {
                "code": "POLICY_DENIED",
                "rule": "readonly_enforcement",
                "reason": f"Read-only key attempted write clause: {offending}",
            }

    # Rate limit per api_key_id (falls back to a shared bucket for unkeyed).
    rate_limits = policies.get("rate_limits") or {}
    default_limits = rate_limits.get("default") or {}
    per_minute = int(default_limits.get("per_minute", POLICY_RATE_LIMIT_PER_MINUTE))
    burst = int(default_limits.get("burst", POLICY_RATE_LIMIT_BURST))
    key = request.api_key_id or "__anonymous__"
    if not _rate_limit_check(key, per_minute, burst):
        return {
            "code": "POLICY_DENIED",
            "rule": "rate_limit",
            "reason": f"Rate limit exceeded ({per_minute}/min, burst {burst}).",
        }

    return None
