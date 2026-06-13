from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from fastapi import HTTPException

from ..core.config import (
    DEFAULT_SCHEMA,
    RECONCILER_MERGE_EMBED_BLEND,
    RECONCILER_MERGE_THRESHOLD,
    RECONCILER_REVIEW_THRESHOLD,
    _PROFILE_HASH_FIELDS,
)


def _normalize_schema_block(raw: Any) -> dict[str, Any]:
    """Coerce a registry record's `schema` field into the canonical shape.

    Accepts a dict with any subset of the canonical keys and fills in defaults.
    Lists are normalized to [{"name": str, "description": str}, ...] and alias
    maps to flat `{old: new}` dicts. Non-dict input returns a fresh default.
    """
    if not isinstance(raw, dict):
        return json.loads(json.dumps(DEFAULT_SCHEMA))

    def _type_list(items: Any) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        if not isinstance(items, list):
            return result
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            description = str(item.get("description", "") or "").strip()
            result.append({"name": name, "description": description})
        return result

    def _alias_map(items: Any) -> dict[str, str]:
        result: dict[str, str] = {}
        if not isinstance(items, dict):
            return result
        for old, new in items.items():
            old_text = str(old or "").strip()
            new_text = str(new or "").strip()
            if not old_text or not new_text:
                continue
            result[old_text] = new_text
        return result

    return {
        "entity_types": _type_list(raw.get("entity_types")),
        "relationship_types": _type_list(raw.get("relationship_types")),
        "entity_type_aliases": _alias_map(raw.get("entity_type_aliases")),
        "relationship_type_aliases": _alias_map(raw.get("relationship_type_aliases")),
    }


def _normalize_reconciler_block(raw: Any) -> dict[str, float]:
    """Coerce a registry record's `reconciler` field into the canonical shape.

    Per-database overrides for the three reconciler tunables live here.
    Missing fields fall back to the process-wide env defaults so the
    registry record carries a complete, hashable picture of the values
    that are actually in effect.

    Values are clamped to the same [0, 1] band the reconciler already
    assumes (cosine distance and a blend weight). An out-of-band value
    in the file is coerced rather than raised so a hand-edited registry
    can't wedge the dashboard.
    """
    merge = RECONCILER_MERGE_THRESHOLD
    review = RECONCILER_REVIEW_THRESHOLD
    blend = RECONCILER_MERGE_EMBED_BLEND
    if isinstance(raw, dict):
        try:
            if raw.get("merge_threshold") is not None:
                merge = float(raw["merge_threshold"])
        except (TypeError, ValueError):
            pass
        try:
            if raw.get("review_threshold") is not None:
                review = float(raw["review_threshold"])
        except (TypeError, ValueError):
            pass
        try:
            if raw.get("merge_embed_blend") is not None:
                blend = float(raw["merge_embed_blend"])
        except (TypeError, ValueError):
            pass
    merge = min(max(merge, 0.0), 1.0)
    review = min(max(review, 0.0), 1.0)
    # merge threshold must stay ≤ review threshold or the three-band
    # classification collapses (merge band would eat the review band).
    if review < merge:
        review = merge
    blend = min(max(blend, 0.0), 1.0)
    return {
        "merge_threshold": merge,
        "review_threshold": review,
        "merge_embed_blend": blend,
    }


def _normalize_type_name(raw: str) -> str:
    """Title-case an authored schema type name.

    `"customer account"` → `"Customer Account"`. Preserves intra-word
    capitalization (`"OAuth Client"` → `"Oauth Client"` would be wrong,
    so we only touch the first letter of each whitespace-delimited token).
    Phase 6 Step 6.2.
    """
    parts = [p for p in str(raw or "").split() if p.strip()]
    return " ".join(part[:1].upper() + part[1:] for part in parts)


def _authored_schema_list_key(kind: str) -> str:
    if kind == "entity":
        return "entity_types"
    if kind == "relationship":
        return "relationship_types"
    raise ValueError(f"Unknown schema kind: {kind}")


def _authored_schema_alias_key(kind: str) -> str:
    if kind == "entity":
        return "entity_type_aliases"
    if kind == "relationship":
        return "relationship_type_aliases"
    raise ValueError(f"Unknown schema kind: {kind}")


def _get_or_create_authored_schema(record: dict[str, Any]) -> dict[str, Any]:
    schema = record.get("schema")
    if not isinstance(schema, dict):
        schema = json.loads(json.dumps(DEFAULT_SCHEMA))
        record["schema"] = schema
    schema.setdefault("entity_types", [])
    schema.setdefault("relationship_types", [])
    schema.setdefault("entity_type_aliases", {})
    schema.setdefault("relationship_type_aliases", {})
    return schema


def _upsert_authored_type(
    record: dict[str, Any],
    *,
    kind: str,
    name: str,
    description: str,
) -> dict[str, str]:
    """Add or update an authored type in-place on the record.

    Create is idempotent on name (case-insensitive match updates in place).
    Raises on alias collisions — if `name` is currently the old side of an
    alias (`Human → Person`), re-introducing it as an authoritative type
    would create an ambiguous state (write path wouldn't know whether to
    canonicalize inputs to the alias target or to the new type).
    """
    schema = _get_or_create_authored_schema(record)
    list_key = _authored_schema_list_key(kind)
    alias_key = _authored_schema_alias_key(kind)

    canonical_name = _normalize_type_name(name)
    if not canonical_name:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_NAME",
                    "message": "Name cannot be empty.",
                }
            },
        )
    description_text = str(description or "").strip()
    if not description_text:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_DESCRIPTION",
                    "message": "Description is required.",
                }
            },
        )

    aliases = schema.get(alias_key) or {}
    for alias_old, alias_new in aliases.items():
        if str(alias_old).strip().lower() == canonical_name.lower():
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "ALIAS_COLLISION",
                        "message": (
                            f"'{canonical_name}' is currently aliased to "
                            f"'{alias_new}'. Aliases are append-only, so "
                            "re-adding this name as an authoritative type "
                            "would create an ambiguity. Pick a different name."
                        ),
                    }
                },
            )

    types = schema.get(list_key) or []
    for item in types:
        if str(item.get("name", "")).strip().lower() == canonical_name.lower():
            item["name"] = canonical_name
            item["description"] = description_text
            schema[list_key] = types
            return {"name": canonical_name, "description": description_text}
    types.append({"name": canonical_name, "description": description_text})
    schema[list_key] = types
    return {"name": canonical_name, "description": description_text}


def _delete_authored_type(
    record: dict[str, Any],
    *,
    kind: str,
    name: str,
) -> bool:
    """Remove an authored type. Does NOT touch existing graph nodes with
    that label — the write-strict/read-loose asymmetry is the whole point.
    Returns False if the type wasn't present.
    """
    schema = _get_or_create_authored_schema(record)
    list_key = _authored_schema_list_key(kind)
    canonical_name = _normalize_type_name(name)
    types = schema.get(list_key) or []
    remaining = [
        item
        for item in types
        if str(item.get("name", "")).strip().lower() != canonical_name.lower()
    ]
    if len(remaining) == len(types):
        return False
    schema[list_key] = remaining
    return True


def _rename_authored_type(
    record: dict[str, Any],
    *,
    kind: str,
    old_name: str,
    new_name: str,
    description: Optional[str],
) -> dict[str, str]:
    """Soft-alias rename (Phase 6 Step 6.4).

    1. Add/update the new type in the list.
    2. Drop the old type from the list (so it no longer advertises).
    3. Add `old → new` to the alias map.
    4. Cumulative: rewrite any existing `X → old` to `X → new`.
    5. Drop any self-referential aliases that fall out.
    """
    schema = _get_or_create_authored_schema(record)
    list_key = _authored_schema_list_key(kind)
    alias_key = _authored_schema_alias_key(kind)

    old_canonical = _normalize_type_name(old_name)
    new_canonical = _normalize_type_name(new_name)
    if not old_canonical or not new_canonical:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_NAME",
                    "message": "Both old_name and new_name are required.",
                }
            },
        )
    if old_canonical.lower() == new_canonical.lower():
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_RENAME",
                    "message": "New name must differ from the old name.",
                }
            },
        )

    types = schema.get(list_key) or []
    existing_description: Optional[str] = None
    for item in types:
        if str(item.get("name", "")).strip().lower() == old_canonical.lower():
            existing_description = str(item.get("description", "") or "").strip() or None
            break
    description_text = str(description or existing_description or "").strip()
    if not description_text:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_DESCRIPTION",
                    "message": (
                        "Description is required when renaming (pass "
                        "`description`, or ensure the old type already has "
                        "one in the schema)."
                    ),
                }
            },
        )

    # Drop both old and new (case-insensitive) from the list, then append
    # the fresh entry. This handles the edge case where new_canonical was
    # previously a separate authored type — Phase 6 rules merge them into
    # the rename target.
    filtered = [
        item
        for item in types
        if str(item.get("name", "")).strip().lower()
        not in {old_canonical.lower(), new_canonical.lower()}
    ]
    filtered.append({"name": new_canonical, "description": description_text})
    schema[list_key] = filtered

    aliases = dict(schema.get(alias_key) or {})
    aliases[old_canonical] = new_canonical
    for key, value in list(aliases.items()):
        if str(value).strip().lower() == old_canonical.lower():
            aliases[key] = new_canonical
    # Remove self-referential entries.
    aliases = {
        k: v
        for k, v in aliases.items()
        if str(k).strip().lower() != str(v).strip().lower()
    }
    schema[alias_key] = aliases

    return {"name": new_canonical, "description": description_text}


def _canonicalize_for_hash(value: Any) -> Any:
    """Return a deterministic canonical form of value for hashing.

    - Dicts are emitted with sorted keys.
    - Lists of dicts with a `name` field are sorted by name.
    - Plain lists pass through in their original order (call sites are
      responsible for feeding in semantically-ordered content; type lists use
      `name`-sorted output so reorders in the UI don't churn the hash).
    """
    if isinstance(value, dict):
        return {k: _canonicalize_for_hash(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        if value and all(isinstance(item, dict) and "name" in item for item in value):
            return [
                _canonicalize_for_hash(item)
                for item in sorted(value, key=lambda item: str(item.get("name", "")))
            ]
        return [_canonicalize_for_hash(item) for item in value]
    return value


def _compute_profile_hash(record: dict[str, Any]) -> str:
    """Compute a content-addressed hash of the observable-state subset of a
    database registry record. Covers every field that affects how the cloud
    serves MCP requests against this target — schema, tool manifest, connection
    info, credentials — while deliberately excluding runtime/liveness fields.

    The hash is stable across semantically-equivalent edits (reordering type
    lists, reordering alias map keys, whitespace differences) because the
    payload is canonicalized before serialization.
    """
    subset: dict[str, Any] = {}
    for field in _PROFILE_HASH_FIELDS:
        if field in record:
            subset[field] = record[field]
    canonical = _canonicalize_for_hash(subset)
    serialized = json.dumps(canonical, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
