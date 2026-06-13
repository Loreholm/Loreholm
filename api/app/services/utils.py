from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Mapping, Optional


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


def normalize(value: str) -> str:
    return value.strip().lower()


def canonicalize_entity_type(value: str, allowed: Mapping[str, str]) -> str:
    """Resolve a user-supplied entity type string to a canonical, per-database
    vocabulary entry.

    `allowed` is the closed-form lookup for one database: lowercased name →
    canonical name, with alias rows folded in. Built by
    `build_entity_type_resolver` in `app.services.schema_resolver`.

    The `allowed` argument is required (no global default) because the legacy
    six-type vocabulary was fully deprecated by the multi-schema migration.
    An empty `allowed` mapping raises: the database has no schema authored,
    so every write must be rejected until the user configures at least one
    entity type in the local dashboard.
    """
    key = normalize(value)
    if not allowed:
        raise ValueError(
            "No entity types configured for this database. Ask the user to "
            "author at least one entity type in the local dashboard schema "
            "editor before writing memories."
        )
    canonical = allowed.get(key)
    if canonical is None:
        allowed_list = ", ".join(sorted(set(allowed.values())))
        raise ValueError(
            f"Invalid entity type '{value}'. Allowed types: {allowed_list}"
        )
    return canonical


def dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
