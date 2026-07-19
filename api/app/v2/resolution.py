from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from .mining import ExtractionOutput
from .models import InstancePolicy, ModelEndpointConfig
from .service import (
    CaptureStore,
    Entity,
    EntityCandidate,
    MiningRun,
    ResolvedMention,
    _new_uuid7,
)


RESOLVER_VERSION = "entity-resolver-v1"


class ResolutionJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["match", "mint"]
    entity_id: str | None


class ResolutionGateway(Protocol):
    def embed(self, texts: list[str], endpoint: ModelEndpointConfig) -> list[list[float]]: ...

    def judge(
        self,
        mention: dict,
        candidates: list[dict],
        endpoint: ModelEndpointConfig,
    ) -> dict: ...


class BifrostResolutionGateway:
    """Send embeddings and ambiguous identity judgments only through Bifrost."""

    def __init__(self, base_url: str, auth: tuple[str, str]) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = httpx.Timeout(180.0, connect=5.0)

    def embed(self, texts: list[str], endpoint: ModelEndpointConfig) -> list[list[float]]:
        response = httpx.post(
            f"{self.base_url}/v1/embeddings",
            auth=self.auth,
            timeout=self.timeout,
            json={
                "model": f"{endpoint.embedding_provider_name}/{endpoint.embedding_model_name}",
                "input": texts,
                "encoding_format": "float",
                "dimensions": endpoint.embedding_dimensions,
            },
        )
        if response.is_error:
            raise RuntimeError(
                f"Bifrost embedding failed ({response.status_code}): {response.text[:500]}"
            )
        try:
            rows = sorted(response.json()["data"], key=lambda item: int(item["index"]))
            vectors = [[float(value) for value in item["embedding"]] for item in rows]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("Bifrost returned an invalid embedding response") from exc
        if len(vectors) != len(texts):
            raise RuntimeError("Bifrost returned the wrong number of embeddings")
        return vectors

    def judge(
        self,
        mention: dict,
        candidates: list[dict],
        endpoint: ModelEndpointConfig,
    ) -> dict:
        schema = ResolutionJudgment.model_json_schema()
        response = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            auth=self.auth,
            timeout=self.timeout,
            json={
                "model": f"{endpoint.provider_name}/{endpoint.model_name}",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Resolve the mention conservatively. Return action 'match' with exactly one "
                            "candidate entity_id, or action 'mint' with a null entity_id."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"mention": mention, "candidates": candidates},
                            separators=(",", ":"),
                        ),
                    },
                ],
                "stream": False,
                "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "loreholm_entity_resolution",
                        "strict": True,
                        "schema": schema,
                    },
                },
            },
        )
        if response.is_error:
            raise RuntimeError(
                f"Bifrost identity judgment failed ({response.status_code}): {response.text[:500]}"
            )
        try:
            return json.loads(response.json()["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Bifrost returned an invalid identity judgment") from exc


@dataclass(frozen=True)
class ResolutionConfig:
    high_threshold: float = 0.90
    low_threshold: float = 0.68
    vector_weight: float = 0.75
    candidate_limit: int = 8

    def __post_init__(self) -> None:
        if not 0 <= self.low_threshold < self.high_threshold <= 1:
            raise ValueError("resolution thresholds must satisfy 0 <= low < high <= 1")
        if not 0 <= self.vector_weight <= 1:
            raise ValueError("vector_weight must be between 0 and 1")
        if self.candidate_limit < 1:
            raise ValueError("candidate_limit must be positive")


def normalize_surface(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\s-]", " ", normalized)
    return " ".join(normalized.split())


def _mention_key(run_id: str, index: int, mention: dict) -> str:
    payload = json.dumps(
        {"run_id": run_id, "index": index, "mention": mention},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _normalize_embedding(vector: list[float], dimensions: int) -> list[float]:
    if len(vector) != dimensions:
        raise ValueError(f"embedding has {len(vector)} dimensions; expected {dimensions}")
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("embedding contains a non-finite value")
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        raise ValueError("embedding cannot be the zero vector")
    return [value / magnitude for value in vector]


class EntityResolver:
    """Materialize extracted mentions and resolve them to stable entity vertices."""

    def __init__(
        self,
        store: CaptureStore,
        gateway: ResolutionGateway,
        config: ResolutionConfig | None = None,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.config = config or ResolutionConfig()

    def _enforce_egress(
        self,
        capture_id: str,
        processing_location: str,
        policy: InstancePolicy,
        operation: str,
    ) -> None:
        capture = self.store.get_capture(capture_id)
        if capture is None:
            raise PermissionError(f"entity resolution source is missing: {capture_id}")
        class_policy = policy.classes.get(capture.envelope.capture_class)
        if class_policy is None or not class_policy.capture:
            raise PermissionError(f"entity resolution source is disabled: {capture_id}")
        if processing_location == "remote" and class_policy.remote_processing not in {
            "derived_only", "unrestricted",
        }:
            raise PermissionError(
                f"remote entity-resolution {operation} requires derived_only or unrestricted policy: "
                f"{capture_id}:{class_policy.remote_processing}"
            )

    def _mint_entity(self, mention_key: str, surface: str, entity_type: str, now) -> Entity:
        return self.store.put_entity(Entity(
            entity_id=_new_uuid7(),
            entity_key=f"mention:{mention_key}",
            canonical_surface=surface,
            normalized_surface=normalize_surface(surface),
            entity_type=entity_type,
            created_at=now,
        ))

    def _scored_candidates(
        self, surface: str, entity_type: str, embedding: list[float]
    ) -> list[tuple[EntityCandidate, float, float]]:
        normalized = normalize_surface(surface)
        scored: list[tuple[EntityCandidate, float, float]] = []
        for candidate in self.store.find_entity_candidates(
            embedding, entity_type, limit=self.config.candidate_limit
        ):
            string_score = max(
                SequenceMatcher(None, normalized, candidate.entity.normalized_surface).ratio(),
                SequenceMatcher(None, normalized, normalize_surface(candidate.mention_surface)).ratio(),
            )
            combined = (
                self.config.vector_weight * max(0.0, candidate.vector_score)
                + (1 - self.config.vector_weight) * string_score
            )
            scored.append((candidate, string_score, combined))
        return sorted(scored, key=lambda item: item[2], reverse=True)

    def resolve_run(
        self,
        run: MiningRun,
        endpoint: ModelEndpointConfig,
        policy: InstancePolicy,
    ) -> list[ResolvedMention]:
        if run.status != "succeeded":
            raise ValueError("only successful extraction runs can be resolved")
        embedding_region = {
            "model": f"{endpoint.embedding_provider_name}/{endpoint.embedding_model_name}",
            "dimensions": endpoint.embedding_dimensions,
        }
        stored_region = self.store.get_config("resolution_embedding_region")
        if stored_region is None:
            self.store.set_config("resolution_embedding_region", embedding_region)
        elif stored_region != embedding_region:
            raise ValueError(
                "the entity-resolution vector region is locked to "
                f"{stored_region.get('model')} ({stored_region.get('dimensions')} dimensions); "
                "a changed embedding configuration requires a new vector region"
            )
        output = ExtractionOutput.model_validate(run.output)
        pending: list[tuple[int, dict, str]] = []
        resolved: list[ResolvedMention] = []
        for index, candidate in enumerate(output.mentions):
            value = candidate.model_dump(mode="json")
            mention_key = _mention_key(run.run_id, index, value)
            existing = self.store.get_resolved_mention(mention_key)
            if existing is not None:
                resolved.append(existing)
                continue
            self._enforce_egress(
                candidate.evidence.capture_id,
                endpoint.embedding_processing_location,
                policy,
                "embedding",
            )
            pending.append((index, value, mention_key))
        if not pending:
            self.store.mark_resolution_complete(
                run.run_id, RESOLVER_VERSION, len(resolved), completed_at=run.completed_at
            )
            return resolved

        texts = [f"{item['surface']}\nType: {item['entity_type']}\nContext: {item['context']}" for _, item, _ in pending]
        vectors = self.gateway.embed(texts, endpoint)
        if len(vectors) != len(pending):
            raise RuntimeError("resolution gateway returned the wrong number of embeddings")

        for (_, mention, mention_key), raw_vector in zip(pending, vectors):
            embedding = _normalize_embedding(raw_vector, endpoint.embedding_dimensions)
            normalized = normalize_surface(mention["surface"])
            exact = self.store.find_entity_by_surface(normalized, mention["entity_type"])
            vector_score = string_score = combined_score = None
            scored: list[tuple[EntityCandidate, float, float]] = []
            judgment_payload: dict | None = None
            if exact is not None:
                entity = exact
                method = "exact"
                string_score = 1.0
                combined_score = 1.0
            else:
                scored = self._scored_candidates(
                    mention["surface"], mention["entity_type"], embedding
                )
                best = scored[0] if scored else None
                if best is None or best[2] < self.config.low_threshold:
                    entity = self._mint_entity(
                        mention_key, mention["surface"], mention["entity_type"], run.completed_at
                    )
                    method = "automatic_mint"
                elif best[2] >= self.config.high_threshold:
                    entity = best[0].entity
                    method = "automatic_match"
                else:
                    self._enforce_egress(
                        mention["evidence"]["capture_id"],
                        endpoint.processing_location,
                        policy,
                        "judgment",
                    )
                    judgment_payload = self.gateway.judge(
                        {
                            "surface": mention["surface"],
                            "entity_type": mention["entity_type"],
                            "context": mention["context"],
                        },
                        [{
                            "entity_id": item[0].entity.entity_id,
                            "canonical_surface": item[0].entity.canonical_surface,
                            "vector_score": item[0].vector_score,
                            "string_score": item[1],
                            "combined_score": item[2],
                        } for item in scored],
                        endpoint,
                    )
                    judgment = ResolutionJudgment.model_validate(judgment_payload)
                    candidate_ids = {item[0].entity.entity_id for item in scored}
                    if judgment.action == "match":
                        if judgment.entity_id not in candidate_ids:
                            raise ValueError("identity judge selected an entity outside the candidate set")
                        entity = self.store.get_entity(judgment.entity_id or "")
                        if entity is None:
                            raise ValueError("identity judge selected a missing entity")
                        method = "judged_match"
                    elif judgment.action == "mint" and judgment.entity_id is None:
                        entity = self._mint_entity(
                            mention_key, mention["surface"], mention["entity_type"], run.completed_at
                        )
                        method = "judged_mint"
                    else:
                        raise ValueError("identity judge returned an invalid action/entity pair")
                if best is not None:
                    vector_score = best[0].vector_score
                    string_score = best[1]
                    combined_score = best[2]

            evidence = mention["evidence"]
            record = self.store.put_resolved_mention(ResolvedMention(
                mention_id=_new_uuid7(),
                mention_key=mention_key,
                run_id=run.run_id,
                capture_id=evidence["capture_id"],
                source_start=evidence["start"],
                source_end=evidence["end"],
                surface=mention["surface"],
                normalized_surface=normalized,
                entity_type=mention["entity_type"],
                context=mention["context"],
                embedding=embedding,
                embedding_model=(
                    f"{endpoint.embedding_provider_name}/{endpoint.embedding_model_name}"
                ),
                resolver_version=RESOLVER_VERSION,
                entity_id=entity.entity_id,
                resolution_method=method,
                vector_score=vector_score,
                string_score=string_score,
                combined_score=combined_score,
                decision_details={
                    "thresholds": {
                        "low": self.config.low_threshold,
                        "high": self.config.high_threshold,
                        "vector_weight": self.config.vector_weight,
                    },
                    "candidates": [{
                        "entity_id": item[0].entity.entity_id,
                        "vector_score": item[0].vector_score,
                        "string_score": item[1],
                        "combined_score": item[2],
                    } for item in scored],
                    "judgment": judgment_payload,
                },
                created_at=run.completed_at,
            ))
            resolved.append(record)
        self.store.mark_resolution_complete(
            run.run_id, RESOLVER_VERSION, len(resolved), completed_at=run.completed_at
        )
        return resolved
