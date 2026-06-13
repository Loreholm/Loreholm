"""Chat agent tool definitions and system prompt builder.

The chat agent is for *interacting with* a database — storing and
retrieving information — not for altering the database structure itself.
The tool set is a subset of the wizard tools: schema inspection, and
read/write Cypher queries.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from fastapi import HTTPException

from .bifrost import _bifrost_error_message, _schema_context_for_prompt
from ..db.cypher import _require_readonly_query, _run_query
from ..db.graph import _schema_payload
from ..db.registry import _find_database, _load_registry
from ..db.schemas import _normalize_schema_block
from ..ai.wizard_tools import _parse_tool_arguments, _resolve_tool_database_id

_chat_abort_event = threading.Event()


_DEFAULT_CHAT_PREAMBLE = (
    "You are a loreholm chat agent. Your job is to help the user store, "
    "retrieve, and explore information in their graph database using Cypher queries. "
    "You have tools to inspect the database schema and run read/write Cypher queries. "
    "Be action-oriented: run queries to answer questions rather than asking the user to do it. "
    "When writing data, use MERGE where appropriate to avoid duplicates. "
    "Keep replies concise and include relevant query results. "
    "If you are unsure about the schema, inspect it first before running queries."
)


def _build_chat_system_prompt(record: dict[str, Any]) -> str:
    """Build a system prompt tailored to the selected database's schema.

    If the database record has a non-empty ``system_prompt`` field, it replaces
    the default preamble. The schema context section is always appended so the
    agent has type information regardless of preamble customization.
    """
    schema_context = _schema_context_for_prompt(record)
    authored = _normalize_schema_block(record.get("schema"))

    entity_types = authored.get("entity_types", [])
    relationship_types = authored.get("relationship_types", [])

    type_lines: list[str] = []
    for et in entity_types:
        name = et.get("name", "")
        desc = et.get("description", "")
        if name:
            type_lines.append(f"  - {name}: {desc}" if desc else f"  - {name}")
    rel_lines: list[str] = []
    for rt in relationship_types:
        name = rt.get("name", "")
        desc = rt.get("description", "")
        if name:
            rel_lines.append(f"  - {name}: {desc}" if desc else f"  - {name}")

    schema_section = f"Database schema:\n{schema_context}"
    if type_lines:
        schema_section += f"\n\nAuthored entity types:\n" + "\n".join(type_lines)
    if rel_lines:
        schema_section += f"\n\nAuthored relationship types:\n" + "\n".join(rel_lines)

    custom = str(record.get("system_prompt") or "").strip()
    preamble = custom if custom else _DEFAULT_CHAT_PREAMBLE
    return f"{preamble}\n\n{schema_section}"


def _chat_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_database_schema",
                "description": (
                    "Get both the authored schema (entity_types, relationship_types) "
                    "and the live schema inferred from existing ArcadeDB data (labels, "
                    "relationships, properties, indexes, constraints)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_readonly_query",
                "description": "Run a readonly Cypher query (MATCH/RETURN) and return rows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cypher": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["cypher"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_query",
                "description": (
                    "Run arbitrary Cypher on the database including writes "
                    "(CREATE/MERGE/SET/DELETE). Use this to store information "
                    "or modify data in the graph."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cypher": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["cypher"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _chat_tool_result(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    database_id: str,
) -> dict[str, Any]:
    """Execute a chat tool and return the result dict."""
    registry = _load_registry()
    record = _find_database(registry, database_id)

    if tool_name == "get_database_schema":
        return {
            "database_id": database_id,
            "schema": _schema_payload(record),
            "authored_schema": _normalize_schema_block(record.get("schema")),
        }

    if tool_name == "run_readonly_query":
        cypher = str(arguments.get("cypher", "")).strip()
        params = arguments.get("params")
        parsed_params = params if isinstance(params, dict) else {}
        _require_readonly_query(cypher)
        columns, rows = _run_query(record, cypher, parsed_params)
        truncated = len(rows) > 50
        return {
            "database_id": database_id,
            "columns": columns,
            "rows": rows[:50],
            "row_count": len(rows),
            "truncated_to": 50 if truncated else len(rows),
            "truncated": truncated,
        }

    if tool_name == "run_query":
        cypher = str(arguments.get("cypher", "")).strip()
        params = arguments.get("params")
        parsed_params = params if isinstance(params, dict) else {}
        if not cypher:
            return {"error": {"code": "CYPHER_REQUIRED", "message": "cypher is required."}}
        columns, rows = _run_query(record, cypher, parsed_params)
        truncated = len(rows) > 50
        return {
            "database_id": database_id,
            "columns": columns,
            "rows": rows[:50],
            "row_count": len(rows),
            "truncated": truncated,
        }

    return {
        "error": {
            "code": "CHAT_UNKNOWN_TOOL",
            "message": f"Unknown chat tool '{tool_name}'.",
        }
    }
