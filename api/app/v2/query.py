from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .claims import RelationSchema
from .models import (
    EntityView,
    GroundedClaimView,
    GroundedEvidenceView,
    GroundedQueryRequest,
    GroundedQueryResponse,
    ModelEndpointConfig,
    QuerySeedView,
    QueryTraceView,
    QueryWarningView,
)
from .resolution import _normalize_embedding
from .salience import _capture_text
from .service import CaptureStore, Claim, Entity, Evidence, QuerySeedCandidate


QUERY_VERSION = "grounded-query-v1"
_CITATION_PATTERN = re.compile(r"\[(E[1-9][0-9]*)\]")


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_entity_ids: list[str] = Field(min_length=1, max_length=3)
    relations: list[str] = Field(default_factory=list, max_length=16)
    direction: Literal["outgoing", "incoming", "both"] = "both"


class SynthesizedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=20_000)
    citations: list[str] = Field(min_length=1, max_length=100)


class QueryGateway(Protocol):
    def embed(self, texts: list[str], endpoint: ModelEndpointConfig) -> list[list[float]]: ...

    def plan(
        self,
        question: str,
        candidates: list[dict],
        relations: dict[str, dict],
        endpoint: ModelEndpointConfig,
    ) -> dict: ...

    def synthesize(
        self,
        question: str,
        bundle: dict,
        token_budget: int,
        endpoint: ModelEndpointConfig,
    ) -> dict: ...


class BifrostQueryGateway:
    """Route query embeddings, planning, and grounded synthesis through Bifrost."""

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
                f"Bifrost query embedding failed ({response.status_code}): {response.text[:500]}"
            )
        try:
            rows = sorted(response.json()["data"], key=lambda item: int(item["index"]))
            vectors = [[float(value) for value in item["embedding"]] for item in rows]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("Bifrost returned an invalid query embedding response") from exc
        if len(vectors) != len(texts):
            raise RuntimeError("Bifrost returned the wrong number of query embeddings")
        return vectors

    def _structured_completion(
        self,
        *,
        name: str,
        schema: dict,
        system: str,
        payload: dict,
        endpoint: ModelEndpointConfig,
        max_tokens: int | None = None,
    ) -> dict:
        body = {
            "model": f"{endpoint.provider_name}/{endpoint.model_name}",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, separators=(",", ":"))},
            ],
            "stream": False,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        response = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            auth=self.auth,
            timeout=self.timeout,
            json=body,
        )
        if response.is_error:
            raise RuntimeError(
                f"Bifrost grounded query failed ({response.status_code}): {response.text[:500]}"
            )
        try:
            return json.loads(response.json()["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Bifrost returned an invalid grounded query response") from exc

    def plan(
        self,
        question: str,
        candidates: list[dict],
        relations: dict[str, dict],
        endpoint: ModelEndpointConfig,
    ) -> dict:
        return self._structured_completion(
            name="loreholm_grounded_query_plan",
            schema=QueryPlan.model_json_schema(),
            system=(
                "Plan a read-only knowledge-graph lookup. Select only candidate entity IDs and "
                "relation names supplied by the user payload. Use an empty relations list only "
                "when every adjacent relation is relevant. Never invent an entity or relation."
            ),
            payload={"question": question, "candidates": candidates, "relations": relations},
            endpoint=endpoint,
        )

    def synthesize(
        self,
        question: str,
        bundle: dict,
        token_budget: int,
        endpoint: ModelEndpointConfig,
    ) -> dict:
        return self._structured_completion(
            name="loreholm_grounded_answer",
            schema=SynthesizedAnswer.model_json_schema(),
            system=(
                "Answer only from the supplied claims and evidence. Cite factual statements with "
                "the supplied bracketed citation handles such as [E1]. Do not use outside knowledge. "
                "If the records conflict or are incomplete, say so explicitly."
            ),
            payload={"question": question, "grounding": bundle},
            endpoint=endpoint,
            max_tokens=token_budget,
        )


@dataclass(frozen=True)
class _HydratedEvidence:
    record: Evidence
    excerpt: str
    surface: str
    session_ref: str | None
    occurred_at: datetime
    normalized_at: datetime


class GroundedQueryService:
    """Use mention vectors to seed a bounded, evidence-backed graph read."""

    def __init__(
        self,
        store: CaptureStore,
        gateway: QueryGateway,
        schema: RelationSchema,
        *,
        policy,
        endpoint,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.schema = schema
        self.policy = policy
        self.endpoint = endpoint

    @staticmethod
    def _entity_view(entity: Entity) -> EntityView:
        return EntityView.model_validate(entity.__dict__)

    @staticmethod
    def _seed_payload(candidate: QuerySeedCandidate) -> dict:
        return {
            "entity_id": candidate.entity.entity_id,
            "canonical_surface": candidate.entity.canonical_surface,
            "entity_type": candidate.entity.entity_type,
            "score": candidate.score,
            "best_vector_score": candidate.best_vector_score,
            "hit_count": candidate.hit_count,
            "mention_surfaces": list(candidate.mention_surfaces),
        }

    def _available_relations(self) -> dict[str, dict]:
        relations = {
            name: definition.model_dump(mode="json")
            for name, definition in self.schema.relations.items()
        }
        for extension in self.store.list_extension_relations(limit=250):
            if extension.status == "reverted":
                continue
            if extension.status == "promoted" and extension.promoted_relation:
                continue
            relations[extension.relation] = extension.definition
        return relations

    @staticmethod
    def _claim_is_applicable(claim: Claim, as_of: datetime, include_history: bool) -> bool:
        if claim.lifecycle == "deleted":
            return False
        if not include_history and claim.lifecycle != "active":
            return False
        if include_history:
            return True
        if claim.valid_from is not None and claim.valid_from > as_of:
            return False
        if claim.valid_to is not None and claim.valid_to <= as_of:
            return False
        return True

    def _hydrate_evidence(
        self, evidence: Evidence, *, include_history: bool
    ) -> tuple[_HydratedEvidence | None, QueryWarningView | None]:
        if evidence.lifecycle == "deleted":
            return None, None
        if not include_history and evidence.lifecycle != "active":
            return None, None
        capture = self.store.get_capture(evidence.capture_id)
        if capture is None:
            return None, QueryWarningView(
                code="missing_capture",
                detail=f"Evidence {evidence.evidence_id} references a missing capture.",
                claim_ids=[evidence.claim_id],
            )
        class_policy = self.policy().classes.get(capture.envelope.capture_class)
        if class_policy is None or not class_policy.capture:
            return None, QueryWarningView(
                code="capture_disabled",
                detail=(
                    f"Evidence {evidence.evidence_id} is hidden because its capture class is disabled."
                ),
                claim_ids=[evidence.claim_id],
            )
        content = _capture_text(capture)
        if (
            evidence.source_start < 0
            or evidence.source_end > len(content)
            or evidence.source_start >= evidence.source_end
        ):
            return None, QueryWarningView(
                code="invalid_source_span",
                detail=f"Evidence {evidence.evidence_id} has an invalid source span.",
                claim_ids=[evidence.claim_id],
            )
        return _HydratedEvidence(
            record=evidence,
            excerpt=content[evidence.source_start:evidence.source_end],
            surface=capture.envelope.surface,
            session_ref=capture.envelope.session_ref,
            occurred_at=capture.envelope.occurred_at,
            normalized_at=capture.normalized_at,
        ), None

    def _enforce_synthesis_egress(
        self, hydrated: list[_HydratedEvidence], endpoint: ModelEndpointConfig
    ) -> None:
        if endpoint.processing_location != "remote":
            return
        for item in hydrated:
            capture = self.store.get_capture(item.record.capture_id)
            if capture is None:
                raise PermissionError("grounded answer source capture is missing")
            class_policy = self.policy().classes.get(capture.envelope.capture_class)
            if class_policy is None or class_policy.remote_processing != "unrestricted":
                mode = class_policy.remote_processing if class_policy is not None else "unknown"
                raise PermissionError(
                    "remote grounded synthesis requires unrestricted policy for every evidence "
                    f"capture: {capture.envelope.capture_id}:{mode}"
                )

    def _enforce_planner_egress(
        self,
        candidates: list[QuerySeedCandidate],
        endpoint: ModelEndpointConfig,
    ) -> None:
        if endpoint.processing_location != "remote":
            return
        checked: set[str] = set()
        for candidate in candidates:
            for capture_id in candidate.capture_ids:
                if capture_id in checked:
                    continue
                checked.add(capture_id)
                capture = self.store.get_capture(capture_id)
                if capture is None:
                    raise PermissionError("query seed source capture is missing")
                class_policy = self.policy().classes.get(capture.envelope.capture_class)
                if class_policy is None or class_policy.remote_processing not in {
                    "derived_only", "unrestricted",
                }:
                    mode = class_policy.remote_processing if class_policy is not None else "unknown"
                    raise PermissionError(
                        "remote query planning requires derived_only or unrestricted policy for "
                        f"every seed source: {capture_id}:{mode}"
                    )

    def _empty_response(
        self,
        request: GroundedQueryRequest,
        endpoint: ModelEndpointConfig,
        seeds: list[QuerySeedView],
        warnings: list[QueryWarningView],
        *,
        status: Literal["no_match", "no_evidence"],
        direction: Literal["outgoing", "incoming", "both"] = "both",
        relations: list[str] | None = None,
    ) -> GroundedQueryResponse:
        return GroundedQueryResponse(
            status=status, answer=None, citations=[], seeds=seeds, claims=[], evidence=[],
            warnings=warnings,
            trace=QueryTraceView(
                embedding_model=(
                    f"{endpoint.embedding_provider_name}/{endpoint.embedding_model_name}"
                ),
                schema_version=self.schema.schema_version,
                as_of=request.as_of,
                direction=direction,
                relations=relations or [],
                truncated=False,
                synthesis="no_evidence",
            ),
        )

    def query(self, request: GroundedQueryRequest) -> GroundedQueryResponse:
        endpoint = self.endpoint()
        expected_region = {
            "model": f"{endpoint.embedding_provider_name}/{endpoint.embedding_model_name}",
            "dimensions": endpoint.embedding_dimensions,
        }
        stored_region = self.store.get_config("resolution_embedding_region")
        if stored_region is not None and stored_region != expected_region:
            raise ValueError(
                "query embedding configuration does not match the mention vector region"
            )
        vectors = self.gateway.embed([request.query], endpoint)
        if len(vectors) != 1:
            raise RuntimeError("query gateway returned the wrong number of embeddings")
        embedding = _normalize_embedding(vectors[0], endpoint.embedding_dimensions)
        candidates = self.store.find_query_seed_candidates(
            embedding, limit=request.candidate_limit
        )
        candidates = [
            item for item in candidates if item.score >= request.minimum_seed_score
        ]
        if not candidates:
            return self._empty_response(request, endpoint, [], [], status="no_match")

        available_relations = self._available_relations()
        self._enforce_planner_egress(candidates, endpoint)
        raw_plan = self.gateway.plan(
            request.query,
            [self._seed_payload(item) for item in candidates],
            available_relations,
            endpoint,
        )
        plan = QueryPlan.model_validate(raw_plan)
        candidate_by_id = {item.entity.entity_id: item for item in candidates}
        invalid_seeds = sorted(set(plan.seed_entity_ids) - set(candidate_by_id))
        if invalid_seeds:
            raise ValueError("query planner selected an entity outside the vector candidate set")
        invalid_relations = sorted(set(plan.relations) - set(available_relations))
        if invalid_relations:
            raise ValueError("query planner selected an unknown relation")

        selected_ids = set(plan.seed_entity_ids)
        seed_views = [QuerySeedView(
            **self._seed_payload(item), selected=item.entity.entity_id in selected_ids
        ) for item in candidates]
        warnings: list[QueryWarningView] = []
        selected_scores = [candidate_by_id[item].score for item in plan.seed_entity_ids]
        unselected = [item for item in candidates if item.entity.entity_id not in selected_ids]
        if len(plan.seed_entity_ids) == 1 and unselected and (
            selected_scores[0] - unselected[0].score <= request.ambiguity_margin
        ):
            warnings.append(QueryWarningView(
                code="ambiguous_seed",
                detail="The selected graph seed has a near-scoring vector alternative.",
                entity_ids=[plan.seed_entity_ids[0], unselected[0].entity.entity_id],
            ))

        claims_by_id: dict[str, Claim] = {}
        fetch_limit = min(250, max(request.max_claims * 4, 40))
        for entity_id in plan.seed_entity_ids:
            for claim in self.store.claims_for_entity(
                entity_id, plan.direction, limit=fetch_limit
            ):
                if plan.relations and claim.relation not in plan.relations:
                    continue
                if self._claim_is_applicable(claim, request.as_of, request.include_history):
                    claims_by_id[claim.claim_id] = claim

        evidence_by_claim: dict[str, list[Evidence]] = {}
        for claim in claims_by_id.values():
            evidence_by_claim[claim.claim_id] = self.store.evidence_for_claim(
                claim.claim_id, limit=max(request.max_evidence_per_claim * 4, 12)
            )
        ranked_claims = sorted(
            claims_by_id.values(),
            key=lambda item: (
                len({
                    evidence.capture_id for evidence in evidence_by_claim[item.claim_id]
                    if evidence.lifecycle != "deleted"
                    and (request.include_history or evidence.lifecycle == "active")
                }),
                item.recorded_at,
                item.claim_id,
            ),
            reverse=True,
        )
        truncated = len(ranked_claims) > request.max_claims
        ranked_claims = ranked_claims[:request.max_claims]

        single_value_groups: dict[tuple[str, str], list[str]] = {}
        for claim in ranked_claims:
            if claim.statefulness == "stateful" and (
                claim.valid_from is None or claim.valid_to is None
            ):
                warnings.append(QueryWarningView(
                    code="missing_temporal_bounds",
                    detail=f"Claim {claim.claim_id} has incomplete temporal bounds.",
                    claim_ids=[claim.claim_id],
                ))
            if claim.cardinality == "one":
                single_value_groups.setdefault(
                    (claim.subject_entity_id, claim.relation), []
                ).append(claim.claim_id)
            if claim.relation.startswith("ext:"):
                warnings.append(QueryWarningView(
                    code="extension_relation",
                    detail=f"Claim {claim.claim_id} uses an unpromoted extension relation.",
                    claim_ids=[claim.claim_id],
                ))
        for claim_ids in single_value_groups.values():
            if len(claim_ids) > 1:
                warnings.append(QueryWarningView(
                    code="competing_single_value",
                    detail="Multiple applicable Claims compete for a single-valued relation.",
                    claim_ids=claim_ids,
                ))

        hydrated_by_claim: dict[str, list[_HydratedEvidence]] = {}
        for claim in ranked_claims:
            hydrated_by_claim[claim.claim_id] = []
            for evidence in evidence_by_claim[claim.claim_id]:
                hydrated, warning = self._hydrate_evidence(
                    evidence, include_history=request.include_history
                )
                if warning is not None:
                    warnings.append(warning)
                if hydrated is not None:
                    hydrated_by_claim[claim.claim_id].append(hydrated)

        packed_claims: list[Claim] = []
        packed_evidence: list[_HydratedEvidence] = []
        remaining_chars = request.token_budget * 4
        for claim in ranked_claims:
            unique_sources: list[_HydratedEvidence] = []
            repeated_sources: list[_HydratedEvidence] = []
            seen_capture_ids: set[str] = set()
            for item in hydrated_by_claim[claim.claim_id]:
                if item.record.capture_id in seen_capture_ids:
                    repeated_sources.append(item)
                else:
                    seen_capture_ids.add(item.record.capture_id)
                    unique_sources.append(item)
            evidence_rows = (unique_sources + repeated_sources)[
                :request.max_evidence_per_claim
            ]
            if not evidence_rows:
                continue
            claim_cost = 240
            selected_evidence: list[_HydratedEvidence] = []
            for item in evidence_rows:
                cost = len(item.excerpt) + 180
                if remaining_chars - claim_cost - cost < 0 and packed_evidence:
                    truncated = True
                    continue
                selected_evidence.append(item)
                remaining_chars -= cost
            if selected_evidence:
                remaining_chars -= claim_cost
                packed_claims.append(claim)
                packed_evidence.extend(selected_evidence)
        if not packed_claims:
            return self._empty_response(
                request, endpoint, seed_views, warnings, status="no_evidence",
                direction=plan.direction, relations=plan.relations,
            )

        entities_by_claim: dict[str, tuple[Entity, Entity | None]] = {}
        valid_claims: list[Claim] = []
        for claim in packed_claims:
            subject = self.store.get_entity(claim.subject_entity_id)
            object_entity = (
                self.store.get_entity(claim.object_entity_id)
                if claim.object_entity_id is not None else None
            )
            if subject is None or (claim.object_kind == "entity" and object_entity is None):
                warnings.append(QueryWarningView(
                    code="missing_entity",
                    detail=f"Claim {claim.claim_id} references a missing graph entity.",
                    claim_ids=[claim.claim_id],
                ))
                continue
            entities_by_claim[claim.claim_id] = (subject, object_entity)
            valid_claims.append(claim)
        packed_claims = valid_claims
        valid_claim_ids = {item.claim_id for item in packed_claims}
        packed_evidence = [
            item for item in packed_evidence if item.record.claim_id in valid_claim_ids
        ]
        if not packed_claims:
            return self._empty_response(
                request, endpoint, seed_views, warnings, status="no_evidence",
                direction=plan.direction, relations=plan.relations,
            )

        citation_by_evidence = {
            item.record.evidence_id: f"E{index}"
            for index, item in enumerate(packed_evidence, start=1)
        }
        evidence_views = [GroundedEvidenceView(
            citation=citation_by_evidence[item.record.evidence_id],
            evidence_id=item.record.evidence_id,
            claim_id=item.record.claim_id,
            capture_id=item.record.capture_id,
            excerpt=item.excerpt,
            source_start=item.record.source_start,
            source_end=item.record.source_end,
            surface=item.surface,
            session_ref=item.session_ref,
            occurred_at=item.occurred_at,
            normalized_at=item.normalized_at,
            run_id=item.record.run_id,
            miner_version=item.record.miner_version,
            schema_version=item.record.schema_version,
            lifecycle=item.record.lifecycle,
        ) for item in packed_evidence]
        evidence_ids_by_claim: dict[str, list[str]] = {}
        for item in packed_evidence:
            evidence_ids_by_claim.setdefault(item.record.claim_id, []).append(
                item.record.evidence_id
            )
        claim_views: list[GroundedClaimView] = []
        for claim in packed_claims:
            subject, object_entity = entities_by_claim[claim.claim_id]
            claim_views.append(GroundedClaimView(
                claim_id=claim.claim_id,
                subject=self._entity_view(subject),
                relation=claim.relation,
                object_kind=claim.object_kind,
                object_entity=self._entity_view(object_entity) if object_entity else None,
                object_literal=claim.object_literal,
                valid_from=claim.valid_from,
                valid_to=claim.valid_to,
                recorded_at=claim.recorded_at,
                statefulness=claim.statefulness,
                cardinality=claim.cardinality,
                schema_version=claim.schema_version,
                lifecycle=claim.lifecycle,
                evidence_ids=evidence_ids_by_claim[claim.claim_id],
            ))

        answer = None
        citations: list[str] = []
        synthesis: Literal["completed", "not_requested", "no_evidence"] = "not_requested"
        if request.answer:
            self._enforce_synthesis_egress(packed_evidence, endpoint)
            bundle = {
                "claims": [item.model_dump(mode="json") for item in claim_views],
                "evidence": [item.model_dump(mode="json") for item in evidence_views],
                "warnings": [item.model_dump(mode="json") for item in warnings],
            }
            synthesized = SynthesizedAnswer.model_validate(self.gateway.synthesize(
                request.query, bundle, request.token_budget, endpoint
            ))
            available_citations = {item.citation for item in evidence_views}
            if not set(synthesized.citations).issubset(available_citations):
                raise ValueError("grounded answer cited evidence outside the query bundle")
            inline_citations = set(_CITATION_PATTERN.findall(synthesized.answer))
            if not inline_citations or not inline_citations.issubset(available_citations):
                raise ValueError("grounded answer contains missing or invalid inline citations")
            if inline_citations != set(synthesized.citations):
                raise ValueError("grounded answer citation list does not match its inline citations")
            answer = synthesized.answer
            citations = synthesized.citations
            synthesis = "completed"

        return GroundedQueryResponse(
            status="ok", answer=answer, citations=citations, seeds=seed_views,
            claims=claim_views, evidence=evidence_views, warnings=warnings,
            trace=QueryTraceView(
                embedding_model=expected_region["model"],
                schema_version=self.schema.schema_version,
                as_of=request.as_of,
                direction=plan.direction,
                relations=plan.relations,
                truncated=truncated,
                synthesis=synthesis,
            ),
        )
