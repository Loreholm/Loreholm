"""Cloud-side client for the ArcadeDB-backed graph store.

Every cloud → user-graph call flows through the local dashboard proxy
endpoint `POST /api/sync/query`. Writes land in a staging vertex that
the dashboard's reconciler promotes to `Entity` / `Memory` asynchronously.

ArcadeDB-specific Cypher shapes:

  - No `CALL embeddings.text()`; writes use the `{{embed:...}}` placeholder
    which the dashboard rewrites into a concrete vector parameter.
  - Vector search uses `vectorNeighbors('Class[prop]', ...)` via SQL.

Errors subclass `GraphStoreUnavailableError` / `GraphStorePolicyDeniedError`
from `services/graph_store_errors.py`.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4

import httpx

from app.services.graph_store_errors import (
    GraphStorePolicyDeniedError,
    GraphStoreUnavailableError,
)
from app.services.utils import (
    canonicalize_entity_type,
    dedupe_preserve_order,
    normalize,
    now_utc,
    parse_iso,
    to_iso,
)


LOCAL_SYNC_PORT = int(os.getenv("LOCAL_SYNC_PORT", "8081"))
LOCAL_SYNC_TIMEOUT_SECONDS = float(os.getenv("LOCAL_SYNC_TIMEOUT_SECONDS", "30.0"))


class ArcadeDBUnavailableError(GraphStoreUnavailableError):
    """Transport-level failure on the ArcadeDB path."""


class PolicyDeniedError(GraphStorePolicyDeniedError):
    """Local dashboard proxy refused the query (read-only / rate limit)."""


def _jsonable_params(params: Dict[str, object]) -> Dict[str, Any]:
    """Coerce query parameters to JSON-safe values for the HTTP transport."""
    def _convert(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, dict):
            return {str(k): _convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_convert(v) for v in value]
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value.hex()
        return str(value)

    return {str(k): _convert(v) for k, v in (params or {}).items()}


def _deserialize_proxy_value(value: Any) -> Any:
    """Adapt proxy response rows to plain-dict / list / scalar shapes.

    The proxy envelope uses a discriminated-union with `_type` tags for
    node / relationship / path, so the cloud-side decoder is independent
    of the underlying graph engine.
    """
    if isinstance(value, dict):
        tag = value.get("_type")
        if tag in {"node", "relationship"}:
            return value.get("properties") or {}
        if tag == "path":
            return {
                "nodes": [_deserialize_proxy_value(n) for n in (value.get("nodes") or [])],
                "relationships": [
                    _deserialize_proxy_value(r)
                    for r in (value.get("relationships") or [])
                ],
            }
        return {k: _deserialize_proxy_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deserialize_proxy_value(v) for v in value]
    return value


_SEARCH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to",
    "was", "what", "when", "where", "who", "why", "with",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(parsed, minimum)


@dataclass
class ArcadeDBConfig:
    """Routing info for the cloud → dashboard query proxy.

    No ArcadeDB credentials live cloud-side — the dashboard owns the
    root password and the per-database credentials. The cloud only
    knows which database target to ask the proxy to route to.
    """

    host: str
    database_id: Optional[str] = None
    proxy_port: int = field(default_factory=lambda: LOCAL_SYNC_PORT)
    api_key_id: Optional[str] = None
    sync_token: Optional[str] = None


class ArcadeDBStore:
    def __init__(self, config: ArcadeDBConfig) -> None:
        self.config = config
        self._last_profile_hash: Optional[str] = None

    @property
    def last_profile_hash(self) -> Optional[str]:
        return self._last_profile_hash

    def _proxy_url(self) -> str:
        return f"http://{self.config.host}:{self.config.proxy_port}/api/sync/query"

    def _token(self) -> str:
        token = self.config.sync_token
        if not token:
            raise ArcadeDBUnavailableError(
                "ArcadeDBConfig.sync_token is required — derive it with "
                "`derive_user_sync_token(user_id)` in the caller before "
                "constructing the store."
            )
        return token

    def _proxy_query(
        self,
        query: str,
        params: Dict[str, object],
        *,
        read_only: bool = False,
        language: str = "cypher",
    ) -> List[List[Any]]:
        if not self.config.database_id:
            raise ArcadeDBUnavailableError(
                "ArcadeDBStore.config.database_id is required for proxy queries."
            )
        payload = {
            "database_id": self.config.database_id,
            "cypher": query,
            "parameters": _jsonable_params(params),
            "read_only": bool(read_only),
            "api_key_id": self.config.api_key_id,
            "language": language,
        }
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(LOCAL_SYNC_TIMEOUT_SECONDS, connect=3.0)
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(self._proxy_url(), headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise ArcadeDBUnavailableError(
                f"Could not reach local dashboard proxy at {self.config.host}:{self.config.proxy_port}: {exc}"
            ) from exc

        if response.status_code in (401, 403):
            raise ArcadeDBUnavailableError(
                "Local dashboard proxy rejected sync credentials."
            )
        if response.status_code == 404:
            raise ArcadeDBUnavailableError(
                f"Local dashboard proxy reports database '{self.config.database_id}' not found."
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ArcadeDBUnavailableError(
                f"Local dashboard proxy returned invalid JSON (HTTP {response.status_code})."
            ) from exc

        if response.status_code >= 500:
            raise ArcadeDBUnavailableError(
                f"Local dashboard proxy failed with HTTP {response.status_code}: {body}"
            )

        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code", "")).upper()
            if code == "POLICY_DENIED":
                raise PolicyDeniedError(
                    rule=str(error.get("rule", "unknown")),
                    reason=str(error.get("reason", "Policy denied.")),
                )
            if code == "QUERY_FAILED":
                raise RuntimeError(str(error.get("message", "Query failed.")))
            raise ArcadeDBUnavailableError(
                f"Local dashboard proxy error: {error.get('message') or error.get('code')}"
            )

        if response.status_code >= 400:
            raise RuntimeError(f"Proxy query failed (HTTP {response.status_code}): {body}")

        profile_hash = body.get("profile_hash") if isinstance(body, dict) else None
        if profile_hash:
            self._last_profile_hash = str(profile_hash)

        raw_rows = body.get("rows") or []
        rows: List[List[Any]] = []
        for row in raw_rows:
            if isinstance(row, list):
                rows.append([_deserialize_proxy_value(v) for v in row])
            else:
                rows.append([_deserialize_proxy_value(row)])
        return rows

    def _fetchone(self, query: str, params: Dict[str, object]) -> Optional[list]:
        rows = self._proxy_query(query, params)
        return rows[0] if rows else None

    def _fetchall(
        self, query: str, params: Dict[str, object], *, language: str = "cypher"
    ) -> List[list]:
        return self._proxy_query(query, params, language=language)

    def _execute(self, query: str, params: Dict[str, object]) -> None:
        self._proxy_query(query, params)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def upsert_entities(
        self,
        inputs: List[Dict[str, object]],
        allowed_entity_types: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, object]:
        """Stage entity proposals for reconciler-driven deduplication.

        Phase 3 form: every LLM-proposed entity lands in a `Staging`
        vertex (`status='pending'`) with its embedding resolved by the
        dashboard hook (`{{embed:proposed_name}}`). A background
        reconciler on the dashboard (Phase 4) walks the queue and
        decides merge / needs_review / promote per the cosine-distance
        thresholds.

        The response envelope is `{staged: [...], message: "..."}` so the
        MCP tool surface explicitly tells the LLM proposals are not yet
        queryable.
        """
        if allowed_entity_types is None:
            allowed_entity_types = {}
        staged: List[Dict[str, object]] = []
        now = to_iso(now_utc())
        for item in inputs:
            name = str(item["name"])
            # Canonicalize against the authored schema up front: a bad
            # type rejects here rather than surfacing as a reconciler
            # `rejected` decision the LLM has to poll for.
            entity_type = canonicalize_entity_type(
                str(item["type"]), allowed_entity_types
            )
            aliases = dedupe_preserve_order(
                [str(a) for a in item.get("aliases", [])]
            )
            aliases_norm = dedupe_preserve_order(
                [normalize(a) for a in aliases if a.strip()]
            )
            merge_into_raw = item.get("merge_into")
            requested_merge_target_id = (
                str(merge_into_raw).strip() if merge_into_raw else None
            ) or None
            had_prior_search = bool(item.get("had_prior_search", False))
            staging_id = uuid4().hex
            # Param names are deliberately camelCase rather than the
            # Cypher property names. ArcadeDB 26.3.1's Cypher → Groovy
            # compiler mangles parameter pairs of the shape `$X` / `$X_norm`
            # when the same statement also writes the LSM_VECTOR `embedding`
            # property — emitting a synthetic `<random4>_norm` Groovy
            # symbol that's never bound (see the "Bug C"
            # entry in CHANGES.md, 2026-05). Renaming the
            # offending pairs (`$name`/`$name_norm`, `$aliases`/`$aliases_norm`)
            # is sufficient; the on-disk property names stay snake_case so
            # the reconciler reads are unaffected.
            row = self._fetchone(
                """
                CREATE (s:Staging {
                  id: $staging_id,
                  proposed_name: $proposedName,
                  proposed_name_norm: $proposedNameNorm,
                  proposed_type: $proposedType,
                  aliases: $proposedAliases,
                  aliases_norm: $proposedAliasesNorm,
                  embedding: {{embed:proposedName}},
                  source: 'upsert_entities',
                  status: 'pending',
                  requested_merge_target_id: $requested_merge_target_id,
                  had_prior_search: $had_prior_search,
                  created_at: $now,
                  updated_at: $now
                })
                RETURN s.id AS staging_id;
                """,
                {
                    "staging_id": staging_id,
                    "proposedName": name,
                    "proposedNameNorm": normalize(name),
                    "proposedType": entity_type,
                    "proposedAliases": aliases,
                    "proposedAliasesNorm": aliases_norm,
                    "requested_merge_target_id": requested_merge_target_id,
                    "had_prior_search": had_prior_search,
                    "now": now,
                },
            )
            staged.append(
                {
                    "staging_id": str(row[0]) if row else staging_id,
                    "proposed_name": name,
                    "proposed_type": entity_type,
                    "aliases": aliases,
                    "status": "pending",
                    "requested_merge_target_id": requested_merge_target_id,
                }
            )
        count = len(staged)
        metrics = self._staging_metrics()
        return {
            "staged": staged,
            "message": (
                f"Staged {count} entity proposal{'s' if count != 1 else ''} "
                "for deduplication. The reconciler will merge close duplicates "
                "into existing entities or promote novel entities within a few "
                "seconds. Query results will reflect promoted entities only; "
                "poll `loreholm_search` or `loreholm_context` to confirm."
            ),
            "reconciler_lag_seconds": metrics["reconciler_lag_seconds"],
            "needs_review_count": metrics["needs_review_count"],
        }

    def _staging_metrics(self) -> Dict[str, Any]:
        """Probe the Staging queue for reconciler-observability metrics.

        Returns `{reconciler_lag_seconds, needs_review_count}`. A probe
        failure is swallowed (both values become None) — the LLM-facing
        MCP response must not hard-fail because an observability side-query
        couldn't complete.
        """
        try:
            rows = self._proxy_query(
                """
                MATCH (s:Staging)
                WHERE s.status IN ['pending', 'needs_review']
                RETURN s.status AS status,
                       count(s) AS count,
                       min(s.created_at) AS oldest;
                """,
                {},
                read_only=True,
            )
        except Exception:
            return {"reconciler_lag_seconds": None, "needs_review_count": None}

        oldest_pending: Optional[str] = None
        needs_review = 0
        for row in rows or []:
            values = row if isinstance(row, list) else [row]
            payload = values[0] if len(values) == 1 and isinstance(values[0], dict) else None
            if payload is None:
                status = values[0] if len(values) > 0 else None
                count_value = values[1] if len(values) > 1 else 0
                oldest_value = values[2] if len(values) > 2 else None
            else:
                status = payload.get("status")
                count_value = payload.get("count")
                oldest_value = payload.get("oldest")
            status_text = str(status or "").strip()
            try:
                count_int = int(count_value or 0)
            except (TypeError, ValueError):
                count_int = 0
            if status_text == "pending":
                oldest_pending = oldest_value if oldest_value else oldest_pending
            elif status_text == "needs_review":
                needs_review = count_int

        lag: Optional[float] = None
        if oldest_pending:
            try:
                dt = parse_iso(str(oldest_pending).replace("Z", "+00:00"))
            except ValueError:
                dt = None
            if dt is not None:
                lag = max(0.0, (now_utc() - dt).total_seconds())
        return {"reconciler_lag_seconds": lag, "needs_review_count": needs_review}

    def delete_entities(self, entity_ids: List[str]) -> Dict[str, object]:
        ids = dedupe_preserve_order(
            [str(entity_id) for entity_id in entity_ids if str(entity_id).strip()]
        )
        if not ids:
            return {
                "deleted_entity_ids": [],
                "not_found_entity_ids": [],
                "deleted_count": 0,
            }
        found_rows = self._fetchall(
            """
            MATCH (e:Entity)
            WHERE e.id IN $entity_ids
            RETURN e.id AS entity_id;
            """,
            {"entity_ids": ids},
        )
        found_ids = {row[0] for row in found_rows}
        deleted_entity_ids = [entity_id for entity_id in ids if entity_id in found_ids]
        not_found_entity_ids = [entity_id for entity_id in ids if entity_id not in found_ids]
        if deleted_entity_ids:
            self._execute(
                """
                MATCH (e:Entity)
                WHERE e.id IN $entity_ids
                DETACH DELETE e;
                """,
                {"entity_ids": deleted_entity_ids},
            )
        return {
            "deleted_entity_ids": deleted_entity_ids,
            "not_found_entity_ids": not_found_entity_ids,
            "deleted_count": len(deleted_entity_ids),
        }

    def delete_memories(self, memory_ids: List[str]) -> Dict[str, object]:
        ids = dedupe_preserve_order(
            [str(memory_id) for memory_id in memory_ids if str(memory_id).strip()]
        )
        if not ids:
            return {
                "deleted_memory_ids": [],
                "not_found_memory_ids": [],
                "deleted_count": 0,
            }
        found_rows = self._fetchall(
            """
            MATCH (m:Memory)
            WHERE m.id IN $memory_ids
            RETURN m.id AS memory_id;
            """,
            {"memory_ids": ids},
        )
        found_ids = {row[0] for row in found_rows}
        deleted_memory_ids = [memory_id for memory_id in ids if memory_id in found_ids]
        not_found_memory_ids = [
            memory_id for memory_id in ids if memory_id not in found_ids
        ]
        if deleted_memory_ids:
            self._execute(
                """
                MATCH (m:Memory)
                WHERE m.id IN $memory_ids
                DETACH DELETE m;
                """,
                {"memory_ids": deleted_memory_ids},
            )
        return {
            "deleted_memory_ids": deleted_memory_ids,
            "not_found_memory_ids": not_found_memory_ids,
            "deleted_count": len(deleted_memory_ids),
        }

    def write_memory(self, payload: Dict[str, object]) -> Dict[str, object]:
        """Stage a memory proposal for reconciler-driven admission.

        Phase 3: Memory write lands in a `Staging` vertex carrying the
        full payload (text, tags, confidence, about_entity_ids,
        conversation/message metadata) and its embedding. The reconciler
        decides whether to promote it into a `Memory` node — and, if so,
        how to resolve `about_entity_ids` that may themselves still be
        staging IDs (the LLM typically calls `upsert_entities` in the
        same turn).

        Conversation/Message vertices are intentionally not created here
        — they become part of the staging payload so the reconciler can
        build them during promotion, keeping the write path single-
        writer for the Staging vertex only.
        """
        now = to_iso(now_utc())
        about_entity_ids = dedupe_preserve_order(
            [str(eid) for eid in payload.get("about_entity_ids", [])]
        )
        # Do not assert existence of `about_entity_ids` here: under the
        # staging model the caller often proposes entities in the same
        # MCP round, so the `entity_id` may refer to a staging_id the
        # reconciler has not yet promoted. Validation happens during
        # promotion (Phase 4.5).
        tags = dedupe_preserve_order([str(tag) for tag in payload.get("tags", [])])
        source_ref = payload["source_ref"]
        conversation_id = str(source_ref["conversation_id"])
        platform_value = str(source_ref.get("platform", "")).strip()
        conversation_platform = platform_value if platform_value else None
        conversation_started_at = parse_iso(source_ref.get("started_at"))
        conversation_started_at_iso = (
            to_iso(conversation_started_at) if conversation_started_at else None
        )
        source_messages = source_ref.get("messages", []) or []
        staged_messages: List[Dict[str, Optional[str]]] = []
        for idx, raw_message in enumerate(source_messages):
            if not isinstance(raw_message, dict):
                raise ValueError(f"source_ref.messages[{idx}] must be an object")
            raw_id = str(raw_message.get("id", "")).strip()
            if not raw_id:
                raise ValueError(
                    f"source_ref.messages[{idx}].id is required when message metadata is provided"
                )
            role_value = str(raw_message.get("role", "")).strip() or None
            text_value = str(raw_message.get("text", "")).strip() or None
            msg_timestamp = parse_iso(raw_message.get("timestamp"))
            staged_messages.append(
                {
                    "id": raw_id,
                    "role": role_value,
                    "text": text_value,
                    "timestamp": to_iso(msg_timestamp) if msg_timestamp else None,
                }
            )
        message_ids = dedupe_preserve_order(
            [str(mid) for mid in source_ref.get("message_ids", [])]
            + [entry["id"] for entry in staged_messages],
        )
        staging_id = uuid4().hex
        memory_text = str(payload["text"])

        row = self._fetchone(
            """
            CREATE (s:Staging {
              id: $staging_id,
              proposed_text: $text,
              embedding: {{embed:text}},
              proposed_confidence: $confidence,
              proposed_tags: $tags,
              proposed_about_entity_ids: $about_entity_ids,
              proposed_conversation_id: $conversation_id,
              proposed_conversation_platform: $platform,
              proposed_conversation_started_at: $started_at,
              proposed_message_ids: $message_ids,
              proposed_message_payload: $staged_messages,
              source: 'write_memory',
              status: 'pending',
              created_at: $now,
              updated_at: $now
            })
            RETURN s.id AS staging_id;
            """,
            {
                "staging_id": staging_id,
                "text": memory_text,
                "confidence": float(payload["confidence"]),
                "tags": tags,
                "about_entity_ids": about_entity_ids,
                "conversation_id": conversation_id,
                "platform": conversation_platform,
                "started_at": conversation_started_at_iso,
                "message_ids": message_ids,
                # staged_messages is a list of small dicts — ArcadeDB stores it
                # as a LIST<EMBEDDED> which is fine for a write-once payload.
                "staged_messages": staged_messages,
                "now": now,
            },
        )
        return {
            "staging_id": str(row[0]) if row else staging_id,
            "status": "pending",
            "linked_entity_proposals": about_entity_ids,
            "message": (
                "Memory proposal staged. The reconciler will promote it after "
                "resolving its about_entity_ids against the current graph "
                "(merging close entity duplicates, promoting novel ones)."
            ),
        }

    def link_entities(self, payload: Dict[str, object]) -> Dict[str, object]:
        """Stage an edge proposal.

        Endpoints may be committed `Entity.id`s or `Staging.id`s — the
        reconciler resolves both during promotion (Phase 4.5), drops
        self-loops that collapsed under merge, and dedupes edges that
        now exist between the same committed pair.
        """
        now = to_iso(now_utc())
        from_id = str(payload["from_entity_id"])
        to_id = str(payload["to_entity_id"])
        staging_id = uuid4().hex
        row = self._fetchone(
            """
            CREATE (s:Staging {
              id: $staging_id,
              proposed_from_id: $from_id,
              proposed_to_id: $to_id,
              proposed_relationship: $relationship,
              proposed_confidence: $confidence,
              proposed_reason: $reason,
              source: 'link_entities',
              status: 'pending',
              created_at: $now,
              updated_at: $now
            })
            RETURN s.id AS staging_id;
            """,
            {
                "staging_id": staging_id,
                "from_id": from_id,
                "to_id": to_id,
                "relationship": str(payload["relationship"]),
                "confidence": float(payload["confidence"]),
                "reason": str(payload["reason"]),
                "now": now,
            },
        )
        return {
            "staging_id": str(row[0]) if row else staging_id,
            "status": "pending",
            "from_entity_id": from_id,
            "to_entity_id": to_id,
            "message": (
                "Edge proposal staged. The reconciler will resolve both "
                "endpoints (promoted entity or merge target) and create or "
                "dedupe the edge accordingly."
            ),
        }

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int,
        entity_types: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        since: Optional[str] = None,
        include_meta: bool = False,
    ) -> object:
        """Hybrid vector + lexical search.

        Vector path uses ArcadeDB's `vectorNeighbors('Memory[embedding]',
        $vec, $k)` SQL function. The embedding for the query text is
        generated by the dashboard hook from the `{{embed:query_text}}`
        placeholder before the query is dispatched to ArcadeDB.

        Lexical and entity-hit fallback stages are graph-structural
        Cypher patterns, not engine-specific.
        """
        since_value = since
        candidate_limit = max(top_k * 5, 40)
        lexical_enabled = _env_bool("SEARCH_ENABLE_LEXICAL_FALLBACK", True)
        hybrid_enabled = _env_bool("SEARCH_ENABLE_HYBRID", False)
        max_terms = _env_int("SEARCH_LEXICAL_MAX_TERMS", 12, minimum=1)
        min_term_len = _env_int("SEARCH_LEXICAL_MIN_TERM_LEN", 3, minimum=1)

        tokens = re.findall(r"[a-z0-9_]+", normalize(query))
        filtered_terms = [
            token
            for token in tokens
            if len(token) >= min_term_len and token not in _SEARCH_STOPWORDS
        ]
        query_terms = dedupe_preserve_order(filtered_terms)[:max_terms]
        if not query_terms and tokens:
            query_terms = dedupe_preserve_order(tokens)[:max_terms]
        query_phrases = dedupe_preserve_order(
            [
                " ".join(tokens[idx : idx + 2])
                for idx in range(0, max(len(tokens) - 1, 0))
                if all(len(token) >= min_term_len for token in tokens[idx : idx + 2])
                and all(
                    token not in _SEARCH_STOPWORDS
                    for token in tokens[idx : idx + 2]
                )
            ]
        )[: max(max_terms // 2, 1)]

        merged: Dict[str, Dict[str, object]] = {}
        lexical_ids = set()
        vector_candidates = 0
        lexical_candidates = 0
        vector_ok = True
        fallback_reason: Optional[str] = None

        def _upsert_item(item: Dict[str, object], score: float) -> None:
            memory_id = str(item["memory_id"])
            existing = merged.get(memory_id)
            if not existing:
                item["rank_score"] = score
                merged[memory_id] = item
                return
            existing_score = float(existing.get("rank_score", float("-inf")))
            if score > existing_score:
                item["rank_score"] = score
                merged[memory_id] = item

        try:
            # vectorNeighbors only exists as a SQL function in ArcadeDB 26.x;
            # the Cypher procedure form (`CALL vectorNeighbors(...) YIELD ...`)
            # was removed. Issue the vector lookup via SQL, then post-process
            # the entity refs in Python (SQL traversal across labels is
            # awkward to express here).
            vector_rows = self._fetchall(
                """
                SELECT
                  id AS memory_id,
                  text,
                  confidence,
                  timestamp,
                  conversation_id,
                  out('ABOUT').asList() AS about_vertices,
                  distance,
                  (1.0 - distance) AS similarity
                FROM (SELECT expand(vectorNeighbors('Memory[embedding]', {{embed:query_text}}, :limit)))
                WHERE (:since IS NULL OR timestamp >= :since)
                ORDER BY similarity DESC, timestamp DESC
                LIMIT :limit
                """,
                {
                    "query_text": query,
                    "limit": candidate_limit,
                    "since": since_value,
                },
                language="sql",
            )
            vector_candidates = len(vector_rows)
            for row in vector_rows:
                if not row or not row[0]:
                    continue
                # Row layout from SQL projection: [memory_id, text, confidence,
                # timestamp, conversation_id, about_vertices, distance, similarity].
                about_vertices = row[5] or []
                entity_refs = []
                for v in about_vertices:
                    if not isinstance(v, dict):
                        continue
                    entity_refs.append({
                        "entity_id": v.get("id"),
                        "name": v.get("name"),
                        "type": v.get("type"),
                    })
                similarity = float(row[7]) if row[7] is not None else 0.0
                _upsert_item(
                    {
                        "memory_id": row[0],
                        "text": row[1],
                        "confidence": row[2],
                        "timestamp": row[3],
                        "entity_refs": entity_refs,
                        "source_ref": {"conversation_id": row[4]},
                    },
                    1000.0 + similarity * 100.0,
                )
        except Exception as exc:
            error_text = str(exc).lower()
            vector_failure_markers = (
                "vectorneighbors",
                "vector_search",
                "embedding",
                "index",
                "does not exist",
                "no such",
            )
            if not any(marker in error_text for marker in vector_failure_markers):
                raise
            vector_ok = False
            fallback_reason = "vector_unavailable"

        run_lexical = (
            lexical_enabled
            and bool(query_terms)
            and (not vector_ok or vector_candidates == 0 or hybrid_enabled)
        )
        if run_lexical:
            memory_rows = self._fetchall(
                """
                MATCH (m:Memory)
                WHERE ($since IS NULL OR m.timestamp >= $since)
                WITH m, toLower(m.text) AS text_norm
                WITH m, text_norm,
                     size([term IN $terms WHERE text_norm CONTAINS term]) AS term_hits,
                     size([phrase IN $phrases WHERE text_norm CONTAINS phrase]) AS phrase_hits
                WHERE term_hits > 0 OR phrase_hits > 0
                OPTIONAL MATCH (m)-[:ABOUT]->(e:Entity)
                WITH m, term_hits, phrase_hits, collect({
                    entity_id: e.id,
                    name: e.name,
                    type: e.type
                }) AS entity_refs
                RETURN
                    m.id AS memory_id,
                    m.text AS text,
                    m.confidence AS confidence,
                    m.timestamp AS timestamp,
                    entity_refs AS entity_refs,
                    m.conversation_id AS conversation_id,
                    term_hits AS term_hits,
                    phrase_hits AS phrase_hits
                ORDER BY phrase_hits DESC, term_hits DESC, m.timestamp DESC
                LIMIT $limit;
                """,
                {
                    "terms": query_terms,
                    "phrases": query_phrases,
                    "limit": candidate_limit,
                    "since": since_value,
                },
            )
            for row in memory_rows:
                if not row or not row[0]:
                    continue
                term_hits = int(row[6] or 0)
                phrase_hits = int(row[7] or 0)
                lexical_ids.add(row[0])
                score = (term_hits * 4.0) + (phrase_hits * 8.0) + float(row[2] or 0.0)
                _upsert_item(
                    {
                        "memory_id": row[0],
                        "text": row[1],
                        "confidence": row[2],
                        "timestamp": row[3],
                        "entity_refs": row[4] or [],
                        "source_ref": {"conversation_id": row[5]},
                    },
                    score,
                )
            lexical_candidates = len(lexical_ids)

        if vector_ok and vector_candidates == 0 and fallback_reason is None:
            if lexical_candidates > 0:
                fallback_reason = "vector_no_results"
            elif lexical_enabled and not query_terms:
                fallback_reason = "query_terms_empty"
            elif not lexical_enabled:
                fallback_reason = "lexical_fallback_disabled"

        retrieval_mode = "vector"
        if not vector_ok:
            retrieval_mode = "lexical_fallback"
        elif vector_candidates == 0 and lexical_candidates > 0:
            retrieval_mode = "hybrid" if hybrid_enabled else "lexical_fallback"
        elif vector_candidates > 0 and hybrid_enabled and lexical_candidates > 0:
            retrieval_mode = "hybrid"

        items = list(merged.values())
        if entity_types:
            type_set = {normalize(str(entity_type)) for entity_type in entity_types}
            items = [
                item
                for item in items
                if any(
                    normalize(str(ref["type"])) in type_set
                    for ref in item["entity_refs"]
                )
            ]
        if tags:
            tag_set = set(tags)
            if not items:
                return []
            tagged = self._fetchall(
                """
                MATCH (m:Memory)
                WHERE m.id IN $memory_ids AND all(tag IN $tags WHERE tag IN m.tags)
                RETURN m.id;
                """,
                {"memory_ids": [item["memory_id"] for item in items], "tags": list(tag_set)},
            )
            allowed = {row[0] for row in tagged}
            items = [item for item in items if item["memory_id"] in allowed]
        items.sort(
            key=lambda item: (
                float(item.get("rank_score", float("-inf"))),
                item.get("timestamp", ""),
            ),
            reverse=True,
        )
        for item in items:
            item.pop("rank_score", None)

        final_items = items[:top_k]
        if not include_meta:
            return final_items
        return {
            "items": final_items,
            "retrieval_mode": retrieval_mode,
            "diagnostics": {
                "vector_ok": vector_ok,
                "fallback_reason": fallback_reason,
                "vector_candidates": vector_candidates,
                "lexical_candidates": lexical_candidates,
                "final_count": len(final_items),
                "query_terms": query_terms,
            },
        }

    def search_similar_entities(
        self,
        query: str,
        top_k: int,
        type: Optional[str] = None,
        include_meta: bool = False,
    ) -> object:
        """Hybrid vector + lexical search over committed `Entity` vertices.

        Powers the LLM-side dedup loop: the caller passes a candidate entity
        name (and optional type), gets back ranked existing entities, and
        decides whether to upsert with a `merge_into` hint or proceed
        un-hinted.

        Vector path: `vectorNeighbors('Entity[embedding]', {{embed:query}}, $k)`,
        same shape as `search` for memories.

        Lexical path: tokenize the query (same stopwords / min-length rules
        as `search`), match against `name_norm` plus any element of
        `aliases_norm`. Lexical-only hits carry a flat penalty so the
        vector's confidence wins ties.
        """
        candidate_limit = max(top_k * 5, 20)
        max_terms = _env_int("SEARCH_LEXICAL_MAX_TERMS", 12, minimum=1)
        min_term_len = _env_int("SEARCH_LEXICAL_MIN_TERM_LEN", 3, minimum=1)

        tokens = re.findall(r"[a-z0-9_]+", normalize(query))
        filtered_terms = [
            token
            for token in tokens
            if len(token) >= min_term_len and token not in _SEARCH_STOPWORDS
        ]
        query_terms = dedupe_preserve_order(filtered_terms)[:max_terms]
        if not query_terms and tokens:
            query_terms = dedupe_preserve_order(tokens)[:max_terms]

        merged: Dict[str, Dict[str, object]] = {}
        vector_candidates = 0
        lexical_candidates = 0
        vector_ok = True

        def _upsert_match(item: Dict[str, object], score: float, source: str) -> None:
            entity_id = str(item["entity_id"])
            existing = merged.get(entity_id)
            if existing is None:
                item["rank_score"] = score
                item["sources"] = [source]
                merged[entity_id] = item
                return
            sources = list(existing.get("sources") or [])
            if source not in sources:
                sources.append(source)
            existing["sources"] = sources
            # Merge lexical/vector specific fields when the second pass adds
            # information the first did not carry.
            for key in ("similarity", "lexical_score"):
                if item.get(key) is not None and existing.get(key) is None:
                    existing[key] = item[key]
            existing_score = float(existing.get("rank_score", float("-inf")))
            if score > existing_score:
                existing["rank_score"] = score

        try:
            # SQL because `vectorNeighbors` is SQL-only in ArcadeDB 26.x.
            vector_rows = self._fetchall(
                """
                SELECT
                  id AS entity_id,
                  name,
                  type,
                  aliases,
                  distance,
                  (1.0 - distance) AS similarity
                FROM (SELECT expand(vectorNeighbors('Entity[embedding]', {{embed:query_text}}, :limit)))
                WHERE (:type IS NULL OR type = :type)
                ORDER BY similarity DESC
                LIMIT :limit
                """,
                {
                    "query_text": query,
                    "limit": candidate_limit,
                    "type": type,
                },
                language="sql",
            )
            vector_candidates = len(vector_rows)
            for row in vector_rows:
                if not row or not row[0]:
                    continue
                similarity = float(row[5]) if row[5] is not None else 0.0
                _upsert_match(
                    {
                        "entity_id": row[0],
                        "name": row[1],
                        "type": row[2],
                        "aliases": row[3] or [],
                        "similarity": similarity,
                        "lexical_score": None,
                    },
                    # 1000.0 base separates vector hits from lexical-only,
                    # mirroring memory `search`'s ranking convention.
                    1000.0 + similarity * 100.0,
                    "vector",
                )
        except Exception as exc:
            error_text = str(exc).lower()
            vector_failure_markers = (
                "vectorneighbors",
                "vector_search",
                "embedding",
                "index",
                "does not exist",
                "no such",
            )
            if not any(marker in error_text for marker in vector_failure_markers):
                raise
            vector_ok = False

        if query_terms:
            lexical_rows = self._fetchall(
                """
                MATCH (e:Entity)
                WHERE ($type IS NULL OR e.type = $type)
                WITH e,
                     coalesce(e.name_norm, toLower(e.name)) AS name_norm,
                     coalesce(e.aliases_norm, []) AS aliases_norm
                WITH e, name_norm, aliases_norm,
                     size([t IN $terms WHERE name_norm CONTAINS t]) AS name_hits,
                     size([t IN $terms WHERE
                       any(a IN aliases_norm WHERE a CONTAINS t)
                     ]) AS alias_hits
                WHERE name_hits > 0 OR alias_hits > 0
                RETURN
                  e.id AS entity_id,
                  e.name AS name,
                  e.type AS type,
                  e.aliases AS aliases,
                  name_hits AS name_hits,
                  alias_hits AS alias_hits
                ORDER BY name_hits DESC, alias_hits DESC
                LIMIT $limit;
                """,
                {
                    "terms": query_terms,
                    "limit": candidate_limit,
                    "type": type,
                },
            )
            lexical_candidates = len(lexical_rows)
            for row in lexical_rows:
                if not row or not row[0]:
                    continue
                name_hits = int(row[4] or 0)
                alias_hits = int(row[5] or 0)
                lexical_score = float((name_hits * 2.0) + alias_hits)
                # Flat 100.0 penalty keeps lexical-only hits below any
                # vector hit (which lives in the 1000.0+ band) but still
                # ranks them by hit density.
                ranked_score = 100.0 + lexical_score
                _upsert_match(
                    {
                        "entity_id": row[0],
                        "name": row[1],
                        "type": row[2],
                        "aliases": row[3] or [],
                        "similarity": None,
                        "lexical_score": lexical_score,
                    },
                    ranked_score,
                    "lexical",
                )

        items = list(merged.values())
        items.sort(
            key=lambda item: float(item.get("rank_score", float("-inf"))),
            reverse=True,
        )
        for item in items:
            item.pop("rank_score", None)
        final_items = items[:top_k]
        if not include_meta:
            return final_items
        return {
            "items": final_items,
            "diagnostics": {
                "vector_ok": vector_ok,
                "vector_candidates": vector_candidates,
                "lexical_candidates": lexical_candidates,
                "final_count": len(final_items),
                "query_terms": query_terms,
            },
        }

    def context(
        self, entity_ids: List[str], depth: int, limit: int
    ) -> Dict[str, List[Dict[str, object]]]:
        memories = self._fetchall(
            """
            UNWIND $entity_ids AS eid
            MATCH (e:Entity {id: eid})
            OPTIONAL MATCH (m:Memory)-[:ABOUT]->(e)
            WITH e, m
            ORDER BY m.timestamp DESC
            WITH e, collect(m)[..$limit] AS mems
            UNWIND mems AS m
            OPTIONAL MATCH (m)-[:ABOUT]->(e2:Entity)
            WITH m, collect(e2.id) AS entity_refs
            RETURN DISTINCT
              m.id AS memory_id,
              m.text AS text,
              m.confidence AS confidence,
              entity_refs AS entity_refs;
            """,
            {"entity_ids": entity_ids, "limit": limit},
        )
        memory_items = [
            {
                "memory_id": row[0],
                "text": row[1],
                "confidence": row[2],
                "entity_refs": row[3] or [],
            }
            for row in memories
            if row
        ]
        entities = self._fetchall(
            """
            UNWIND $entity_ids AS eid
            MATCH (e:Entity {id: eid})
            OPTIONAL MATCH (e)-[r:RELATED_TO]->(n:Entity)
            RETURN DISTINCT
              n.id AS entity_id,
              n.name AS name,
              n.type AS type,
              e.id AS from_entity_id,
              r.relationship AS relationship,
              r.confidence AS confidence
            LIMIT $limit;
            """,
            {"entity_ids": entity_ids, "limit": limit},
        )
        entity_items: List[Dict[str, object]] = []
        entity_index: Dict[str, int] = {}
        for row in entities:
            if not row or not row[0]:
                continue
            item = {
                "entity_id": row[0],
                "name": row[1],
                "type": row[2],
                "from_entity_id": row[3],
                "relationship": row[4],
                "confidence": row[5],
            }
            index = entity_index.get(row[0])
            if index is None:
                entity_index[row[0]] = len(entity_items)
                entity_items.append(item)
                continue
            existing = entity_items[index]
            if (
                existing.get("relationship") is None
                and item.get("relationship") is not None
            ):
                entity_items[index] = item
        if depth >= 2:
            depth_rows = self._fetchall(
                """
                UNWIND $entity_ids AS eid
                MATCH (e:Entity {id: eid})
                MATCH (e)-[:RELATED_TO]->(n1:Entity)-[:RELATED_TO]->(n2:Entity)
                RETURN DISTINCT
                  n2.id AS entity_id,
                  n2.name AS name,
                  n2.type AS type,
                  e.id AS from_entity_id
                LIMIT $limit;
                """,
                {"entity_ids": entity_ids, "limit": limit},
            )
            for row in depth_rows:
                if row and row[0]:
                    index = entity_index.get(row[0])
                    if index is None:
                        entity_index[row[0]] = len(entity_items)
                        entity_items.append(
                            {
                                "entity_id": row[0],
                                "name": row[1],
                                "type": row[2],
                                "from_entity_id": row[3],
                                "relationship": None,
                                "confidence": None,
                            }
                        )
        return {"memories": memory_items, "entities": entity_items[:limit]}

    def recent(self, limit: int, since: Optional[str]) -> List[Dict[str, object]]:
        rows = self._fetchall(
            """
            MATCH (m:Memory)
            WHERE ($since IS NULL OR m.timestamp >= $since)
            OPTIONAL MATCH (m)-[:ABOUT]->(e:Entity)
            WITH m, collect({entity_id: e.id, name: e.name, type: e.type}) AS entity_refs
            RETURN
              m.id AS memory_id,
              m.text AS text,
              m.confidence AS confidence,
              m.tags AS tags,
              m.timestamp AS timestamp,
              entity_refs AS entity_refs,
              m.conversation_id AS conversation_id
            ORDER BY m.timestamp DESC
            LIMIT $limit;
            """,
            {"limit": limit, "since": since},
        )
        return [
            {
                "memory_id": row[0],
                "text": row[1],
                "confidence": row[2],
                "tags": row[3] or [],
                "timestamp": row[4],
                "entity_refs": row[5] or [],
                "source_ref": {"conversation_id": row[6]},
            }
            for row in rows
        ]

    def stats(self) -> Dict[str, object]:
        entity_count_row = self._fetchone(
            "MATCH (e:Entity) RETURN count(e) AS entity_count;", {}
        )
        memory_count_row = self._fetchone(
            "MATCH (m:Memory) RETURN count(m) AS memory_count;", {}
        )
        top_entities = self._fetchall(
            """
            MATCH (e:Entity)<-[:ABOUT]-(m:Memory)
            WITH e, count(m) AS mem_count
            RETURN e.id AS entity_id, e.name AS name, e.type AS type, mem_count
            ORDER BY mem_count DESC
            LIMIT 20;
            """,
            {},
        )
        return {
            "entity_count": int(entity_count_row[0]) if entity_count_row else 0,
            "memory_count": int(memory_count_row[0]) if memory_count_row else 0,
            "top_entities": [
                {
                    "entity_id": row[0],
                    "name": row[1],
                    "type": row[2],
                    "mem_count": row[3],
                }
                for row in top_entities
            ],
        }
