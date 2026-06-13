from __future__ import annotations

import socket
from typing import Any

from fastapi import HTTPException

from ..core.config import (
    LOCAL_DASHBOARD_ARCADEDB_HOST,
    LOCAL_DASHBOARD_ARCADEDB_PORT,
    _LABEL_RE,
    _PROPERTY_RE,
)
from .cypher import _jsonify, _safe_query
from ..core.models import GraphRequest
from .registry import _resolve_arcadedb_host


def _server_port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _database_status(record: dict[str, Any]) -> str:
    # With the single-server architecture a database's "online" status is the
    # reachability of the shared ArcadeDB server — per-database ports don't
    # exist anymore. If the shared port answers, every registered database
    # served by it is online.
    host = _resolve_arcadedb_host(record) or LOCAL_DASHBOARD_ARCADEDB_HOST
    port = LOCAL_DASHBOARD_ARCADEDB_PORT
    return "online" if _server_port_open(host, port) else "offline"


def _database_summary(record: dict[str, Any]) -> dict[str, Any]:
    from ..core.auth import _now_iso

    status = _database_status(record)
    resolved_host = _resolve_arcadedb_host(record)
    return {
        "database_id": record["database_id"],
        "name": record.get("name", record["database_id"]),
        "host": resolved_host,
        "port": LOCAL_DASHBOARD_ARCADEDB_PORT,
        "profile_id": record.get("profile_id", "memory-default"),
        "profile_version": int(record.get("profile_version", 1)),
        "status": status,
        "last_seen_at": _now_iso() if status == "online" else None,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _schema_payload(record: dict[str, Any]) -> dict[str, Any]:
    _, label_rows = _safe_query(
        record,
        """
        MATCH (n)
        UNWIND labels(n) AS label
        RETURN DISTINCT label
        ORDER BY label;
        """,
    )
    labels = [str(row[0]) for row in label_rows if row]

    _, relationship_rows = _safe_query(
        record,
        """
        MATCH ()-[r]->()
        RETURN DISTINCT type(r) AS relationship
        ORDER BY relationship;
        """,
    )
    relationships = [str(row[0]) for row in relationship_rows if row]

    _, node_prop_rows = _safe_query(
        record,
        """
        MATCH (n)
        UNWIND labels(n) AS label
        UNWIND keys(n) AS property
        RETURN DISTINCT label, property
        ORDER BY label, property;
        """,
    )
    node_properties: dict[str, list[str]] = {}
    for row in node_prop_rows:
        if len(row) < 2:
            continue
        label = str(row[0])
        prop = str(row[1])
        node_properties.setdefault(label, []).append(prop)

    _, rel_prop_rows = _safe_query(
        record,
        """
        MATCH ()-[r]->()
        UNWIND keys(r) AS property
        RETURN DISTINCT type(r) AS relationship, property
        ORDER BY relationship, property;
        """,
    )
    relationship_properties: dict[str, list[str]] = {}
    for row in rel_prop_rows:
        if len(row) < 2:
            continue
        relationship = str(row[0])
        prop = str(row[1])
        relationship_properties.setdefault(relationship, []).append(prop)

    return {
        "labels": labels,
        "relationships": relationships,
        "node_properties": node_properties,
        "relationship_properties": relationship_properties,
        "indexes": [],
        "constraints": [],
    }


def _rows_to_graph(
    rows: list[list[Any]], limit_nodes: int
) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    for row in rows:
        if len(row) < 9:
            continue
        src_id = str(row[0])
        src_labels = row[1] if isinstance(row[1], list) else []
        src_props = row[2] if isinstance(row[2], dict) else {}
        rel_id = str(row[3])
        rel_type = str(row[4])
        rel_props = row[5] if isinstance(row[5], dict) else {}
        dst_id = str(row[6])
        dst_labels = row[7] if isinstance(row[7], list) else []
        dst_props = row[8] if isinstance(row[8], dict) else {}

        if src_id not in nodes and len(nodes) < limit_nodes:
            nodes[src_id] = {
                "id": src_id,
                "labels": [_jsonify(v) for v in src_labels],
                "properties": _jsonify(src_props),
            }
        if dst_id not in nodes and len(nodes) < limit_nodes:
            nodes[dst_id] = {
                "id": dst_id,
                "labels": [_jsonify(v) for v in dst_labels],
                "properties": _jsonify(dst_props),
            }

        if src_id in nodes and dst_id in nodes:
            edges[rel_id] = {
                "id": rel_id,
                "type": rel_type,
                "from": src_id,
                "to": dst_id,
                "properties": _jsonify(rel_props),
            }

    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "truncated": len(nodes) >= limit_nodes,
    }


def _build_graph_query(payload: GraphRequest) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {"edge_limit": max(1, payload.limit_nodes * 2)}

    seed_label = (payload.seed_label or "").strip()
    seed_property = (payload.seed_property or "").strip()
    seed_value = payload.seed_value

    if seed_label and not _LABEL_RE.match(seed_label):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_SEED", "message": "Invalid seed label."}},
        )
    if seed_property and not _PROPERTY_RE.match(seed_property):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": "INVALID_SEED", "message": "Invalid seed property."}
            },
        )

    if seed_label or seed_property:
        clauses: list[str] = []
        if seed_label:
            clauses.append(f"'{seed_label}' IN labels(s)")
        if seed_property:
            clauses.append(f"toString(s.{seed_property}) = $seed_value")
            params["seed_value"] = str(seed_value or "")
        where_expr = " AND ".join(clauses) if clauses else "true"
        query = f"""
            MATCH (s)
            WHERE {where_expr}
            WITH s LIMIT 1
            MATCH p=(s)-[*1..{payload.depth}]-(n)
            UNWIND relationships(p) AS r
            WITH DISTINCT r, startNode(r) AS a, endNode(r) AS b
            RETURN
              id(a) AS source_id,
              labels(a) AS source_labels,
              properties(a) AS source_props,
              id(r) AS edge_id,
              type(r) AS edge_type,
              properties(r) AS edge_props,
              id(b) AS target_id,
              labels(b) AS target_labels,
              properties(b) AS target_props
            LIMIT $edge_limit;
        """
        return query, params

    query = """
        MATCH (a)-[r]->(b)
        RETURN
          id(a) AS source_id,
          labels(a) AS source_labels,
          properties(a) AS source_props,
          id(r) AS edge_id,
          type(r) AS edge_type,
          properties(r) AS edge_props,
          id(b) AS target_id,
          labels(b) AS target_labels,
          properties(b) AS target_props
        LIMIT $edge_limit;
    """
    return query, params
