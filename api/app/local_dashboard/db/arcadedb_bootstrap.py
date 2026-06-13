"""ArcadeDB DDL bootstrap for newly-deployed per-database containers.

Phase 2.4: after `_create_arcadedb_container` starts a container, this
module runs once to:
  1. Create the per-database (name matches `database_id`).
  2. Create `Entity`, `Memory`, and `Staging` vertex types.
  3. Create `MENTIONS` and `RELATED_TO` edge types.
  4. Build the LSM_VECTOR HNSW indexes on `Entity.embedding` and
     `Memory.embedding` at the dimension of the configured embedding
     model (Harrier: 640, MiniLM: 384).
  5. Build lookup indexes on the property shapes the store layer relies
     on (`name_norm`, `type`, `created_at`, `aliases_norm`).

Idempotent: every DDL is wrapped in `IF NOT EXISTS`, so re-running after
a container rebuild (Phase 5 migration) is safe.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from ..core.config import (
    DASHBOARD_EMBEDDING_MODEL,
    _load_arcadedb_root_password,
)

_LOG = logging.getLogger(__name__)


# Keep this table in lockstep with `ai/embeddings.py::_MODEL_SPECS`. A mismatch
# between bootstrap dimension and encoder dimension silently breaks every
# vector query — fail loud at `_resolve_dimension` instead.
_EMBEDDING_DIMENSIONS: Dict[str, int] = {
    "harrier-270m": 640,
    "minilm": 384,
}


def _resolve_dimension(model_key: Optional[str] = None) -> int:
    key = (model_key or DASHBOARD_EMBEDDING_MODEL or "").strip().lower()
    if key not in _EMBEDDING_DIMENSIONS:
        raise RuntimeError(
            f"Unknown DASHBOARD_EMBEDDING_MODEL={key!r}; expected one of "
            f"{sorted(_EMBEDDING_DIMENSIONS)}."
        )
    return _EMBEDDING_DIMENSIONS[key]


def _build_ddl(dimension: int) -> List[str]:
    """DDL statements executed in order against a fresh ArcadeDB database.

    Ordering matters: vertex/edge types must exist before their property
    indexes, and property indexes must exist before LSM_VECTOR references
    them. Each statement is idempotent so reruns after container redeploy
    simply no-op.
    """
    return [
        # Vertex types.
        "CREATE VERTEX TYPE Entity IF NOT EXISTS",
        "CREATE VERTEX TYPE Memory IF NOT EXISTS",
        "CREATE VERTEX TYPE Staging IF NOT EXISTS",
        "CREATE VERTEX TYPE Conversation IF NOT EXISTS",
        "CREATE VERTEX TYPE Message IF NOT EXISTS",
        # Edge types.
        #   ABOUT:        Memory -> Entity (what the memory is about)
        #   MENTIONS:     Memory -> Entity (reserved for extraction stage)
        #   RELATED_TO:   Entity -> Entity (carries the authored-schema
        #                 `relationship_types`).
        #   HAS_MESSAGE:  Conversation -> Message
        #   DERIVED_FROM: Memory -> Message
        "CREATE EDGE TYPE ABOUT IF NOT EXISTS",
        "CREATE EDGE TYPE MENTIONS IF NOT EXISTS",
        "CREATE EDGE TYPE RELATED_TO IF NOT EXISTS",
        "CREATE EDGE TYPE HAS_MESSAGE IF NOT EXISTS",
        "CREATE EDGE TYPE DERIVED_FROM IF NOT EXISTS",
        # Entity property shape.
        "CREATE PROPERTY Entity.id IF NOT EXISTS STRING",
        "CREATE PROPERTY Entity.name IF NOT EXISTS STRING",
        "CREATE PROPERTY Entity.name_norm IF NOT EXISTS STRING",
        "CREATE PROPERTY Entity.type IF NOT EXISTS STRING",
        "CREATE PROPERTY Entity.aliases IF NOT EXISTS LIST OF STRING",
        "CREATE PROPERTY Entity.aliases_norm IF NOT EXISTS LIST OF STRING",
        # ARRAY_OF_FLOATS (not LIST OF FLOAT): the strict-typed list rejects
        # JSON DOUBLE values without coercion, breaking every embedding write.
        "CREATE PROPERTY Entity.embedding IF NOT EXISTS ARRAY_OF_FLOATS",
        "CREATE PROPERTY Entity.created_at IF NOT EXISTS DATETIME",
        "CREATE PROPERTY Entity.updated_at IF NOT EXISTS DATETIME",
        # Memory property shape.
        "CREATE PROPERTY Memory.id IF NOT EXISTS STRING",
        "CREATE PROPERTY Memory.text IF NOT EXISTS STRING",
        "CREATE PROPERTY Memory.embedding IF NOT EXISTS ARRAY_OF_FLOATS",
        "CREATE PROPERTY Memory.tags IF NOT EXISTS LIST OF STRING",
        "CREATE PROPERTY Memory.confidence IF NOT EXISTS DOUBLE",
        "CREATE PROPERTY Memory.timestamp IF NOT EXISTS DATETIME",
        "CREATE PROPERTY Memory.conversation_id IF NOT EXISTS STRING",
        "CREATE PROPERTY Memory.created_at IF NOT EXISTS DATETIME",
        # Conversation / Message property shape (re-materialized during
        # reconciler promotion from the staging payload).
        "CREATE PROPERTY Conversation.id IF NOT EXISTS STRING",
        "CREATE PROPERTY Conversation.platform IF NOT EXISTS STRING",
        "CREATE PROPERTY Conversation.started_at IF NOT EXISTS DATETIME",
        "CREATE PROPERTY Conversation.created_at IF NOT EXISTS DATETIME",
        "CREATE PROPERTY Message.id IF NOT EXISTS STRING",
        "CREATE PROPERTY Message.role IF NOT EXISTS STRING",
        "CREATE PROPERTY Message.text IF NOT EXISTS STRING",
        "CREATE PROPERTY Message.timestamp IF NOT EXISTS DATETIME",
        "CREATE PROPERTY Message.created_at IF NOT EXISTS DATETIME",
        # RELATED_TO edge shape.
        "CREATE PROPERTY RELATED_TO.id IF NOT EXISTS STRING",
        "CREATE PROPERTY RELATED_TO.relationship IF NOT EXISTS STRING",
        "CREATE PROPERTY RELATED_TO.confidence IF NOT EXISTS DOUBLE",
        "CREATE PROPERTY RELATED_TO.reason IF NOT EXISTS STRING",
        "CREATE PROPERTY RELATED_TO.created_at IF NOT EXISTS DATETIME",
        "CREATE PROPERTY RELATED_TO.updated_at IF NOT EXISTS DATETIME",
        # Staging property shape (Phase 3 reconciler source).
        "CREATE PROPERTY Staging.id IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.proposed_name IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.proposed_name_norm IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.proposed_type IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.aliases IF NOT EXISTS LIST OF STRING",
        "CREATE PROPERTY Staging.aliases_norm IF NOT EXISTS LIST OF STRING",
        "CREATE PROPERTY Staging.embedding IF NOT EXISTS ARRAY_OF_FLOATS",
        "CREATE PROPERTY Staging.source IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.status IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.created_at IF NOT EXISTS DATETIME",
        "CREATE PROPERTY Staging.updated_at IF NOT EXISTS DATETIME",
        # Staging payload shape carried across for memory / link proposals.
        "CREATE PROPERTY Staging.proposed_text IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.proposed_confidence IF NOT EXISTS DOUBLE",
        "CREATE PROPERTY Staging.proposed_tags IF NOT EXISTS LIST OF STRING",
        "CREATE PROPERTY Staging.proposed_about_entity_ids IF NOT EXISTS LIST OF STRING",
        "CREATE PROPERTY Staging.proposed_conversation_id IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.proposed_conversation_platform IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.proposed_conversation_started_at IF NOT EXISTS DATETIME",
        "CREATE PROPERTY Staging.proposed_message_ids IF NOT EXISTS LIST OF STRING",
        # Embedded list of message dicts (id/role/text/timestamp) carried across
        # for re-materialization during promotion.
        "CREATE PROPERTY Staging.proposed_message_payload IF NOT EXISTS LIST OF EMBEDDED",
        "CREATE PROPERTY Staging.proposed_from_id IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.proposed_to_id IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.proposed_relationship IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.proposed_reason IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.rejection_reason IF NOT EXISTS STRING",
        "CREATE PROPERTY Staging.skip_merge_target_id IF NOT EXISTS STRING",
        # Phase 4.6 audit log.
        "CREATE VERTEX TYPE ReconcilerDecision IF NOT EXISTS",
        "CREATE PROPERTY ReconcilerDecision.id IF NOT EXISTS STRING",
        "CREATE PROPERTY ReconcilerDecision.staging_id IF NOT EXISTS STRING",
        "CREATE PROPERTY ReconcilerDecision.decision IF NOT EXISTS STRING",
        "CREATE PROPERTY ReconcilerDecision.distance IF NOT EXISTS DOUBLE",
        "CREATE PROPERTY ReconcilerDecision.target_id IF NOT EXISTS STRING",
        "CREATE PROPERTY ReconcilerDecision.reason IF NOT EXISTS STRING",
        "CREATE PROPERTY ReconcilerDecision.payload IF NOT EXISTS STRING",
        "CREATE PROPERTY ReconcilerDecision.decided_at IF NOT EXISTS DATETIME",
        "CREATE PROPERTY ReconcilerDecision.reversed IF NOT EXISTS BOOLEAN",
        "CREATE INDEX IF NOT EXISTS ON ReconcilerDecision (id) UNIQUE",
        "CREATE INDEX IF NOT EXISTS ON ReconcilerDecision (decided_at) NOTUNIQUE",
        "CREATE INDEX IF NOT EXISTS ON ReconcilerDecision (staging_id) NOTUNIQUE",
        # Identity + lookup indexes.
        "CREATE INDEX IF NOT EXISTS ON Entity (id) UNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Entity (name_norm, type) NOTUNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Entity (type) NOTUNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Memory (id) UNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Memory (created_at) NOTUNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Memory (timestamp) NOTUNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Conversation (id) UNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Message (id) UNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Staging (id) UNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Staging (status) NOTUNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Staging (created_at) NOTUNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Staging (source) NOTUNIQUE",
        # LSM_VECTOR HNSW indexes — cosine similarity at model dimension.
        # Parameters `m` and `ef` match ArcadeDB defaults at the time of
        # writing; tune with the spike bench results (§0.4).
        #
        # ArcadeDB's SQL parser rejects `CREATE INDEX IF NOT EXISTS <name> ON …`
        # with a quoted index name here; the unnamed form is accepted and
        # auto-names the index `Type[property]` — the same name the store
        # layer references in `vectorNeighbors('Entity[embedding]', …)`.
        f"CREATE INDEX IF NOT EXISTS ON Entity (embedding) LSM_VECTOR "
        f"METADATA {{\"dimensions\": {dimension}, \"similarity\": \"COSINE\", \"m\": 16, \"ef\": 128}}",
        f"CREATE INDEX IF NOT EXISTS ON Memory (embedding) LSM_VECTOR "
        f"METADATA {{\"dimensions\": {dimension}, \"similarity\": \"COSINE\", \"m\": 16, \"ef\": 128}}",
        f"CREATE INDEX IF NOT EXISTS ON Staging (embedding) LSM_VECTOR "
        f"METADATA {{\"dimensions\": {dimension}, \"similarity\": \"COSINE\", \"m\": 16, \"ef\": 128}}",
    ]


def _arcadedb_url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def _auth(username: Optional[str], password: Optional[str]) -> tuple:
    user = (username or "root").strip() or "root"
    pwd = (password or "").strip()
    if not pwd:
        pwd = _load_arcadedb_root_password()
    return (user, pwd)


def _post_command(
    *,
    host: str,
    port: int,
    database: Optional[str],
    command: str,
    auth: tuple,
    language: str = "sql",
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """POST a single DDL/DML statement to ArcadeDB's REST API.

    `database=None` targets the server-level endpoint (used for CREATE
    DATABASE). Otherwise the command runs against the named database.
    """
    if database is None:
        path = "/api/v1/server"
    else:
        path = f"/api/v1/command/{database}"
    payload = {"language": language, "command": command}
    try:
        with httpx.Client(timeout=timeout, auth=auth) as client:
            response = client.post(_arcadedb_url(host, port, path), json=payload)
    except httpx.RequestError as exc:
        raise RuntimeError(
            f"ArcadeDB bootstrap could not reach {host}:{port}: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise RuntimeError(
            f"ArcadeDB bootstrap command failed (HTTP {response.status_code}): "
            f"{response.text}"
        )
    try:
        return response.json()
    except ValueError:
        return {}


def bootstrap_database(
    *,
    host: str,
    port: int,
    database_id: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    embedding_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply DDL against an already-created ArcadeDB database.

    Under the single-server architecture, database creation is the caller's
    responsibility (see `arcadedb_server.create_database`). This function
    assumes the database already exists and only installs the vertex/edge
    types and indexes.

    Returns a small summary dict for the caller to persist alongside the
    registry record (`embedding_dimension` in particular is useful for
    the cloud-side profile_hash).
    """
    auth = _auth(username, password)
    dimension = _resolve_dimension(embedding_model)

    # DDL (vertex/edge types, indexes).
    for stmt in _build_ddl(dimension):
        _post_command(
            host=host,
            port=port,
            database=database_id,
            command=stmt,
            auth=auth,
            language="sqlscript",
        )

    _LOG.info(
        "[arcadedb-bootstrap] database=%s dimension=%d model=%s",
        database_id,
        dimension,
        embedding_model or DASHBOARD_EMBEDDING_MODEL,
    )
    return {
        "database_id": database_id,
        "embedding_dimension": dimension,
        "embedding_model": embedding_model or DASHBOARD_EMBEDDING_MODEL,
    }
