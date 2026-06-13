"""Per-database schema resolver.

Builds the closed-form allowed-type lookup dicts used by the write path
(`canonicalize_entity_type`) and the MCP tool composition layer
(`handle_tools_list`). The resolver is pure — it takes a parsed
`schema_json` dict and returns a frozen lookup dict.

Aliases are folded into the single lookup dict so callers never have to
walk a rename chain. Per the alias design doc, aliases are never chained:
every alias entry points at a currently-live type name.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping, Optional


def _normalize_entity_types_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        result.append({
            "name": name,
            "description": str(item.get("description", "")).strip(),
        })
    return result


def _normalize_alias_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for old, new in value.items():
        old_s = str(old or "").strip()
        new_s = str(new or "").strip()
        if not old_s or not new_s:
            continue
        result[old_s] = new_s
    return result


def build_entity_type_resolver(
    schema: Optional[Mapping[str, Any]],
) -> Mapping[str, str]:
    """Return a frozen case-insensitive lookup: `input_name.lower() → canonical`.

    Both the authored type names and their alias entries are registered in
    a single dict, so one lookup resolves everything.

    Returns an empty MappingProxyType when schema is None or has no
    authored entity types — callers must treat that case as "schema not
    configured yet."
    """
    if not isinstance(schema, Mapping):
        return MappingProxyType({})

    entity_types = _normalize_entity_types_list(schema.get("entity_types"))
    aliases = _normalize_alias_map(schema.get("entity_type_aliases"))

    lookup: dict[str, str] = {}
    canonical_by_lower: dict[str, str] = {}

    for entity in entity_types:
        name = entity["name"]
        lookup[name.lower()] = name
        canonical_by_lower[name.lower()] = name

    for old, new in aliases.items():
        canonical = canonical_by_lower.get(new.lower())
        if canonical is None:
            # Alias points at a name that isn't in the authored list —
            # silently skip rather than poisoning the resolver. The local
            # dashboard's schema editor is responsible for preventing
            # dangling aliases; this is defense in depth.
            continue
        lookup[old.lower()] = canonical
        # Also register the alias's own lowercase, in case it's used
        # as-is somewhere else in the request path.

    return MappingProxyType(lookup)


def build_relationship_type_resolver(
    schema: Optional[Mapping[str, Any]],
) -> Mapping[str, str]:
    """Symmetric helper for relationship-type aliases. Not wired into the
    write path yet (relationships aren't enum-validated in Phase 4), but
    kept here for Phase 6 and tool composition parity."""
    if not isinstance(schema, Mapping):
        return MappingProxyType({})

    rel_types = _normalize_entity_types_list(schema.get("relationship_types"))
    aliases = _normalize_alias_map(schema.get("relationship_type_aliases"))

    lookup: dict[str, str] = {}
    canonical_by_lower: dict[str, str] = {}
    for rel in rel_types:
        name = rel["name"]
        lookup[name.lower()] = name
        canonical_by_lower[name.lower()] = name
    for old, new in aliases.items():
        canonical = canonical_by_lower.get(new.lower())
        if canonical is None:
            continue
        lookup[old.lower()] = canonical
    return MappingProxyType(lookup)


def entity_type_descriptions(
    schema: Optional[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Return [{name, description}, ...] for tool composition. Preserves
    authored order so the tool description reads naturally."""
    if not isinstance(schema, Mapping):
        return []
    return _normalize_entity_types_list(schema.get("entity_types"))
