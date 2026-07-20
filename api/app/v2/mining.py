from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import InstancePolicy, ModelEndpointConfig
from .service import CaptureStore, MiningRun, SalienceRecord, _new_uuid7


MINER_VERSION = "structured-extractor-v1"
STAGE = "interpret_extract"


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=2_000)


class EpisodeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    evidence: list[SourceReference] = Field(min_length=1, max_length=50)
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class MentionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: str = Field(min_length=1, max_length=500)
    entity_type: str = Field(min_length=1, max_length=128)
    context: str = Field(min_length=1, max_length=2_000)
    evidence: SourceReference


class ClaimCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=500)
    relation: str = Field(pattern=r"^(?:[a-z][a-z0-9_]{1,127}|ext:[a-z][a-z0-9_]{1,123})$")
    object: str = Field(min_length=1, max_length=2_000)
    object_kind: Literal["entity", "literal"]
    evidence: list[SourceReference] = Field(min_length=1, max_length=50)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("valid_from", "valid_to")
    @classmethod
    def require_temporal_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("claim temporal bounds must include a timezone")
            return value.astimezone(timezone.utc)
        return value

    @model_validator(mode="after")
    def validate_temporal_order(self) -> "ClaimCandidate":
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_to < self.valid_from:
                raise ValueError("claim valid_to cannot precede valid_from")
        return self


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episodes: list[EpisodeCandidate] = Field(default_factory=list, max_length=100)
    mentions: list[MentionCandidate] = Field(default_factory=list, max_length=500)
    candidate_claims: list[ClaimCandidate] = Field(default_factory=list, max_length=250)

    def source_references(self) -> list[SourceReference]:
        references: list[SourceReference] = []
        for episode in self.episodes:
            references.extend(episode.evidence)
        for mention in self.mentions:
            references.append(mention.evidence)
        for claim in self.candidate_claims:
            references.extend(claim.evidence)
        return references

    def evidence_capture_ids(self) -> list[str]:
        return list(dict.fromkeys(item.capture_id for item in self.source_references()))


class ExtractionGateway(Protocol):
    def extract(self, preparation: "MiningPreparation", endpoint: ModelEndpointConfig) -> dict: ...


class ResolutionStage(Protocol):
    def resolve_run(
        self, run: MiningRun, endpoint: ModelEndpointConfig, policy: InstancePolicy
    ) -> list: ...


class ClaimCommitStage(Protocol):
    @property
    def schema_version(self) -> str: ...

    def validate_extraction(self, output: ExtractionOutput) -> None: ...

    def commit_run(self, run: MiningRun): ...


class BifrostExtractionGateway:
    def __init__(
        self,
        base_url: str,
        auth: tuple[str, str],
        relation_schema: dict | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = httpx.Timeout(180.0, connect=5.0)
        # Imported lazily because the deterministic committer consumes this
        # module's extraction models in the opposite direction.
        from .claims import extraction_schema_prompt

        self.relation_schema = relation_schema or extraction_schema_prompt()

    def extract(self, preparation: "MiningPreparation", endpoint: ModelEndpointConfig) -> dict:
        schema = ExtractionOutput.model_json_schema()
        prompt = {
            "instructions": [
                "Extract only new episodes, mentions, and candidate claims supported by delta captures.",
                "Every evidence item must quote an exact delta substring and give its zero-based start/end offsets.",
                "Context and prior output may disambiguate the delta but must not be cited as new evidence.",
                "Do not invent missing temporal bounds; use null.",
                "Use only a relation in relation_schema and obey its subject/object types.",
                "If no core relation fits, you may propose one conservative ext:<name> relation; it will be admitted as eventive and multi-valued for review.",
                "Return JSON matching the supplied schema and no prose.",
            ],
            "relation_schema": self.relation_schema,
            "evidence_eligible_capture_ids": preparation.evidence_eligible_capture_ids,
            "prior_output": preparation.prior_output,
            "context_only": preparation.context,
            "delta": preparation.delta,
        }
        response = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            auth=self.auth,
            timeout=self.timeout,
            json={
                "model": f"{endpoint.provider_name}/{endpoint.model_name}",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are Loreholm's conservative structured context extractor.",
                    },
                    {"role": "user", "content": json.dumps(prompt, separators=(",", ":"))},
                ],
                "stream": False,
                "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "loreholm_extraction",
                        "strict": True,
                        "schema": schema,
                    },
                },
            },
        )
        if response.is_error:
            raise RuntimeError(f"Bifrost extraction failed ({response.status_code}): {response.text[:500]}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            return json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Bifrost returned an invalid structured extraction response") from exc


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class MiningPreparation:
    run_key: str
    salience_id: str
    scope_id: str
    generation: int
    stage: str
    miner_version: str
    config_fingerprint: str
    input_fingerprint: str
    parent_run_id: str | None
    prior_output: dict
    context: list[dict]
    delta: list[dict]
    covered_capture_ids: list[str]
    evidence_eligible_capture_ids: list[str]
    reusable_run: MiningRun | None = None


class MiningRunCoordinator:
    """Prepare incremental miner input and persist successful reusable output."""

    def __init__(self, store: CaptureStore, *, context_turns: int = 4) -> None:
        if context_turns < 1:
            raise ValueError("context_turns must be at least 1")
        self.store = store
        self.context_turns = context_turns

    def prepare(
        self,
        salience: SalienceRecord,
        *,
        stage: str,
        miner_version: str,
        config: dict,
    ) -> MiningPreparation:
        if salience.status != "admitted":
            raise ValueError("only admitted salience records can enter mining")
        if not stage or not miner_version:
            raise ValueError("stage and miner_version are required")

        config_fingerprint = _fingerprint(config)
        parent = self.store.latest_successful_mining_run(
            salience.scope_id,
            before_generation=salience.generation,
            stage=stage,
            miner_version=miner_version,
            config_fingerprint=config_fingerprint,
        )
        previously_covered = set(parent.covered_capture_ids if parent else [])
        delta = [
            item for item in salience.trimmed
            if item["capture_id"] not in previously_covered
        ]
        delta_ids = {item["capture_id"] for item in delta}
        first_delta_index = next(
            (index for index, item in enumerate(salience.trimmed) if item["capture_id"] in delta_ids),
            len(salience.trimmed),
        )
        preceding = [
            item for item in salience.trimmed[:first_delta_index]
            if item["capture_id"] in previously_covered
        ]
        if not preceding and parent is not None:
            preceding = [
                item for item in salience.trimmed
                if item["capture_id"] in previously_covered
            ]
        context = preceding[-self.context_turns:]
        ordered_covered = [item["capture_id"] for item in salience.trimmed]
        input_payload = {
            "salience_id": salience.salience_id,
            "stage": stage,
            "miner_version": miner_version,
            "config_fingerprint": config_fingerprint,
            "parent_run_id": parent.run_id if parent else None,
            "prior_output_sha256": _fingerprint(parent.output) if parent else None,
            "context": [
                {"capture_id": item["capture_id"], "content_sha256": _fingerprint(item["content"])}
                for item in context
            ],
            "delta": [
                {"capture_id": item["capture_id"], "content_sha256": _fingerprint(item["content"])}
                for item in delta
            ],
        }
        input_fingerprint = _fingerprint(input_payload)
        run_key = _fingerprint({
            "salience_id": salience.salience_id,
            "stage": stage,
            "miner_version": miner_version,
            "config_fingerprint": config_fingerprint,
            "input_fingerprint": input_fingerprint,
        })
        reusable = self.store.get_mining_run(run_key)
        return MiningPreparation(
            run_key=run_key,
            salience_id=salience.salience_id,
            scope_id=salience.scope_id,
            generation=salience.generation,
            stage=stage,
            miner_version=miner_version,
            config_fingerprint=config_fingerprint,
            input_fingerprint=input_fingerprint,
            parent_run_id=parent.run_id if parent else None,
            prior_output=parent.output if parent else {},
            context=context,
            delta=delta,
            covered_capture_ids=ordered_covered,
            evidence_eligible_capture_ids=[item["capture_id"] for item in delta],
            reusable_run=reusable if reusable and reusable.status == "succeeded" else None,
        )

    def record_success(
        self,
        preparation: MiningPreparation,
        *,
        output: dict,
        evidence_capture_ids: list[str],
        now: datetime | None = None,
    ) -> MiningRun:
        eligible = set(preparation.evidence_eligible_capture_ids)
        invalid = sorted(set(evidence_capture_ids) - eligible)
        if invalid:
            raise ValueError(
                "context-only captures cannot become new evidence: " + ", ".join(invalid)
            )
        completed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        run = MiningRun(
            run_id=_new_uuid7(),
            run_key=preparation.run_key,
            salience_id=preparation.salience_id,
            scope_id=preparation.scope_id,
            generation=preparation.generation,
            stage=preparation.stage,
            miner_version=preparation.miner_version,
            config_fingerprint=preparation.config_fingerprint,
            input_fingerprint=preparation.input_fingerprint,
            parent_run_id=preparation.parent_run_id,
            status="succeeded",
            output=output,
            covered_capture_ids=preparation.covered_capture_ids,
            evidence_capture_ids=list(dict.fromkeys(evidence_capture_ids)),
            error=None,
            created_at=completed_at,
            completed_at=completed_at,
        )
        return self.store.put_mining_run(run)

    def record_failure(
        self,
        preparation: MiningPreparation,
        *,
        error: str,
        now: datetime | None = None,
    ) -> MiningRun:
        completed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        run = MiningRun(
            run_id=_new_uuid7(),
            run_key=preparation.run_key,
            salience_id=preparation.salience_id,
            scope_id=preparation.scope_id,
            generation=preparation.generation,
            stage=preparation.stage,
            miner_version=preparation.miner_version,
            config_fingerprint=preparation.config_fingerprint,
            input_fingerprint=preparation.input_fingerprint,
            parent_run_id=preparation.parent_run_id,
            status="failed",
            output={},
            covered_capture_ids=[],
            evidence_capture_ids=[],
            error=error[:1000],
            created_at=completed_at,
            completed_at=completed_at,
        )
        return self.store.put_mining_run(run)


class MiningWorker:
    """Claim admitted scopes and persist validated, policy-gated extraction output."""

    def __init__(
        self,
        store: CaptureStore,
        gateway: ExtractionGateway,
        *,
        worker_id: str,
        policy: Callable[[], InstancePolicy],
        endpoint: Callable[[], ModelEndpointConfig],
        resolver: ResolutionStage | None = None,
        committer: ClaimCommitStage | None = None,
        lease_for: timedelta = timedelta(minutes=5),
        retry_after: timedelta = timedelta(seconds=30),
        batch_size: int = 2,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.worker_id = worker_id
        self.policy = policy
        self.endpoint = endpoint
        self.resolver = resolver
        self.committer = committer
        if committer is None:
            from .claims import load_relation_schema

            self.relation_schema_version = load_relation_schema().schema_version
        else:
            self.relation_schema_version = committer.schema_version
        self.lease_for = lease_for
        self.retry_after = retry_after
        self.batch_size = batch_size
        self.coordinator = MiningRunCoordinator(store)

    def _enforce_egress(
        self, preparation: MiningPreparation, endpoint: ModelEndpointConfig, policy: InstancePolicy
    ) -> None:
        sent_ids = list(dict.fromkeys(
            [item["capture_id"] for item in preparation.context + preparation.delta]
        ))
        denied: list[str] = []
        for capture_id in sent_ids:
            capture = self.store.get_capture(capture_id)
            if capture is None:
                denied.append(f"{capture_id}:missing")
                continue
            class_policy = policy.classes.get(capture.envelope.capture_class)
            if class_policy is None or not class_policy.capture:
                denied.append(f"{capture_id}:capture_disabled")
                continue
            mode = class_policy.remote_processing
            if endpoint.processing_location == "remote" and mode != "unrestricted":
                denied.append(f"{capture_id}:{mode}")
        if denied:
            raise PermissionError(
                "extraction is blocked by current capture or model-egress policy: "
                + ", ".join(denied)
            )

    def process_once(self, now: datetime | None = None) -> list[MiningRun]:
        started_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        policy = self.policy()
        if policy.mining_status != "active":
            return []
        endpoint = self.endpoint()
        claimed = self.store.claim_ready_mining_work(
            self.worker_id,
            now=started_at,
            lease_for=self.lease_for,
            limit=self.batch_size,
        )
        completed: list[MiningRun] = []
        for work in claimed:
            preparation: MiningPreparation | None = None
            try:
                salience = self.store.get_salience_record(work.admission_work_id)
                if salience is None or salience.salience_id != work.salience_id:
                    raise RuntimeError("mining work has no matching salience record")
                config = {
                    "output_schema": MINER_VERSION,
                    "base_url": endpoint.base_url,
                    "provider_name": endpoint.provider_name,
                    "model_name": endpoint.model_name,
                    "processing_location": endpoint.processing_location,
                    "temperature": 0,
                    "relation_schema_version": self.relation_schema_version,
                }
                remining = self.store.active_remining_for_salience(work.salience_id)
                if remining is not None:
                    config["reprocess_token"] = remining.reprocess_token
                preparation = self.coordinator.prepare(
                    salience,
                    stage=STAGE,
                    miner_version=MINER_VERSION,
                    config=config,
                )
                if preparation.reusable_run is not None:
                    run = preparation.reusable_run
                else:
                    self._enforce_egress(preparation, endpoint, policy)
                    raw_output = self.gateway.extract(preparation, endpoint)
                    output = ExtractionOutput.model_validate(raw_output)
                    if self.committer is not None:
                        self.committer.validate_extraction(output)
                    evidence_ids = output.evidence_capture_ids()
                    invalid = sorted(
                        set(evidence_ids) - set(preparation.evidence_eligible_capture_ids)
                    )
                    if invalid:
                        raise ValueError(
                            "extraction cited context-only or unknown captures: " + ", ".join(invalid)
                        )
                    delta_content = {
                        item["capture_id"]: item["content"] for item in preparation.delta
                    }
                    for reference in output.source_references():
                        content = delta_content[reference.capture_id]
                        if reference.end > len(content) or reference.start >= reference.end:
                            raise ValueError(
                                f"extraction returned invalid source offsets for {reference.capture_id}"
                            )
                        if content[reference.start:reference.end] != reference.quote:
                            raise ValueError(
                                f"extraction source quote does not match {reference.capture_id}"
                            )
                    run = self.coordinator.record_success(
                        preparation,
                        output=output.model_dump(mode="json"),
                        evidence_capture_ids=evidence_ids,
                        now=started_at,
                    )
                if self.resolver is not None:
                    self.resolver.resolve_run(run, endpoint, policy)
                commit_result = None
                if self.committer is not None:
                    commit_result = self.committer.commit_run(run)
                if remining is not None:
                    self.store.complete_remining(
                        remining.request_id,
                        run.run_id,
                        [item.claim_id for item in (commit_result.claims if commit_result else [])],
                        now=started_at,
                    )
                if not self.store.complete_mining_work(
                    work.mining_work_id, self.worker_id, now=started_at
                ):
                    raise RuntimeError("worker lost its mining lease before completion")
                completed.append(run)
            except Exception as exc:
                if preparation is not None and not isinstance(exc, PermissionError):
                    self.coordinator.record_failure(preparation, error=str(exc), now=started_at)
                self.store.retry_mining_work(
                    work.mining_work_id,
                    self.worker_id,
                    now=started_at,
                    retry_after=self.retry_after,
                    error=str(exc),
                )
        return completed
