from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .mining import ClaimCandidate, ExtractionOutput
from .resolution import normalize_surface
from .service import (
    CaptureStore,
    Claim,
    Evidence,
    ExtensionRelation,
    MaintenanceNotice,
    MiningRun,
    ResolvedMention,
    _new_uuid7,
)


COMMITTER_VERSION = "claim-committer-v1"


class RelationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    subject_types: list[str] = Field(min_length=1)
    object_kind: Literal["entity", "literal"]
    object_entity_types: list[str]
    statefulness: Literal["stateful", "eventive"]
    cardinality: Literal["one", "many"]

    @model_validator(mode="after")
    def validate_object_types(self) -> "RelationDefinition":
        if self.object_kind == "entity" and not self.object_entity_types:
            raise ValueError("entity relations require object_entity_types")
        if self.object_kind == "literal" and self.object_entity_types:
            raise ValueError("literal relations cannot declare object_entity_types")
        return self


class RelationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1, max_length=64)
    relations: dict[str, RelationDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_relation_names(self) -> "RelationSchema":
        pattern = re.compile(r"^(?:[a-z][a-z0-9_]{1,127}|ext:[a-z][a-z0-9_]{1,123})$")
        invalid = [name for name in self.relations if pattern.fullmatch(name) is None]
        if invalid:
            raise ValueError("invalid relation names: " + ", ".join(sorted(invalid)))
        return self


def load_relation_schema(path: Path | None = None) -> RelationSchema:
    schema_path = path or Path(__file__).with_name("relation_schema.json")
    return RelationSchema.model_validate_json(schema_path.read_text(encoding="utf-8"))


def extraction_schema_prompt(schema: RelationSchema | None = None) -> dict:
    selected = schema or load_relation_schema()
    return {
        "schema_version": selected.schema_version,
        "relations": {
            name: definition.model_dump(mode="json")
            for name, definition in selected.relations.items()
        },
    }


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _literal_normal_form(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _temporal_key(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


@dataclass(frozen=True)
class CommitResult:
    claims: list[Claim]
    evidence: list[Evidence]


class ClaimCommitter:
    """Validate resolved candidates and persist idempotent claims and Evidence."""

    def __init__(self, store: CaptureStore, schema: RelationSchema | None = None) -> None:
        self.store = store
        self.schema = schema or load_relation_schema()

    @property
    def schema_version(self) -> str:
        return self.schema.schema_version

    def _relation_definition(
        self, candidate: ClaimCandidate, output: ExtractionOutput, *, admit: bool
    ) -> tuple[str, RelationDefinition, str]:
        definition = self.schema.relations.get(candidate.relation)
        if definition is not None:
            return candidate.relation, definition, self.schema.schema_version
        extension = self.store.get_extension_relation(candidate.relation)
        if extension is not None and extension.status != "reverted":
            if extension.status == "promoted" and extension.promoted_relation:
                promoted = self.schema.relations.get(extension.promoted_relation)
                if promoted is None:
                    raise ValueError("extension promotion target is absent from the core schema")
                return extension.promoted_relation, promoted, extension.schema_version
            return candidate.relation, RelationDefinition.model_validate(extension.definition), extension.schema_version
        if not candidate.relation.startswith("ext:"):
            raise ValueError(
                f"relation is not registered in {self.schema.schema_version}: {candidate.relation}"
            )
        related = [item for item in output.candidate_claims if item.relation == candidate.relation]
        subject_surfaces = {normalize_surface(item.subject) for item in related}
        object_surfaces = {
            normalize_surface(item.object) for item in related if item.object_kind == "entity"
        }
        subject_types = sorted({
            mention.entity_type for mention in output.mentions
            if normalize_surface(mention.surface) in subject_surfaces
        })
        object_types = sorted({
            mention.entity_type for mention in output.mentions
            if normalize_surface(mention.surface) in object_surfaces
        })
        definition = RelationDefinition(
            description=f"Conservatively admitted extension relation {candidate.relation}.",
            subject_types=subject_types or ["*"], object_kind=candidate.object_kind,
            object_entity_types=object_types if candidate.object_kind == "entity" else [],
            statefulness="eventive", cardinality="many",
        )
        dumped = definition.model_dump(mode="json")
        schema_version = f"ext-v1-{_fingerprint({'relation': candidate.relation, 'definition': dumped})[:12]}"
        if admit:
            now = datetime.now(timezone.utc)
            extension = self.store.put_extension_relation(ExtensionRelation(
                relation=candidate.relation, definition=dumped, status="admitted",
                schema_version=schema_version, promoted_relation=None, schema_commit=None,
                migration_plan={"strategy": "conservative_admission", "statefulness": "eventive", "cardinality": "many"},
                created_at=now, updated_at=now,
            ))
            return candidate.relation, RelationDefinition.model_validate(extension.definition), extension.schema_version
        return candidate.relation, definition, schema_version

    def validate_extraction(self, output: ExtractionOutput) -> None:
        """Reject schema-incompatible candidates before a run becomes reusable."""
        pending_extensions: dict[str, dict] = {}
        for candidate in output.candidate_claims:
            _, definition, _ = self._relation_definition(candidate, output, admit=False)
            if candidate.relation.startswith("ext:") and self.store.get_extension_relation(candidate.relation) is None:
                dumped = definition.model_dump(mode="json")
                previous = pending_extensions.setdefault(candidate.relation, dumped)
                if previous != dumped:
                    raise ValueError(
                        f"extension relation {candidate.relation} has incompatible candidate shapes"
                    )
            if candidate.object_kind != definition.object_kind:
                raise ValueError(
                    f"relation {candidate.relation} requires {definition.object_kind} objects"
                )
            subject_matches = [
                mention for mention in output.mentions
                if normalize_surface(mention.surface) == normalize_surface(candidate.subject)
            ]
            if len(subject_matches) != 1:
                raise ValueError(
                    f"claim subject must match exactly one extracted mention: {candidate.subject}"
                )
            if not self._allows(subject_matches[0].entity_type, definition.subject_types):
                raise ValueError(
                    f"relation {candidate.relation} does not allow subject type "
                    f"{subject_matches[0].entity_type}"
                )
            if candidate.object_kind == "entity":
                object_matches = [
                    mention for mention in output.mentions
                    if normalize_surface(mention.surface) == normalize_surface(candidate.object)
                ]
                if len(object_matches) != 1:
                    raise ValueError(
                        f"claim object must match exactly one extracted mention: {candidate.object}"
                    )
                if not self._allows(
                    object_matches[0].entity_type, definition.object_entity_types
                ):
                    raise ValueError(
                        f"relation {candidate.relation} does not allow object type "
                        f"{object_matches[0].entity_type}"
                    )

    @staticmethod
    def _resolve_surface(
        surface: str, mentions: list[ResolvedMention], *, role: str
    ) -> ResolvedMention:
        normalized = normalize_surface(surface)
        matches = [item for item in mentions if item.normalized_surface == normalized]
        entity_ids = {item.entity_id for item in matches}
        if not matches:
            raise ValueError(f"claim {role} has no resolved mention in this run: {surface}")
        if len(entity_ids) != 1:
            raise ValueError(f"claim {role} resolves ambiguously in this run: {surface}")
        return matches[0]

    @staticmethod
    def _allows(actual: str, allowed: list[str]) -> bool:
        return "*" in allowed or actual in allowed

    def commit_run(self, run: MiningRun) -> CommitResult:
        if run.status != "succeeded":
            raise ValueError("only successful extraction runs can be committed")
        if not self.store.is_resolution_complete(run.run_id):
            raise ValueError("claim commit requires completed entity resolution")
        output = ExtractionOutput.model_validate(run.output)
        self.validate_extraction(output)
        mentions = self.store.resolved_mentions_for_run(run.run_id)
        prepared: list[
            tuple[
                ClaimCandidate,
                RelationDefinition,
                ResolvedMention,
                str | None,
                str | None,
                str | None,
                str,
                str,
                str,
            ]
        ] = []
        for candidate in output.candidate_claims:
            relation_name, definition, relation_schema_version = self._relation_definition(
                candidate, output, admit=True
            )
            if candidate.object_kind != definition.object_kind:
                raise ValueError(
                    f"relation {candidate.relation} requires {definition.object_kind} objects"
                )

            subject = self._resolve_surface(candidate.subject, mentions, role="subject")
            if not self._allows(subject.entity_type, definition.subject_types):
                raise ValueError(
                    f"relation {candidate.relation} does not allow subject type {subject.entity_type}"
                )

            object_entity_id: str | None = None
            object_literal: str | None = None
            object_literal_norm: str | None = None
            if candidate.object_kind == "entity":
                object_mention = self._resolve_surface(candidate.object, mentions, role="object")
                if not self._allows(object_mention.entity_type, definition.object_entity_types):
                    raise ValueError(
                        f"relation {candidate.relation} does not allow object type "
                        f"{object_mention.entity_type}"
                    )
                object_entity_id = object_mention.entity_id
                object_key: dict = {"entity_id": object_entity_id}
            else:
                object_literal = _literal_normal_form(candidate.object)
                if not object_literal:
                    raise ValueError("claim literal object cannot be blank")
                object_literal_norm = object_literal
                object_key = {"literal": object_literal_norm}

            claim_key = _fingerprint({
                "subject_entity_id": subject.entity_id,
                "relation": relation_name,
                "object_kind": candidate.object_kind,
                "object": object_key,
                "valid_from": _temporal_key(candidate.valid_from),
                "valid_to": _temporal_key(candidate.valid_to),
            })
            for reference in candidate.evidence:
                if reference.capture_id not in run.evidence_capture_ids:
                    raise ValueError(
                        f"claim evidence is outside the run evidence boundary: {reference.capture_id}"
                    )
                if self.store.get_capture(reference.capture_id) is None:
                    raise ValueError(f"claim evidence capture is missing: {reference.capture_id}")
            prepared.append((
                candidate,
                definition,
                subject,
                object_entity_id,
                object_literal,
                object_literal_norm,
                claim_key,
                relation_name,
                relation_schema_version,
            ))

        # Validation is intentionally complete before the first graph write.
        # Persistence below remains idempotent for storage failures partway through.
        committed_claims: dict[str, Claim] = {}
        committed_evidence: dict[str, Evidence] = {}
        for (
            candidate,
            definition,
            subject,
            object_entity_id,
            object_literal,
            object_literal_norm,
            claim_key,
            relation_name,
            relation_schema_version,
        ) in prepared:
            claim = self.store.put_claim(Claim(
                claim_id=_new_uuid7(),
                claim_key=claim_key,
                subject_entity_id=subject.entity_id,
                relation=relation_name,
                object_kind=candidate.object_kind,
                object_entity_id=object_entity_id,
                object_literal=object_literal,
                object_literal_norm=object_literal_norm,
                valid_from=candidate.valid_from,
                valid_to=candidate.valid_to,
                recorded_at=run.completed_at,
                statefulness=definition.statefulness,
                cardinality=definition.cardinality,
                schema_version=relation_schema_version,
                lifecycle="active",
                first_run_id=run.run_id,
            ))
            committed_claims[claim.claim_id] = claim

            for reference in candidate.evidence:
                evidence_key = _fingerprint({
                    "claim_id": claim.claim_id,
                    "capture_id": reference.capture_id,
                    "source_start": reference.start,
                    "source_end": reference.end,
                })
                evidence = self.store.put_evidence(Evidence(
                    evidence_id=_new_uuid7(),
                    evidence_key=evidence_key,
                    claim_id=claim.claim_id,
                    capture_id=reference.capture_id,
                    source_start=reference.start,
                    source_end=reference.end,
                    run_id=run.run_id,
                    miner_version=run.miner_version,
                    schema_version=relation_schema_version,
                    lifecycle="active",
                    recorded_at=run.completed_at,
                ))
                committed_evidence[evidence.evidence_id] = evidence

            if claim.statefulness == "stateful" and (
                claim.valid_from is None or claim.valid_to is None
            ):
                self.store.put_maintenance_notice(MaintenanceNotice(
                    notice_id=_new_uuid7(), notice_key=f"temporal:{claim.claim_id}",
                    kind="missing_temporal_bounds", status="open", scope_id=run.scope_id,
                    claim_id=claim.claim_id, run_id=run.run_id,
                    details={"missing_valid_from": claim.valid_from is None, "missing_valid_to": claim.valid_to is None},
                    created_at=run.completed_at,
                ))
            if claim.statefulness == "stateful" and claim.cardinality == "one":
                competing = [
                    item.claim_id
                    for item in self.store.active_claims_for_subject_relation(
                        claim.subject_entity_id, claim.relation
                    )
                    if item.claim_id != claim.claim_id and item.claim_key != claim.claim_key
                ]
                if competing:
                    ids = sorted([claim.claim_id, *competing])
                    self.store.put_maintenance_notice(MaintenanceNotice(
                        notice_id=_new_uuid7(), notice_key="competition:" + _fingerprint(ids),
                        kind="competing_single_value", status="open", scope_id=run.scope_id,
                        claim_id=claim.claim_id, run_id=run.run_id,
                        details={"claim_ids": ids, "relation": claim.relation}, created_at=run.completed_at,
                    ))

        self.store.mark_claim_commit_complete(
            run.run_id,
            COMMITTER_VERSION,
            self.schema.schema_version,
            len(committed_claims),
            len(committed_evidence),
            completed_at=run.completed_at,
        )
        return CommitResult(list(committed_claims.values()), list(committed_evidence.values()))
