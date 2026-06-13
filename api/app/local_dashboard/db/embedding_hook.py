"""Rewrite `{{embed:<param>}}` placeholders in incoming Cypher into a
parameter carrying the encoded vector.

The cloud → dashboard query proxy (`POST /api/sync/query`) accepts
arbitrary Cypher. ArcadeDB has no in-database embedding procedure, so
writes that need an embedding carry a placeholder marker like:

    CREATE (s:Staging {embedding: {{embed:text}}, ...})

where `parameters["text"]` holds the sentence to embed. The hook:

1. Finds each `{{embed:<name>}}` occurrence in the Cypher.
2. Reads `parameters[name]` (string; must be present).
3. Calls `EmbeddingService.embed(...)` to produce a vector.
4. Rewrites the Cypher to use a new parameter `name__embedding` and
   populates that parameter slot with the vector.

Calls without a placeholder are a byte-identical pass-through.

The explicit placeholder is chosen over an implicit "embed any string
param" rule so callers can pass entity IDs, labels, and filter strings
without accidental embedding.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from ..ai.embeddings import get_embedding_service


_EMBED_PLACEHOLDER_RE = re.compile(r"\{\{\s*embed\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _derived_param_name(param_name: str) -> str:
    return f"{param_name}__embedding"


def rewrite_embed_placeholders(
    cypher: str, parameters: dict[str, Any], language: str = "cypher"
) -> tuple[str, dict[str, Any]]:
    """Return `(rewritten_cypher, rewritten_parameters)`.

    Raises `HTTPException(400, INVALID_QUERY)` if the placeholder references
    a parameter that is missing or not a string.

    `language` selects the parameter-binding sigil for the substitution:
    Cypher uses `$name`, ArcadeDB SQL uses `:name`.
    """
    param_sigil = ":" if language == "sql" else "$"
    if "{{embed:" not in cypher and "{{ embed" not in cypher:
        # Fast path — avoid importing the embedder when no placeholders exist.
        return cypher, parameters

    matches = list(_EMBED_PLACEHOLDER_RE.finditer(cypher))
    if not matches:
        return cypher, parameters

    rewritten_params = dict(parameters or {})
    new_cypher = cypher

    # Group by param_name so we embed each text at most once per request.
    param_names = []
    seen = set()
    for match in matches:
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        param_names.append(name)

    # Resolve every referenced parameter up front so we fail fast on missing
    # inputs before spending inference time.
    texts_to_embed: list[tuple[str, str]] = []
    for name in param_names:
        if name not in rewritten_params:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "INVALID_QUERY",
                        "message": (
                            f"Cypher references {{{{embed:{name}}}}} but the "
                            f"'{name}' parameter is missing from the request."
                        ),
                    }
                },
            )
        raw_value = rewritten_params[name]
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "INVALID_QUERY",
                        "message": (
                            f"Parameter '{name}' must be a non-empty string to "
                            f"be embedded via {{{{embed:{name}}}}}."
                        ),
                    }
                },
            )
        texts_to_embed.append((name, raw_value))

    embedder = get_embedding_service()
    vectors = embedder.embed_batch([text for _, text in texts_to_embed])

    for (name, _), vector in zip(texts_to_embed, vectors):
        derived = _derived_param_name(name)
        # Use a unique parameter name; collisions with user-supplied inputs
        # would corrupt the query, so we reject instead of silently overwriting.
        if derived in rewritten_params and rewritten_params[derived] != vector:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "INVALID_QUERY",
                        "message": (
                            f"Parameter '{derived}' collides with the derived "
                            f"embedding name for '{name}'. Rename the caller's "
                            "parameter to avoid the clash."
                        ),
                    }
                },
            )
        rewritten_params[derived] = vector
        # Replace each `{{embed:name}}` with the language-appropriate
        # parameter reference (`$name__embedding` for Cypher,
        # `:name__embedding` for SQL).
        placeholder_pattern = re.compile(
            r"\{\{\s*embed\s*:\s*" + re.escape(name) + r"\s*\}\}"
        )
        new_cypher = placeholder_pattern.sub(param_sigil + derived, new_cypher)

    return new_cypher, rewritten_params
