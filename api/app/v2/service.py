from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Literal

import httpx

from .models import CaptureEnvelope, CaptureReceipt, InstancePolicy


@dataclass(frozen=True)
class StoredCapture:
    envelope: CaptureEnvelope
    received_at: datetime
    normalized_at: datetime
    state: str


@dataclass
class StoredSession:
    session_id: str
    scope_key: str
    user_id: str
    surface: str
    session_ref: str
    first_normalized_at: datetime
    last_normalized_at: datetime
    capture_count: int
    generation: int
    current_work_id: str


@dataclass
class WorkItem:
    work_id: str
    work_key: str
    work_type: Literal["session_admission", "push_admission"]
    scope_id: str
    generation: int
    state: Literal["pending", "leased", "completed"]
    available_at: datetime
    input_through: datetime
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    lease_owner: str | None = None
    leased_until: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class SalienceRecord:
    salience_id: str
    work_id: str
    work_key: str
    work_type: Literal["session_admission", "push_admission"]
    scope_id: str
    generation: int
    status: Literal["admitted", "skipped"]
    reason: str
    algorithm_version: str
    input_fingerprint: str
    signals: dict
    trimmed: list[dict]
    created_at: datetime


@dataclass
class MiningWorkItem:
    mining_work_id: str
    salience_id: str
    admission_work_id: str
    state: Literal["pending", "leased", "completed"]
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    lease_owner: str | None = None
    leased_until: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class MiningRun:
    run_id: str
    run_key: str
    salience_id: str
    scope_id: str
    generation: int
    stage: str
    miner_version: str
    config_fingerprint: str
    input_fingerprint: str
    parent_run_id: str | None
    status: Literal["succeeded", "failed"]
    output: dict
    covered_capture_ids: list[str]
    evidence_capture_ids: list[str]
    error: str | None
    created_at: datetime
    completed_at: datetime


@dataclass(frozen=True)
class Entity:
    entity_id: str
    entity_key: str
    canonical_surface: str
    normalized_surface: str
    entity_type: str
    created_at: datetime


@dataclass(frozen=True)
class ResolvedMention:
    mention_id: str
    mention_key: str
    run_id: str
    capture_id: str
    source_start: int
    source_end: int
    surface: str
    normalized_surface: str
    entity_type: str
    context: str
    embedding: list[float]
    embedding_model: str
    resolver_version: str
    entity_id: str
    resolution_method: Literal[
        "exact", "automatic_match", "automatic_mint", "judged_match", "judged_mint"
    ]
    vector_score: float | None
    string_score: float | None
    combined_score: float | None
    decision_details: dict
    created_at: datetime


@dataclass(frozen=True)
class EntityCandidate:
    entity: Entity
    vector_score: float
    mention_surface: str


@dataclass(frozen=True)
class Claim:
    claim_id: str
    claim_key: str
    subject_entity_id: str
    relation: str
    object_kind: Literal["entity", "literal"]
    object_entity_id: str | None
    object_literal: str | None
    object_literal_norm: str | None
    valid_from: datetime | None
    valid_to: datetime | None
    recorded_at: datetime
    statefulness: Literal["stateful", "eventive"]
    cardinality: Literal["one", "many"]
    schema_version: str
    lifecycle: Literal["active", "superseded", "deleted"]
    first_run_id: str


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    evidence_key: str
    claim_id: str
    capture_id: str
    source_start: int
    source_end: int
    run_id: str
    miner_version: str
    schema_version: str
    lifecycle: Literal["active", "superseded", "deleted"]
    recorded_at: datetime


@dataclass(frozen=True)
class MaintenanceNotice:
    notice_id: str
    notice_key: str
    kind: Literal["missing_temporal_bounds", "competing_single_value", "remining_applied"]
    status: Literal["open", "resolved"]
    scope_id: str
    claim_id: str | None
    run_id: str | None
    details: dict
    created_at: datetime
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class ReminingRequest:
    request_id: str
    request_key: str
    source_run_id: str
    salience_id: str
    scope_id: str
    reason: str
    reprocess_token: str
    status: Literal["pending", "completed", "failed"]
    replacement_run_id: str | None
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class ExtensionRelation:
    relation: str
    definition: dict
    status: Literal["admitted", "promoted", "reverted"]
    schema_version: str
    promoted_relation: str | None
    schema_commit: str | None
    migration_plan: dict
    created_at: datetime
    updated_at: datetime


def _new_uuid7() -> str:
    millis = int(time.time() * 1000)
    value = (millis & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    raw = f"{value:032x}"
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _session_scope_key(capture: CaptureEnvelope) -> str:
    session_ref = capture.session_ref or f"capture:{capture.capture_id}"
    material = "\x00".join((capture.meta.user_id, capture.surface, session_ref))
    return hashlib.sha256(material.encode()).hexdigest()


def _epoch_millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class CaptureStore:
    """Persistence seam; the V2 ArcadeDB adapter implements this contract."""

    def put_if_absent(self, capture: StoredCapture) -> bool:
        raise NotImplementedError

    def get_capture(self, capture_id: str) -> StoredCapture | None:
        raise NotImplementedError

    def get_config(self, key: str) -> dict | None:
        raise NotImplementedError

    def set_config(self, key: str, value: dict) -> None:
        raise NotImplementedError

    def schedule_capture(
        self,
        capture: StoredCapture,
        *,
        quiescence: timedelta,
    ) -> None:
        raise NotImplementedError

    def claim_ready_work(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_for: timedelta,
        limit: int = 1,
    ) -> list[WorkItem]:
        raise NotImplementedError

    def complete_work(self, work_id: str, worker_id: str, *, now: datetime) -> bool:
        raise NotImplementedError

    def retry_work(
        self,
        work_id: str,
        worker_id: str,
        *,
        now: datetime,
        retry_after: timedelta,
        error: str,
    ) -> bool:
        raise NotImplementedError

    def captures_for_work(self, work: WorkItem) -> list[StoredCapture]:
        raise NotImplementedError

    def get_salience_record(self, work_id: str) -> SalienceRecord | None:
        raise NotImplementedError

    def put_salience_record(self, record: SalienceRecord) -> SalienceRecord:
        raise NotImplementedError

    def schedule_mining(self, record: SalienceRecord) -> MiningWorkItem | None:
        raise NotImplementedError

    def backfill_mining_work(self) -> int:
        raise NotImplementedError

    def claim_ready_mining_work(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_for: timedelta,
        limit: int = 1,
    ) -> list[MiningWorkItem]:
        raise NotImplementedError

    def complete_mining_work(
        self, mining_work_id: str, worker_id: str, *, now: datetime
    ) -> bool:
        raise NotImplementedError

    def retry_mining_work(
        self,
        mining_work_id: str,
        worker_id: str,
        *,
        now: datetime,
        retry_after: timedelta,
        error: str,
    ) -> bool:
        raise NotImplementedError

    def get_mining_run(self, run_key: str) -> MiningRun | None:
        raise NotImplementedError

    def latest_successful_mining_run(
        self,
        scope_id: str,
        *,
        before_generation: int,
        stage: str,
        miner_version: str,
        config_fingerprint: str,
    ) -> MiningRun | None:
        raise NotImplementedError

    def put_mining_run(self, run: MiningRun) -> MiningRun:
        raise NotImplementedError

    def list_mining_runs(self, *, limit: int = 50) -> list[MiningRun]:
        raise NotImplementedError

    def get_resolved_mention(self, mention_key: str) -> ResolvedMention | None:
        raise NotImplementedError

    def put_resolved_mention(self, mention: ResolvedMention) -> ResolvedMention:
        raise NotImplementedError

    def get_entity(self, entity_id: str) -> Entity | None:
        raise NotImplementedError

    def find_entity_by_surface(self, normalized_surface: str, entity_type: str) -> Entity | None:
        raise NotImplementedError

    def put_entity(self, entity: Entity) -> Entity:
        raise NotImplementedError

    def find_entity_candidates(
        self, embedding: list[float], entity_type: str, *, limit: int = 10
    ) -> list[EntityCandidate]:
        raise NotImplementedError

    def list_resolved_mentions(self, *, limit: int = 50) -> list[ResolvedMention]:
        raise NotImplementedError

    def list_entities(self, *, limit: int = 50) -> list[Entity]:
        raise NotImplementedError

    def mark_resolution_complete(
        self, run_id: str, resolver_version: str, mention_count: int, *, completed_at: datetime
    ) -> None:
        raise NotImplementedError

    def is_resolution_complete(self, run_id: str) -> bool:
        raise NotImplementedError

    def resolved_mentions_for_run(self, run_id: str) -> list[ResolvedMention]:
        raise NotImplementedError

    def put_claim(self, claim: Claim) -> Claim:
        raise NotImplementedError

    def put_evidence(self, evidence: Evidence) -> Evidence:
        raise NotImplementedError

    def list_claims(self, *, limit: int = 50) -> list[Claim]:
        raise NotImplementedError

    def list_evidence(self, *, limit: int = 50) -> list[Evidence]:
        raise NotImplementedError

    def mark_claim_commit_complete(
        self,
        run_id: str,
        committer_version: str,
        schema_version: str,
        claim_count: int,
        evidence_count: int,
        *,
        completed_at: datetime,
    ) -> None:
        raise NotImplementedError

    def is_claim_commit_complete(self, run_id: str) -> bool:
        raise NotImplementedError

    def put_maintenance_notice(self, notice: MaintenanceNotice) -> MaintenanceNotice:
        raise NotImplementedError

    def list_maintenance_notices(self, *, limit: int = 50) -> list[MaintenanceNotice]:
        raise NotImplementedError

    def active_claims_for_subject_relation(self, subject_entity_id: str, relation: str) -> list[Claim]:
        raise NotImplementedError

    def request_remining(self, request: ReminingRequest) -> ReminingRequest:
        raise NotImplementedError

    def active_remining_for_salience(self, salience_id: str) -> ReminingRequest | None:
        raise NotImplementedError

    def complete_remining(
        self, request_id: str, replacement_run_id: str, replacement_claim_ids: list[str], *, now: datetime
    ) -> ReminingRequest:
        raise NotImplementedError

    def list_remining_requests(self, *, limit: int = 50) -> list[ReminingRequest]:
        raise NotImplementedError

    def get_mining_run_by_id(self, run_id: str) -> MiningRun | None:
        raise NotImplementedError

    def put_extension_relation(self, relation: ExtensionRelation) -> ExtensionRelation:
        raise NotImplementedError

    def get_extension_relation(self, relation: str) -> ExtensionRelation | None:
        raise NotImplementedError

    def list_extension_relations(self, *, limit: int = 100) -> list[ExtensionRelation]:
        raise NotImplementedError

    def update_extension_relation(self, relation: ExtensionRelation) -> ExtensionRelation:
        raise NotImplementedError

    def supersede_claims_for_relation(self, relation: str) -> list[str]:
        raise NotImplementedError


class MemoryCaptureStore(CaptureStore):
    """Deterministic development/test store, never selected in production."""

    def __init__(self) -> None:
        self._captures: dict[str, StoredCapture] = {}
        self._config: dict[str, dict] = {}
        self._sessions: dict[str, StoredSession] = {}
        self._session_by_scope: dict[str, str] = {}
        self._work: dict[str, WorkItem] = {}
        self._session_members: dict[str, tuple[str, int]] = {}
        self._salience: dict[str, SalienceRecord] = {}
        self._mining_work: dict[str, MiningWorkItem] = {}
        self._mining_runs: dict[str, MiningRun] = {}
        self._entities: dict[str, Entity] = {}
        self._entity_by_key: dict[str, str] = {}
        self._mentions: dict[str, ResolvedMention] = {}
        self._resolution_complete: dict[str, dict] = {}
        self._claims: dict[str, Claim] = {}
        self._claim_by_key: dict[str, str] = {}
        self._evidence: dict[str, Evidence] = {}
        self._evidence_by_key: dict[str, str] = {}
        self._claim_commit_complete: dict[str, dict] = {}
        self._maintenance_notices: dict[str, MaintenanceNotice] = {}
        self._remining_requests: dict[str, ReminingRequest] = {}
        self._extension_relations: dict[str, ExtensionRelation] = {}
        self._scheduled_captures: set[str] = set()
        self._lock = Lock()

    def put_if_absent(self, capture: StoredCapture) -> bool:
        with self._lock:
            if capture.envelope.capture_id in self._captures:
                return False
            self._captures[capture.envelope.capture_id] = capture
            return True

    def get_capture(self, capture_id: str) -> StoredCapture | None:
        return self._captures.get(capture_id)

    def get_config(self, key: str) -> dict | None:
        return self._config.get(key)

    def set_config(self, key: str, value: dict) -> None:
        self._config[key] = value

    @property
    def captures(self) -> tuple[StoredCapture, ...]:
        return tuple(self._captures.values())

    @property
    def sessions(self) -> tuple[StoredSession, ...]:
        return tuple(self._sessions.values())

    @property
    def work_items(self) -> tuple[WorkItem, ...]:
        return tuple(self._work.values())

    @property
    def salience_records(self) -> tuple[SalienceRecord, ...]:
        return tuple(self._salience.values())

    @property
    def mining_runs(self) -> tuple[MiningRun, ...]:
        return tuple(self._mining_runs.values())

    @property
    def mining_work_items(self) -> tuple[MiningWorkItem, ...]:
        return tuple(self._mining_work.values())

    @property
    def entities(self) -> tuple[Entity, ...]:
        return tuple(self._entities.values())

    @property
    def resolved_mentions(self) -> tuple[ResolvedMention, ...]:
        return tuple(self._mentions.values())

    @property
    def claims(self) -> tuple[Claim, ...]:
        return tuple(self._claims.values())

    @property
    def evidence_records(self) -> tuple[Evidence, ...]:
        return tuple(self._evidence.values())

    def _new_work(
        self,
        *,
        work_key: str,
        work_type: Literal["session_admission", "push_admission"],
        scope_id: str,
        generation: int,
        available_at: datetime,
        input_through: datetime,
        now: datetime,
    ) -> WorkItem:
        work = WorkItem(
            work_id=_new_uuid7(),
            work_key=work_key,
            work_type=work_type,
            scope_id=scope_id,
            generation=generation,
            state="pending",
            available_at=available_at,
            input_through=input_through,
            created_at=now,
            updated_at=now,
        )
        self._work[work.work_id] = work
        return work

    def schedule_capture(self, capture: StoredCapture, *, quiescence: timedelta) -> None:
        envelope = capture.envelope
        with self._lock:
            if envelope.capture_id in self._scheduled_captures:
                return
            if envelope.capture_class.startswith("push."):
                work_key = f"push:{envelope.capture_id}"
                if any(item.work_key == work_key for item in self._work.values()):
                    return
                self._new_work(
                    work_key=work_key,
                    work_type="push_admission",
                    scope_id=envelope.capture_id,
                    generation=1,
                    available_at=capture.received_at,
                    input_through=capture.normalized_at,
                    now=capture.received_at,
                )
                self._scheduled_captures.add(envelope.capture_id)
                return
            if envelope.capture_class != "transcript.message":
                self._scheduled_captures.add(envelope.capture_id)
                return

            scope_key = _session_scope_key(envelope)
            session_id = self._session_by_scope.get(scope_key)
            if session_id is None:
                session_id = envelope.capture_id
                work = self._new_work(
                    work_key=f"session:{session_id}:1",
                    work_type="session_admission",
                    scope_id=session_id,
                    generation=1,
                    available_at=capture.received_at + quiescence,
                    input_through=capture.normalized_at,
                    now=capture.received_at,
                )
                self._sessions[session_id] = StoredSession(
                    session_id=session_id,
                    scope_key=scope_key,
                    user_id=envelope.meta.user_id,
                    surface=envelope.surface,
                    session_ref=envelope.session_ref or f"capture:{envelope.capture_id}",
                    first_normalized_at=capture.normalized_at,
                    last_normalized_at=capture.normalized_at,
                    capture_count=1,
                    generation=1,
                    current_work_id=work.work_id,
                )
                self._session_by_scope[scope_key] = session_id
                self._session_members[envelope.capture_id] = (session_id, 1)
                self._scheduled_captures.add(envelope.capture_id)
                return

            session = self._sessions[session_id]
            session.first_normalized_at = min(session.first_normalized_at, capture.normalized_at)
            session.last_normalized_at = max(session.last_normalized_at, capture.normalized_at)
            session.capture_count += 1
            current = self._work[session.current_work_id]
            if current.state == "pending":
                current.available_at = capture.received_at + quiescence
                current.input_through = max(current.input_through, capture.normalized_at)
                current.updated_at = capture.received_at
                self._session_members[envelope.capture_id] = (session_id, session.generation)
                self._scheduled_captures.add(envelope.capture_id)
                return

            session.generation += 1
            work = self._new_work(
                work_key=f"session:{session_id}:{session.generation}",
                work_type="session_admission",
                scope_id=session_id,
                generation=session.generation,
                available_at=capture.received_at + quiescence,
                input_through=capture.normalized_at,
                now=capture.received_at,
            )
            session.current_work_id = work.work_id
            self._session_members[envelope.capture_id] = (session_id, session.generation)
            self._scheduled_captures.add(envelope.capture_id)

    def claim_ready_work(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_for: timedelta,
        limit: int = 1,
    ) -> list[WorkItem]:
        if not worker_id or limit < 1:
            return []
        with self._lock:
            ready = [
                item for item in self._work.values()
                if (item.state == "pending" and item.available_at <= now)
                or (item.state == "leased" and item.leased_until is not None and item.leased_until <= now)
            ]
            ready.sort(key=lambda item: (item.available_at, item.work_id))
            claimed: list[WorkItem] = []
            for item in ready[:limit]:
                item.state = "leased"
                item.lease_owner = worker_id
                item.leased_until = now + lease_for
                item.attempts += 1
                item.updated_at = now
                claimed.append(item)
            return claimed

    def complete_work(self, work_id: str, worker_id: str, *, now: datetime) -> bool:
        with self._lock:
            item = self._work.get(work_id)
            if item is None or item.state != "leased" or item.lease_owner != worker_id:
                return False
            item.state = "completed"
            item.lease_owner = None
            item.leased_until = None
            item.updated_at = now
            return True

    def retry_work(
        self,
        work_id: str,
        worker_id: str,
        *,
        now: datetime,
        retry_after: timedelta,
        error: str,
    ) -> bool:
        with self._lock:
            item = self._work.get(work_id)
            if item is None or item.state != "leased" or item.lease_owner != worker_id:
                return False
            item.state = "pending"
            item.available_at = now + retry_after
            item.lease_owner = None
            item.leased_until = None
            item.last_error = error[:1000]
            item.updated_at = now
            return True

    def captures_for_work(self, work: WorkItem) -> list[StoredCapture]:
        if work.work_type == "push_admission":
            capture = self._captures.get(work.scope_id)
            return [capture] if capture is not None else []
        captures = [
            capture
            for capture_id, capture in self._captures.items()
            if (
                (member := self._session_members.get(capture_id)) is not None
                and member[0] == work.scope_id
                and member[1] <= work.generation
            )
        ]
        return sorted(captures, key=lambda item: (item.normalized_at, item.envelope.capture_id))

    def get_salience_record(self, work_id: str) -> SalienceRecord | None:
        return self._salience.get(work_id)

    def put_salience_record(self, record: SalienceRecord) -> SalienceRecord:
        with self._lock:
            existing = self._salience.get(record.work_id)
            if existing is not None:
                return existing
            self._salience[record.work_id] = record
            return record

    def schedule_mining(self, record: SalienceRecord) -> MiningWorkItem | None:
        if record.status != "admitted":
            return None
        with self._lock:
            existing = next(
                (item for item in self._mining_work.values() if item.salience_id == record.salience_id),
                None,
            )
            if existing is not None:
                return existing
            item = MiningWorkItem(
                mining_work_id=_new_uuid7(),
                salience_id=record.salience_id,
                admission_work_id=record.work_id,
                state="pending",
                available_at=record.created_at,
                created_at=record.created_at,
                updated_at=record.created_at,
            )
            self._mining_work[item.mining_work_id] = item
            return item

    def backfill_mining_work(self) -> int:
        before = len(self._mining_work)
        for record in tuple(self._salience.values()):
            self.schedule_mining(record)
        created = len(self._mining_work) - before
        for run in self._mining_runs.values():
            if (
                run.status != "succeeded"
                or run.stage != "interpret_extract"
                or (
                    self.is_resolution_complete(run.run_id)
                    and self.is_claim_commit_complete(run.run_id)
                )
            ):
                continue
            work = next((
                item for item in self._mining_work.values() if item.salience_id == run.salience_id
            ), None)
            if work is not None and work.state == "completed":
                work.state = "pending"
                work.available_at = run.completed_at
                work.updated_at = run.completed_at
                work.last_error = None
                created += 1
        return created

    def claim_ready_mining_work(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_for: timedelta,
        limit: int = 1,
    ) -> list[MiningWorkItem]:
        if not worker_id or limit < 1:
            return []
        with self._lock:
            ready = [
                item for item in self._mining_work.values()
                if (item.state == "pending" and item.available_at <= now)
                or (item.state == "leased" and item.leased_until is not None and item.leased_until <= now)
            ]
            ready.sort(key=lambda item: (item.available_at, item.mining_work_id))
            for item in ready[:limit]:
                item.state = "leased"
                item.lease_owner = worker_id
                item.leased_until = now + lease_for
                item.attempts += 1
                item.updated_at = now
            return ready[:limit]

    def complete_mining_work(
        self, mining_work_id: str, worker_id: str, *, now: datetime
    ) -> bool:
        with self._lock:
            item = self._mining_work.get(mining_work_id)
            if item is None or item.state != "leased" or item.lease_owner != worker_id:
                return False
            item.state = "completed"
            item.lease_owner = None
            item.leased_until = None
            item.updated_at = now
            return True

    def retry_mining_work(
        self,
        mining_work_id: str,
        worker_id: str,
        *,
        now: datetime,
        retry_after: timedelta,
        error: str,
    ) -> bool:
        with self._lock:
            item = self._mining_work.get(mining_work_id)
            if item is None or item.state != "leased" or item.lease_owner != worker_id:
                return False
            item.state = "pending"
            item.available_at = now + retry_after
            item.lease_owner = None
            item.leased_until = None
            item.last_error = error[:1000]
            item.updated_at = now
            return True

    def get_mining_run(self, run_key: str) -> MiningRun | None:
        return self._mining_runs.get(run_key)

    def latest_successful_mining_run(
        self,
        scope_id: str,
        *,
        before_generation: int,
        stage: str,
        miner_version: str,
        config_fingerprint: str,
    ) -> MiningRun | None:
        compatible = [
            run for run in self._mining_runs.values()
            if run.scope_id == scope_id
            and run.generation < before_generation
            and run.stage == stage
            and run.miner_version == miner_version
            and run.config_fingerprint == config_fingerprint
            and run.status == "succeeded"
        ]
        return max(compatible, key=lambda run: (run.generation, run.completed_at), default=None)

    def put_mining_run(self, run: MiningRun) -> MiningRun:
        with self._lock:
            existing = self._mining_runs.get(run.run_key)
            if existing is not None:
                if existing.status == "succeeded" or run.status != "succeeded":
                    return existing
                run = replace(run, run_id=existing.run_id, created_at=existing.created_at)
            self._mining_runs[run.run_key] = run
            return run

    def list_mining_runs(self, *, limit: int = 50) -> list[MiningRun]:
        bounded = max(1, min(limit, 250))
        return sorted(
            self._mining_runs.values(),
            key=lambda run: (run.completed_at, run.run_id),
            reverse=True,
        )[:bounded]

    def get_resolved_mention(self, mention_key: str) -> ResolvedMention | None:
        return self._mentions.get(mention_key)

    def put_resolved_mention(self, mention: ResolvedMention) -> ResolvedMention:
        with self._lock:
            existing = self._mentions.get(mention.mention_key)
            if existing is not None:
                return existing
            self._mentions[mention.mention_key] = mention
            return mention

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def find_entity_by_surface(self, normalized_surface: str, entity_type: str) -> Entity | None:
        return next((
            entity for entity in self._entities.values()
            if entity.normalized_surface == normalized_surface and entity.entity_type == entity_type
        ), None)

    def put_entity(self, entity: Entity) -> Entity:
        with self._lock:
            existing_id = self._entity_by_key.get(entity.entity_key)
            if existing_id is not None:
                return self._entities[existing_id]
            self._entities[entity.entity_id] = entity
            self._entity_by_key[entity.entity_key] = entity.entity_id
            return entity

    def find_entity_candidates(
        self, embedding: list[float], entity_type: str, *, limit: int = 10
    ) -> list[EntityCandidate]:
        import math

        query_norm = math.sqrt(sum(value * value for value in embedding))
        best: dict[str, EntityCandidate] = {}
        if query_norm == 0:
            return []
        for mention in self._mentions.values():
            if mention.entity_type != entity_type or len(mention.embedding) != len(embedding):
                continue
            candidate_norm = math.sqrt(sum(value * value for value in mention.embedding))
            if candidate_norm == 0:
                continue
            score = sum(a * b for a, b in zip(embedding, mention.embedding)) / (
                query_norm * candidate_norm
            )
            entity = self._entities.get(mention.entity_id)
            if entity is None:
                continue
            candidate = EntityCandidate(entity, score, mention.surface)
            previous = best.get(entity.entity_id)
            if previous is None or candidate.vector_score > previous.vector_score:
                best[entity.entity_id] = candidate
        return sorted(best.values(), key=lambda item: item.vector_score, reverse=True)[:limit]

    def list_resolved_mentions(self, *, limit: int = 50) -> list[ResolvedMention]:
        bounded = max(1, min(limit, 250))
        return sorted(
            self._mentions.values(), key=lambda item: (item.created_at, item.mention_id), reverse=True
        )[:bounded]

    def list_entities(self, *, limit: int = 50) -> list[Entity]:
        bounded = max(1, min(limit, 250))
        return sorted(
            self._entities.values(), key=lambda item: (item.created_at, item.entity_id), reverse=True
        )[:bounded]

    def mark_resolution_complete(
        self, run_id: str, resolver_version: str, mention_count: int, *, completed_at: datetime
    ) -> None:
        with self._lock:
            self._resolution_complete.setdefault(run_id, {
                "resolver_version": resolver_version,
                "mention_count": mention_count,
                "completed_at": completed_at,
            })

    def is_resolution_complete(self, run_id: str) -> bool:
        return run_id in self._resolution_complete

    def resolved_mentions_for_run(self, run_id: str) -> list[ResolvedMention]:
        return sorted(
            (item for item in self._mentions.values() if item.run_id == run_id),
            key=lambda item: (item.created_at, item.mention_id),
        )

    def put_claim(self, claim: Claim) -> Claim:
        with self._lock:
            existing_id = self._claim_by_key.get(claim.claim_key)
            if existing_id is not None:
                return self._claims[existing_id]
            self._claims[claim.claim_id] = claim
            self._claim_by_key[claim.claim_key] = claim.claim_id
            return claim

    def put_evidence(self, evidence: Evidence) -> Evidence:
        with self._lock:
            existing_id = self._evidence_by_key.get(evidence.evidence_key)
            if existing_id is not None:
                return self._evidence[existing_id]
            self._evidence[evidence.evidence_id] = evidence
            self._evidence_by_key[evidence.evidence_key] = evidence.evidence_id
            return evidence

    def list_claims(self, *, limit: int = 50) -> list[Claim]:
        bounded = max(1, min(limit, 250))
        return sorted(
            self._claims.values(), key=lambda item: (item.recorded_at, item.claim_id), reverse=True
        )[:bounded]

    def list_evidence(self, *, limit: int = 50) -> list[Evidence]:
        bounded = max(1, min(limit, 250))
        return sorted(
            self._evidence.values(),
            key=lambda item: (item.recorded_at, item.evidence_id),
            reverse=True,
        )[:bounded]

    def mark_claim_commit_complete(
        self,
        run_id: str,
        committer_version: str,
        schema_version: str,
        claim_count: int,
        evidence_count: int,
        *,
        completed_at: datetime,
    ) -> None:
        with self._lock:
            self._claim_commit_complete.setdefault(run_id, {
                "committer_version": committer_version,
                "schema_version": schema_version,
                "claim_count": claim_count,
                "evidence_count": evidence_count,
                "completed_at": completed_at,
            })

    def is_claim_commit_complete(self, run_id: str) -> bool:
        return run_id in self._claim_commit_complete

    def put_maintenance_notice(self, notice: MaintenanceNotice) -> MaintenanceNotice:
        with self._lock:
            return self._maintenance_notices.setdefault(notice.notice_key, notice)

    def list_maintenance_notices(self, *, limit: int = 50) -> list[MaintenanceNotice]:
        bounded = max(1, min(limit, 250))
        return sorted(
            self._maintenance_notices.values(),
            key=lambda item: (item.created_at, item.notice_id), reverse=True,
        )[:bounded]

    def active_claims_for_subject_relation(self, subject_entity_id: str, relation: str) -> list[Claim]:
        return [
            item for item in self._claims.values()
            if item.subject_entity_id == subject_entity_id
            and item.relation == relation and item.lifecycle == "active"
        ]

    def get_mining_run_by_id(self, run_id: str) -> MiningRun | None:
        return next((item for item in self._mining_runs.values() if item.run_id == run_id), None)

    def request_remining(self, request: ReminingRequest) -> ReminingRequest:
        with self._lock:
            existing = next((item for item in self._remining_requests.values() if item.request_key == request.request_key), None)
            if existing is not None:
                return existing
            work = next((item for item in self._mining_work.values() if item.salience_id == request.salience_id), None)
            if work is None:
                raise ValueError("source run has no mining work")
            work.state = "pending"
            work.available_at = request.created_at
            work.updated_at = request.created_at
            work.lease_owner = None
            work.leased_until = None
            work.last_error = None
            self._remining_requests[request.request_id] = request
            return request

    def active_remining_for_salience(self, salience_id: str) -> ReminingRequest | None:
        pending = [item for item in self._remining_requests.values() if item.salience_id == salience_id and item.status == "pending"]
        return max(pending, key=lambda item: item.created_at, default=None)

    def complete_remining(
        self, request_id: str, replacement_run_id: str, replacement_claim_ids: list[str], *, now: datetime
    ) -> ReminingRequest:
        with self._lock:
            request = self._remining_requests[request_id]
            if request.status == "completed":
                return request
            replacement = set(replacement_claim_ids)
            affected_claims: set[str] = set()
            for evidence_id, evidence in tuple(self._evidence.items()):
                if evidence.run_id == request.source_run_id and evidence.claim_id not in replacement and evidence.lifecycle == "active":
                    self._evidence[evidence_id] = replace(evidence, lifecycle="superseded")
                    affected_claims.add(evidence.claim_id)
            for claim_id in affected_claims:
                if not any(item.claim_id == claim_id and item.lifecycle == "active" for item in self._evidence.values()):
                    self._claims[claim_id] = replace(self._claims[claim_id], lifecycle="superseded")
            completed = replace(request, status="completed", replacement_run_id=replacement_run_id, completed_at=now)
            self._remining_requests[request_id] = completed
            notice = MaintenanceNotice(
                notice_id=_new_uuid7(), notice_key=f"remining:{request_id}", kind="remining_applied",
                status="resolved", scope_id=request.scope_id, claim_id=None, run_id=replacement_run_id,
                details={"source_run_id": request.source_run_id, "replacement_run_id": replacement_run_id,
                         "superseded_claim_ids": sorted(affected_claims)}, created_at=now, resolved_at=now,
            )
            self._maintenance_notices.setdefault(notice.notice_key, notice)
            return completed

    def list_remining_requests(self, *, limit: int = 50) -> list[ReminingRequest]:
        bounded = max(1, min(limit, 250))
        return sorted(self._remining_requests.values(), key=lambda item: (item.created_at, item.request_id), reverse=True)[:bounded]

    def put_extension_relation(self, relation: ExtensionRelation) -> ExtensionRelation:
        with self._lock:
            return self._extension_relations.setdefault(relation.relation, relation)

    def get_extension_relation(self, relation: str) -> ExtensionRelation | None:
        return self._extension_relations.get(relation)

    def list_extension_relations(self, *, limit: int = 100) -> list[ExtensionRelation]:
        bounded = max(1, min(limit, 250))
        return sorted(self._extension_relations.values(), key=lambda item: (item.updated_at, item.relation), reverse=True)[:bounded]

    def update_extension_relation(self, relation: ExtensionRelation) -> ExtensionRelation:
        with self._lock:
            if relation.relation not in self._extension_relations:
                raise ValueError("extension relation is not registered")
            self._extension_relations[relation.relation] = relation
            return relation

    def supersede_claims_for_relation(self, relation: str) -> list[str]:
        with self._lock:
            affected = [item.claim_id for item in self._claims.values() if item.relation == relation and item.lifecycle == "active"]
            for claim_id in affected:
                self._claims[claim_id] = replace(self._claims[claim_id], lifecycle="superseded")
                for evidence_id, evidence in tuple(self._evidence.items()):
                    if evidence.claim_id == claim_id and evidence.lifecycle == "active":
                        self._evidence[evidence_id] = replace(evidence, lifecycle="superseded")
            return affected


class ArcadeCaptureStore(CaptureStore):
    """ArcadeDB document-store adapter for the append-only capture region."""

    def __init__(
        self, base_url: str, database: str, username: str, password: str,
        *, embedding_dimensions: int = 384,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.database = database
        self.command_url = f"{self.base_url}/api/v1/command/{database}"
        self.auth = (username, password)
        self.timeout = httpx.Timeout(15.0, connect=3.0)
        self.embedding_dimensions = embedding_dimensions

    def _command(self, command: str, params: dict | None = None) -> dict:
        response = httpx.post(
            self.command_url,
            auth=self.auth,
            timeout=self.timeout,
            json={"language": "sql", "command": command, "params": params or {}},
        )
        if response.is_error:
            raise RuntimeError(
                f"ArcadeDB command failed ({response.status_code}) for {command!r}: "
                f"{response.text[:1000]}"
            )
        return response.json()

    def bootstrap(self) -> None:
        exists = httpx.get(
            f"{self.base_url}/api/v1/exists/{self.database}",
            auth=self.auth,
            timeout=self.timeout,
        )
        exists.raise_for_status()
        if not bool(exists.json().get("result")):
            created = httpx.post(
                f"{self.base_url}/api/v1/server",
                auth=self.auth,
                timeout=self.timeout,
                json={"language": "sql", "command": f"CREATE DATABASE {self.database}"},
            )
            created.raise_for_status()
        statements = (
            "ALTER DATABASE `arcadedb.dateTimeFormat` 'yyyy-MM-dd HH:mm:ss.SSS'",
            "CREATE DOCUMENT TYPE V2Capture IF NOT EXISTS",
            "CREATE PROPERTY V2Capture.capture_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Capture.capture_class IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Capture.kind IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Capture.state IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Capture.surface IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Capture.session_ref IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Capture.occurred_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2Capture.received_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2Capture.normalized_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2Capture.envelope_json IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON V2Capture (capture_id) UNIQUE",
            "CREATE DOCUMENT TYPE V2InstanceConfig IF NOT EXISTS",
            "CREATE PROPERTY V2InstanceConfig.config_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2InstanceConfig.value_json IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON V2InstanceConfig (config_key) UNIQUE",
            "CREATE DOCUMENT TYPE V2Session IF NOT EXISTS",
            "CREATE PROPERTY V2Session.session_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Session.scope_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Session.user_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Session.surface IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Session.session_ref IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Session.first_normalized_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2Session.last_normalized_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2Session.capture_count IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2Session.generation IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2Session.current_work_id IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON V2Session (session_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Session (scope_key) UNIQUE",
            "CREATE DOCUMENT TYPE V2SessionCapture IF NOT EXISTS",
            "CREATE PROPERTY V2SessionCapture.capture_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SessionCapture.scope_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SessionCapture.normalized_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2SessionCapture.received_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2SessionCapture.generation IF NOT EXISTS INTEGER",
            "CREATE INDEX IF NOT EXISTS ON V2SessionCapture (capture_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2SessionCapture (scope_key) NOTUNIQUE",
            "CREATE DOCUMENT TYPE V2WorkItem IF NOT EXISTS",
            "CREATE PROPERTY V2WorkItem.work_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2WorkItem.work_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2WorkItem.work_type IF NOT EXISTS STRING",
            "CREATE PROPERTY V2WorkItem.scope_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2WorkItem.generation IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2WorkItem.state IF NOT EXISTS STRING",
            "CREATE PROPERTY V2WorkItem.available_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2WorkItem.input_through IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2WorkItem.created_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2WorkItem.updated_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2WorkItem.attempts IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2WorkItem.lease_owner IF NOT EXISTS STRING",
            "CREATE PROPERTY V2WorkItem.leased_until IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2WorkItem.last_error IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON V2WorkItem (work_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2WorkItem (work_key) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2WorkItem (state, available_at) NOTUNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2WorkItem (state, leased_until) NOTUNIQUE",
            "CREATE DOCUMENT TYPE V2SalienceRecord IF NOT EXISTS",
            "CREATE PROPERTY V2SalienceRecord.salience_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.work_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.work_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.work_type IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.scope_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.generation IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2SalienceRecord.status IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.reason IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.algorithm_version IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.input_fingerprint IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.signals_json IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.trimmed_json IF NOT EXISTS STRING",
            "CREATE PROPERTY V2SalienceRecord.created_at IF NOT EXISTS DATETIME",
            "CREATE INDEX IF NOT EXISTS ON V2SalienceRecord (salience_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2SalienceRecord (work_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2SalienceRecord (status, created_at) NOTUNIQUE",
            "CREATE DOCUMENT TYPE V2MiningWork IF NOT EXISTS",
            "CREATE PROPERTY V2MiningWork.mining_work_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningWork.salience_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningWork.admission_work_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningWork.state IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningWork.available_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2MiningWork.created_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2MiningWork.updated_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2MiningWork.attempts IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2MiningWork.lease_owner IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningWork.leased_until IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2MiningWork.last_error IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON V2MiningWork (mining_work_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2MiningWork (salience_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2MiningWork (state, available_at) NOTUNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2MiningWork (state, leased_until) NOTUNIQUE",
            "CREATE DOCUMENT TYPE V2MiningRun IF NOT EXISTS",
            "CREATE PROPERTY V2MiningRun.run_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.run_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.salience_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.scope_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.generation IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2MiningRun.stage IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.miner_version IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.config_fingerprint IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.input_fingerprint IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.parent_run_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.status IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.output_json IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.covered_capture_ids IF NOT EXISTS LIST OF STRING",
            "CREATE PROPERTY V2MiningRun.evidence_capture_ids IF NOT EXISTS LIST OF STRING",
            "CREATE PROPERTY V2MiningRun.error IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MiningRun.created_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2MiningRun.completed_at IF NOT EXISTS DATETIME",
            "CREATE INDEX IF NOT EXISTS ON V2MiningRun (run_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2MiningRun (run_key) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2MiningRun (scope_id, generation) NOTUNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2MiningRun (status, stage) NOTUNIQUE",
            "CREATE VERTEX TYPE V2Entity IF NOT EXISTS",
            "CREATE PROPERTY V2Entity.entity_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Entity.entity_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Entity.canonical_surface IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Entity.normalized_surface IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Entity.entity_type IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Entity.created_at IF NOT EXISTS DATETIME",
            "CREATE INDEX IF NOT EXISTS ON V2Entity (entity_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Entity (entity_key) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Entity (normalized_surface, entity_type) NOTUNIQUE",
            "CREATE DOCUMENT TYPE V2Mention IF NOT EXISTS",
            "CREATE PROPERTY V2Mention.mention_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.mention_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.run_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.capture_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.source_start IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2Mention.source_end IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2Mention.surface IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.normalized_surface IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.entity_type IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.context IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.embedding IF NOT EXISTS ARRAY_OF_FLOATS",
            "CREATE PROPERTY V2Mention.embedding_model IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.resolver_version IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.entity_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.resolution_method IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.vector_score IF NOT EXISTS DOUBLE",
            "CREATE PROPERTY V2Mention.string_score IF NOT EXISTS DOUBLE",
            "CREATE PROPERTY V2Mention.combined_score IF NOT EXISTS DOUBLE",
            "CREATE PROPERTY V2Mention.decision_json IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Mention.created_at IF NOT EXISTS DATETIME",
            "CREATE INDEX IF NOT EXISTS ON V2Mention (mention_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Mention (mention_key) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Mention (run_id) NOTUNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Mention (entity_id) NOTUNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Mention (entity_type) NOTUNIQUE",
            f"CREATE INDEX IF NOT EXISTS ON V2Mention (embedding) LSM_VECTOR METADATA "
            f"{{dimensions: {self.embedding_dimensions}, similarity: 'COSINE'}}",
            "CREATE DOCUMENT TYPE V2ResolutionRun IF NOT EXISTS",
            "CREATE PROPERTY V2ResolutionRun.run_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ResolutionRun.resolver_version IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ResolutionRun.mention_count IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2ResolutionRun.completed_at IF NOT EXISTS DATETIME",
            "CREATE INDEX IF NOT EXISTS ON V2ResolutionRun (run_id) UNIQUE",
            "CREATE VERTEX TYPE V2Claim IF NOT EXISTS",
            "CREATE PROPERTY V2Claim.claim_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.claim_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.subject_entity_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.relation IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.object_kind IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.object_entity_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.object_literal IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.object_literal_norm IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.valid_from IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2Claim.valid_to IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2Claim.recorded_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2Claim.statefulness IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.cardinality IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.schema_version IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.lifecycle IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Claim.first_run_id IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON V2Claim (claim_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Claim (claim_key) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Claim (subject_entity_id, relation) NOTUNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Claim (object_entity_id) NOTUNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Claim (lifecycle, recorded_at) NOTUNIQUE",
            "CREATE DOCUMENT TYPE V2Evidence IF NOT EXISTS",
            "CREATE PROPERTY V2Evidence.evidence_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Evidence.evidence_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Evidence.claim_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Evidence.capture_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Evidence.source_start IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2Evidence.source_end IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2Evidence.run_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Evidence.miner_version IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Evidence.schema_version IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Evidence.lifecycle IF NOT EXISTS STRING",
            "CREATE PROPERTY V2Evidence.recorded_at IF NOT EXISTS DATETIME",
            "CREATE INDEX IF NOT EXISTS ON V2Evidence (evidence_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Evidence (evidence_key) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Evidence (claim_id) NOTUNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Evidence (capture_id) NOTUNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2Evidence (run_id) NOTUNIQUE",
            "CREATE DOCUMENT TYPE V2ClaimCommitRun IF NOT EXISTS",
            "CREATE PROPERTY V2ClaimCommitRun.run_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ClaimCommitRun.committer_version IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ClaimCommitRun.schema_version IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ClaimCommitRun.claim_count IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2ClaimCommitRun.evidence_count IF NOT EXISTS INTEGER",
            "CREATE PROPERTY V2ClaimCommitRun.completed_at IF NOT EXISTS DATETIME",
            "CREATE INDEX IF NOT EXISTS ON V2ClaimCommitRun (run_id) UNIQUE",
            "CREATE DOCUMENT TYPE V2MaintenanceNotice IF NOT EXISTS",
            "CREATE PROPERTY V2MaintenanceNotice.notice_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MaintenanceNotice.notice_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MaintenanceNotice.kind IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MaintenanceNotice.status IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MaintenanceNotice.scope_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MaintenanceNotice.claim_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MaintenanceNotice.run_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MaintenanceNotice.details_json IF NOT EXISTS STRING",
            "CREATE PROPERTY V2MaintenanceNotice.created_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2MaintenanceNotice.resolved_at IF NOT EXISTS DATETIME",
            "CREATE INDEX IF NOT EXISTS ON V2MaintenanceNotice (notice_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2MaintenanceNotice (notice_key) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2MaintenanceNotice (status, created_at) NOTUNIQUE",
            "CREATE DOCUMENT TYPE V2ReminingRequest IF NOT EXISTS",
            "CREATE PROPERTY V2ReminingRequest.request_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ReminingRequest.request_key IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ReminingRequest.source_run_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ReminingRequest.salience_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ReminingRequest.scope_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ReminingRequest.reason IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ReminingRequest.reprocess_token IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ReminingRequest.status IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ReminingRequest.replacement_run_id IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ReminingRequest.created_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2ReminingRequest.completed_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2ReminingRequest.error IF NOT EXISTS STRING",
            "CREATE INDEX IF NOT EXISTS ON V2ReminingRequest (request_id) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2ReminingRequest (request_key) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2ReminingRequest (salience_id, status) NOTUNIQUE",
            "CREATE DOCUMENT TYPE V2ExtensionRelation IF NOT EXISTS",
            "CREATE PROPERTY V2ExtensionRelation.relation IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ExtensionRelation.definition_json IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ExtensionRelation.status IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ExtensionRelation.schema_version IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ExtensionRelation.promoted_relation IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ExtensionRelation.schema_commit IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ExtensionRelation.migration_plan_json IF NOT EXISTS STRING",
            "CREATE PROPERTY V2ExtensionRelation.created_at IF NOT EXISTS DATETIME",
            "CREATE PROPERTY V2ExtensionRelation.updated_at IF NOT EXISTS DATETIME",
            "CREATE INDEX IF NOT EXISTS ON V2ExtensionRelation (relation) UNIQUE",
            "CREATE INDEX IF NOT EXISTS ON V2ExtensionRelation (status, updated_at) NOTUNIQUE",
        )
        for statement in statements:
            self._command(statement)

    def put_if_absent(self, capture: StoredCapture) -> bool:
        envelope_json = capture.envelope.model_dump_json(by_alias=True)
        existing = self._command(
            "SELECT capture_id FROM V2Capture WHERE capture_id = :capture_id LIMIT 1",
            {"capture_id": capture.envelope.capture_id},
        ).get("result", [])
        if existing:
            return False
        try:
            self._command(
                "INSERT INTO V2Capture SET capture_id = :capture_id, capture_class = :capture_class, "
                "kind = :kind, state = :state, surface = :surface, session_ref = :session_ref, "
                "occurred_at = :occurred_at, received_at = :received_at, normalized_at = :normalized_at, "
                "envelope_json = :envelope_json",
                {
                    "capture_id": capture.envelope.capture_id,
                    "capture_class": capture.envelope.capture_class,
                    "kind": capture.envelope.kind.value,
                    "state": capture.state,
                    "surface": capture.envelope.surface,
                    "session_ref": capture.envelope.session_ref,
                    "occurred_at": _epoch_millis(capture.envelope.occurred_at),
                    "received_at": _epoch_millis(capture.received_at),
                    "normalized_at": _epoch_millis(capture.normalized_at),
                    "envelope_json": envelope_json,
                },
            )
        except RuntimeError:
            # A racing retry can lose after the preflight read; the unique
            # index remains authoritative.
            existing = self._command(
                "SELECT capture_id FROM V2Capture WHERE capture_id = :capture_id LIMIT 1",
                {"capture_id": capture.envelope.capture_id},
            ).get("result", [])
            if existing:
                return False
            raise
        return True

    def get_capture(self, capture_id: str) -> StoredCapture | None:
        rows = self._command(
            "SELECT envelope_json, received_at, normalized_at, state FROM V2Capture "
            "WHERE capture_id = :capture_id LIMIT 1",
            {"capture_id": capture_id},
        ).get("result", [])
        if not rows:
            return None
        row = rows[0]
        return StoredCapture(
            envelope=CaptureEnvelope.model_validate_json(row["envelope_json"]),
            received_at=self._datetime(row["received_at"]),
            normalized_at=self._datetime(row["normalized_at"]),
            state=row["state"],
        )

    def get_config(self, key: str) -> dict | None:
        rows = self._command(
            "SELECT value_json FROM V2InstanceConfig WHERE config_key = :config_key LIMIT 1",
            {"config_key": key},
        ).get("result", [])
        return json.loads(rows[0]["value_json"]) if rows else None

    def set_config(self, key: str, value: dict) -> None:
        encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
        rows = self._command(
            "SELECT config_key FROM V2InstanceConfig WHERE config_key = :config_key LIMIT 1",
            {"config_key": key},
        ).get("result", [])
        if rows:
            self._command(
                "UPDATE V2InstanceConfig SET value_json = :value_json WHERE config_key = :config_key",
                {"config_key": key, "value_json": encoded},
            )
        else:
            self._command(
                "INSERT INTO V2InstanceConfig SET config_key = :config_key, value_json = :value_json",
                {"config_key": key, "value_json": encoded},
            )

    @staticmethod
    def _datetime(value: datetime | str | int | float | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _work_from_row(cls, row: dict) -> WorkItem:
        return WorkItem(
            work_id=row["work_id"],
            work_key=row["work_key"],
            work_type=row["work_type"],
            scope_id=row["scope_id"],
            generation=int(row["generation"]),
            state=row["state"],
            available_at=cls._datetime(row["available_at"]),
            input_through=cls._datetime(row["input_through"]),
            created_at=cls._datetime(row["created_at"]),
            updated_at=cls._datetime(row["updated_at"]),
            attempts=int(row.get("attempts") or 0),
            lease_owner=row.get("lease_owner"),
            leased_until=cls._datetime(row.get("leased_until")),
            last_error=row.get("last_error"),
        )

    def _create_work(
        self,
        *,
        work_key: str,
        work_type: Literal["session_admission", "push_admission"],
        scope_id: str,
        generation: int,
        available_at: datetime,
        input_through: datetime,
        now: datetime,
    ) -> WorkItem:
        rows = self._command(
            "SELECT FROM V2WorkItem WHERE work_key = :work_key LIMIT 1",
            {"work_key": work_key},
        ).get("result", [])
        if rows:
            return self._work_from_row(rows[0])
        work = WorkItem(
            work_id=_new_uuid7(),
            work_key=work_key,
            work_type=work_type,
            scope_id=scope_id,
            generation=generation,
            state="pending",
            available_at=available_at,
            input_through=input_through,
            created_at=now,
            updated_at=now,
        )
        try:
            self._command(
                "INSERT INTO V2WorkItem SET work_id = :work_id, work_key = :work_key, "
                "work_type = :work_type, scope_id = :scope_id, generation = :generation, "
                "state = 'pending', available_at = :available_at, input_through = :input_through, "
                "created_at = :created_at, updated_at = :updated_at, attempts = 0",
                {
                    "work_id": work.work_id,
                    "work_key": work.work_key,
                    "work_type": work.work_type,
                    "scope_id": work.scope_id,
                    "generation": work.generation,
                    "available_at": _epoch_millis(work.available_at),
                    "input_through": _epoch_millis(work.input_through),
                    "created_at": _epoch_millis(work.created_at),
                    "updated_at": _epoch_millis(work.updated_at),
                },
            )
        except RuntimeError:
            rows = self._command(
                "SELECT FROM V2WorkItem WHERE work_key = :work_key LIMIT 1",
                {"work_key": work_key},
            ).get("result", [])
            if not rows:
                raise
            return self._work_from_row(rows[0])
        return work

    def schedule_capture(self, capture: StoredCapture, *, quiescence: timedelta) -> None:
        envelope = capture.envelope
        if envelope.capture_class.startswith("push."):
            self._create_work(
                work_key=f"push:{envelope.capture_id}",
                work_type="push_admission",
                scope_id=envelope.capture_id,
                generation=1,
                available_at=capture.received_at,
                input_through=capture.normalized_at,
                now=capture.received_at,
            )
            return
        if envelope.capture_class != "transcript.message":
            return

        scope_key = _session_scope_key(envelope)
        associations = self._command(
            "SELECT capture_id, generation FROM V2SessionCapture WHERE capture_id = :capture_id LIMIT 1",
            {"capture_id": envelope.capture_id},
        ).get("result", [])
        scheduled_generation = int(associations[0].get("generation") or 0) if associations else 0
        if not associations:
            try:
                self._command(
                    "INSERT INTO V2SessionCapture SET capture_id = :capture_id, scope_key = :scope_key, "
                    "normalized_at = :normalized_at, received_at = :received_at, generation = 0",
                    {
                        "capture_id": envelope.capture_id,
                        "scope_key": scope_key,
                        "normalized_at": _epoch_millis(capture.normalized_at),
                        "received_at": _epoch_millis(capture.received_at),
                    },
                )
            except RuntimeError:
                associations = self._command(
                    "SELECT capture_id, generation FROM V2SessionCapture "
                    "WHERE capture_id = :capture_id LIMIT 1",
                    {"capture_id": envelope.capture_id},
                ).get("result", [])
                if not associations:
                    raise
                scheduled_generation = int(associations[0].get("generation") or 0)
        if scheduled_generation > 0:
            return
        members = self._command(
            "SELECT normalized_at, received_at FROM V2SessionCapture WHERE scope_key = :scope_key",
            {"scope_key": scope_key},
        ).get("result", [])
        normalized_times = [self._datetime(row["normalized_at"]) for row in members]
        received_times = [self._datetime(row["received_at"]) for row in members]
        first_at = min(normalized_times)
        last_at = max(normalized_times)
        latest_received_at = max(received_times)
        capture_count = len(members)
        sessions = self._command(
            "SELECT FROM V2Session WHERE scope_key = :scope_key LIMIT 1",
            {"scope_key": scope_key},
        ).get("result", [])
        if not sessions:
            session_id = envelope.capture_id
            try:
                self._command(
                    "INSERT INTO V2Session SET session_id = :session_id, scope_key = :scope_key, "
                    "user_id = :user_id, surface = :surface, session_ref = :session_ref, "
                    "first_normalized_at = :normalized_at, last_normalized_at = :normalized_at, "
                    "capture_count = 1, generation = 1, current_work_id = ''",
                    {
                        "session_id": session_id,
                        "scope_key": scope_key,
                        "user_id": envelope.meta.user_id,
                        "surface": envelope.surface,
                        "session_ref": envelope.session_ref or f"capture:{envelope.capture_id}",
                        "normalized_at": _epoch_millis(first_at),
                    },
                )
            except RuntimeError:
                sessions = self._command(
                    "SELECT FROM V2Session WHERE scope_key = :scope_key LIMIT 1",
                    {"scope_key": scope_key},
                ).get("result", [])
                if not sessions:
                    raise
            else:
                work = self._create_work(
                    work_key=f"session:{session_id}:1",
                    work_type="session_admission",
                    scope_id=session_id,
                    generation=1,
                    available_at=latest_received_at + quiescence,
                    input_through=last_at,
                    now=latest_received_at,
                )
                self._command(
                    "UPDATE V2Session SET first_normalized_at = :first_normalized_at, "
                    "last_normalized_at = :last_normalized_at, capture_count = :capture_count, "
                    "current_work_id = :current_work_id "
                    "WHERE session_id = :session_id",
                    {
                        "first_normalized_at": _epoch_millis(first_at),
                        "last_normalized_at": _epoch_millis(last_at),
                        "capture_count": capture_count,
                        "current_work_id": work.work_id,
                        "session_id": session_id,
                    },
                )
                self._command(
                    "UPDATE V2SessionCapture SET generation = 1 WHERE capture_id = :capture_id",
                    {"capture_id": envelope.capture_id},
                )
                return

        session = sessions[0]
        session_id = session["session_id"]
        current_rows = self._command(
            "SELECT FROM V2WorkItem WHERE work_id = :work_id LIMIT 1",
            {"work_id": session["current_work_id"]},
        ).get("result", [])
        current = self._work_from_row(current_rows[0]) if current_rows else None
        if current is not None and current.state == "pending":
            self._command(
                "UPDATE V2WorkItem SET available_at = :available_at, input_through = :input_through, "
                "updated_at = :updated_at WHERE work_id = :work_id AND state = 'pending'",
                {
                    "available_at": _epoch_millis(latest_received_at + quiescence),
                    "input_through": _epoch_millis(last_at),
                    "updated_at": _epoch_millis(latest_received_at),
                    "work_id": current.work_id,
                },
            )
            work = current
            generation = int(session.get("generation") or 1)
        else:
            generation = (
                int(session.get("generation") or 1)
                if not session.get("current_work_id")
                else int(session.get("generation") or 1) + 1
            )
            work = self._create_work(
                work_key=f"session:{session_id}:{generation}",
                work_type="session_admission",
                scope_id=session_id,
                generation=generation,
                available_at=latest_received_at + quiescence,
                input_through=last_at,
                now=latest_received_at,
            )
        self._command(
            "UPDATE V2Session SET first_normalized_at = :first_normalized_at, "
            "last_normalized_at = :last_normalized_at, capture_count = :capture_count, "
            "generation = :generation, current_work_id = :current_work_id "
            "WHERE session_id = :session_id",
            {
                "first_normalized_at": _epoch_millis(first_at),
                "last_normalized_at": _epoch_millis(last_at),
                "capture_count": capture_count,
                "generation": generation,
                "current_work_id": work.work_id,
                "session_id": session_id,
            },
        )
        self._command(
            "UPDATE V2SessionCapture SET generation = :generation WHERE capture_id = :capture_id",
            {"generation": generation, "capture_id": envelope.capture_id},
        )

    def claim_ready_work(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_for: timedelta,
        limit: int = 1,
    ) -> list[WorkItem]:
        if not worker_id or limit < 1:
            return []
        limit = min(limit, 250)
        rows = self._command(
            "SELECT FROM V2WorkItem WHERE (state = 'pending' AND available_at <= :now) "
            "OR (state = 'leased' AND leased_until <= :now) "
            f"ORDER BY available_at, work_id LIMIT {limit}",
            {"now": _epoch_millis(now)},
        ).get("result", [])
        claimed: list[WorkItem] = []
        for row in rows:
            work_id = row["work_id"]
            updated = self._command(
                "UPDATE V2WorkItem SET state = 'leased', lease_owner = :worker_id, "
                "leased_until = :leased_until, attempts = attempts + 1, updated_at = :updated_at "
                "RETURN AFTER @this "
                "WHERE work_id = :work_id AND ((state = 'pending' AND available_at <= :now) "
                "OR (state = 'leased' AND leased_until <= :now))",
                {
                    "worker_id": worker_id,
                    "leased_until": _epoch_millis(now + lease_for),
                    "updated_at": _epoch_millis(now),
                    "work_id": work_id,
                    "now": _epoch_millis(now),
                },
            ).get("result", [])
            if updated and updated[0].get("lease_owner") == worker_id:
                claimed.append(self._work_from_row(updated[0]))
        return claimed

    def complete_work(self, work_id: str, worker_id: str, *, now: datetime) -> bool:
        updated = self._command(
            "UPDATE V2WorkItem SET state = 'completed', lease_owner = null, leased_until = null, "
            "updated_at = :updated_at RETURN AFTER @this "
            "WHERE work_id = :work_id AND state = 'leased' "
            "AND lease_owner = :worker_id",
            {"updated_at": _epoch_millis(now), "work_id": work_id, "worker_id": worker_id},
        ).get("result", [])
        return bool(updated and updated[0].get("state") == "completed")

    def retry_work(
        self,
        work_id: str,
        worker_id: str,
        *,
        now: datetime,
        retry_after: timedelta,
        error: str,
    ) -> bool:
        available_at = now + retry_after
        updated = self._command(
            "UPDATE V2WorkItem SET state = 'pending', available_at = :available_at, "
            "lease_owner = null, leased_until = null, last_error = :last_error, updated_at = :updated_at "
            "RETURN AFTER @this "
            "WHERE work_id = :work_id AND state = 'leased' AND lease_owner = :worker_id",
            {
                "available_at": _epoch_millis(available_at),
                "last_error": error[:1000],
                "updated_at": _epoch_millis(now),
                "work_id": work_id,
                "worker_id": worker_id,
            },
        ).get("result", [])
        return bool(
            updated
            and updated[0].get("state") == "pending"
            and self._datetime(updated[0].get("available_at")) == available_at
        )

    def captures_for_work(self, work: WorkItem) -> list[StoredCapture]:
        if work.work_type == "push_admission":
            capture = self.get_capture(work.scope_id)
            return [capture] if capture is not None else []
        sessions = self._command(
            "SELECT scope_key FROM V2Session WHERE session_id = :session_id LIMIT 1",
            {"session_id": work.scope_id},
        ).get("result", [])
        if not sessions:
            return []
        members = self._command(
            "SELECT capture_id FROM V2SessionCapture WHERE scope_key = :scope_key "
            "AND generation <= :generation ORDER BY normalized_at, capture_id",
            {"scope_key": sessions[0]["scope_key"], "generation": work.generation},
        ).get("result", [])
        captures = [self.get_capture(row["capture_id"]) for row in members]
        return [capture for capture in captures if capture is not None]

    @classmethod
    def _salience_from_row(cls, row: dict) -> SalienceRecord:
        return SalienceRecord(
            salience_id=row["salience_id"],
            work_id=row["work_id"],
            work_key=row["work_key"],
            work_type=row["work_type"],
            scope_id=row["scope_id"],
            generation=int(row["generation"]),
            status=row["status"],
            reason=row["reason"],
            algorithm_version=row["algorithm_version"],
            input_fingerprint=row["input_fingerprint"],
            signals=json.loads(row["signals_json"]),
            trimmed=json.loads(row["trimmed_json"]),
            created_at=cls._datetime(row["created_at"]),
        )

    def get_salience_record(self, work_id: str) -> SalienceRecord | None:
        rows = self._command(
            "SELECT FROM V2SalienceRecord WHERE work_id = :work_id LIMIT 1",
            {"work_id": work_id},
        ).get("result", [])
        return self._salience_from_row(rows[0]) if rows else None

    def put_salience_record(self, record: SalienceRecord) -> SalienceRecord:
        existing = self.get_salience_record(record.work_id)
        if existing is not None:
            return existing
        params = {
            "salience_id": record.salience_id,
            "work_id": record.work_id,
            "work_key": record.work_key,
            "work_type": record.work_type,
            "scope_id": record.scope_id,
            "generation": record.generation,
            "status": record.status,
            "reason": record.reason,
            "algorithm_version": record.algorithm_version,
            "input_fingerprint": record.input_fingerprint,
            "signals_json": json.dumps(record.signals, separators=(",", ":"), sort_keys=True),
            "trimmed_json": json.dumps(record.trimmed, separators=(",", ":"), sort_keys=True),
            "created_at": _epoch_millis(record.created_at),
        }
        try:
            self._command(
                "INSERT INTO V2SalienceRecord SET salience_id = :salience_id, work_id = :work_id, "
                "work_key = :work_key, work_type = :work_type, scope_id = :scope_id, "
                "generation = :generation, status = :status, reason = :reason, "
                "algorithm_version = :algorithm_version, input_fingerprint = :input_fingerprint, "
                "signals_json = :signals_json, trimmed_json = :trimmed_json, created_at = :created_at",
                params,
            )
        except RuntimeError:
            existing = self.get_salience_record(record.work_id)
            if existing is None:
                raise
            return existing
        return record

    @classmethod
    def _mining_work_from_row(cls, row: dict) -> MiningWorkItem:
        return MiningWorkItem(
            mining_work_id=row["mining_work_id"],
            salience_id=row["salience_id"],
            admission_work_id=row["admission_work_id"],
            state=row["state"],
            available_at=cls._datetime(row["available_at"]),
            created_at=cls._datetime(row["created_at"]),
            updated_at=cls._datetime(row["updated_at"]),
            attempts=int(row.get("attempts") or 0),
            lease_owner=row.get("lease_owner"),
            leased_until=cls._datetime(row.get("leased_until")),
            last_error=row.get("last_error"),
        )

    def schedule_mining(self, record: SalienceRecord) -> MiningWorkItem | None:
        if record.status != "admitted":
            return None
        rows = self._command(
            "SELECT FROM V2MiningWork WHERE salience_id = :salience_id LIMIT 1",
            {"salience_id": record.salience_id},
        ).get("result", [])
        if rows:
            return self._mining_work_from_row(rows[0])
        item = MiningWorkItem(
            mining_work_id=_new_uuid7(),
            salience_id=record.salience_id,
            admission_work_id=record.work_id,
            state="pending",
            available_at=record.created_at,
            created_at=record.created_at,
            updated_at=record.created_at,
        )
        try:
            self._command(
                "INSERT INTO V2MiningWork SET mining_work_id = :mining_work_id, "
                "salience_id = :salience_id, admission_work_id = :admission_work_id, "
                "state = 'pending', available_at = :available_at, created_at = :created_at, "
                "updated_at = :updated_at, attempts = 0",
                {
                    "mining_work_id": item.mining_work_id,
                    "salience_id": item.salience_id,
                    "admission_work_id": item.admission_work_id,
                    "available_at": _epoch_millis(item.available_at),
                    "created_at": _epoch_millis(item.created_at),
                    "updated_at": _epoch_millis(item.updated_at),
                },
            )
        except RuntimeError:
            rows = self._command(
                "SELECT FROM V2MiningWork WHERE salience_id = :salience_id LIMIT 1",
                {"salience_id": record.salience_id},
            ).get("result", [])
            if not rows:
                raise
            return self._mining_work_from_row(rows[0])
        return item

    def backfill_mining_work(self) -> int:
        rows = self._command(
            "SELECT FROM V2SalienceRecord WHERE status = 'admitted' ORDER BY created_at"
        ).get("result", [])
        created = 0
        for row in rows:
            record = self._salience_from_row(row)
            existed = self._command(
                "SELECT mining_work_id FROM V2MiningWork WHERE salience_id = :salience_id LIMIT 1",
                {"salience_id": record.salience_id},
            ).get("result", [])
            self.schedule_mining(record)
            if not existed:
                created += 1
        unresolved = self._command(
            "SELECT run_id, salience_id, completed_at FROM V2MiningRun "
            "WHERE status = 'succeeded' AND stage = 'interpret_extract' ORDER BY completed_at"
        ).get("result", [])
        for run in unresolved:
            if (
                self.is_resolution_complete(run["run_id"])
                and self.is_claim_commit_complete(run["run_id"])
            ):
                continue
            reopened = self._command(
                "UPDATE V2MiningWork SET state = 'pending', available_at = :available_at, "
                "lease_owner = null, leased_until = null, last_error = null, updated_at = :updated_at "
                "RETURN AFTER @this WHERE salience_id = :salience_id AND state = 'completed'",
                {
                    "available_at": _epoch_millis(self._datetime(run["completed_at"])),
                    "updated_at": _epoch_millis(self._datetime(run["completed_at"])),
                    "salience_id": run["salience_id"],
                },
            ).get("result", [])
            if reopened:
                created += 1
        pending_remining = self._command(
            "SELECT salience_id, created_at FROM V2ReminingRequest WHERE status = 'pending'"
        ).get("result", [])
        for request in pending_remining:
            reopened = self._command(
                "UPDATE V2MiningWork SET state = 'pending', available_at = :available_at, "
                "lease_owner = null, leased_until = null, last_error = null, updated_at = :available_at "
                "RETURN AFTER @this WHERE salience_id = :salience_id AND state = 'completed'",
                {"available_at": _epoch_millis(self._datetime(request["created_at"])),
                 "salience_id": request["salience_id"]},
            ).get("result", [])
            if reopened:
                created += 1
        return created

    def claim_ready_mining_work(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_for: timedelta,
        limit: int = 1,
    ) -> list[MiningWorkItem]:
        if not worker_id or limit < 1:
            return []
        limit = min(limit, 250)
        rows = self._command(
            "SELECT FROM V2MiningWork WHERE (state = 'pending' AND available_at <= :now) "
            "OR (state = 'leased' AND leased_until <= :now) "
            f"ORDER BY available_at, mining_work_id LIMIT {limit}",
            {"now": _epoch_millis(now)},
        ).get("result", [])
        claimed: list[MiningWorkItem] = []
        for row in rows:
            updated = self._command(
                "UPDATE V2MiningWork SET state = 'leased', lease_owner = :worker_id, "
                "leased_until = :leased_until, attempts = attempts + 1, updated_at = :updated_at "
                "RETURN AFTER @this WHERE mining_work_id = :mining_work_id AND "
                "((state = 'pending' AND available_at <= :now) OR "
                "(state = 'leased' AND leased_until <= :now))",
                {
                    "worker_id": worker_id,
                    "leased_until": _epoch_millis(now + lease_for),
                    "updated_at": _epoch_millis(now),
                    "mining_work_id": row["mining_work_id"],
                    "now": _epoch_millis(now),
                },
            ).get("result", [])
            if updated and updated[0].get("lease_owner") == worker_id:
                claimed.append(self._mining_work_from_row(updated[0]))
        return claimed

    def complete_mining_work(
        self, mining_work_id: str, worker_id: str, *, now: datetime
    ) -> bool:
        rows = self._command(
            "UPDATE V2MiningWork SET state = 'completed', lease_owner = null, "
            "leased_until = null, updated_at = :updated_at RETURN AFTER @this "
            "WHERE mining_work_id = :mining_work_id AND state = 'leased' "
            "AND lease_owner = :worker_id",
            {
                "updated_at": _epoch_millis(now),
                "mining_work_id": mining_work_id,
                "worker_id": worker_id,
            },
        ).get("result", [])
        return bool(rows and rows[0].get("state") == "completed")

    def retry_mining_work(
        self,
        mining_work_id: str,
        worker_id: str,
        *,
        now: datetime,
        retry_after: timedelta,
        error: str,
    ) -> bool:
        available_at = now + retry_after
        rows = self._command(
            "UPDATE V2MiningWork SET state = 'pending', available_at = :available_at, "
            "lease_owner = null, leased_until = null, last_error = :last_error, "
            "updated_at = :updated_at RETURN AFTER @this "
            "WHERE mining_work_id = :mining_work_id AND state = 'leased' "
            "AND lease_owner = :worker_id",
            {
                "available_at": _epoch_millis(available_at),
                "last_error": error[:1000],
                "updated_at": _epoch_millis(now),
                "mining_work_id": mining_work_id,
                "worker_id": worker_id,
            },
        ).get("result", [])
        return bool(rows and rows[0].get("state") == "pending")

    @classmethod
    def _mining_run_from_row(cls, row: dict) -> MiningRun:
        return MiningRun(
            run_id=row["run_id"],
            run_key=row["run_key"],
            salience_id=row["salience_id"],
            scope_id=row["scope_id"],
            generation=int(row["generation"]),
            stage=row["stage"],
            miner_version=row["miner_version"],
            config_fingerprint=row["config_fingerprint"],
            input_fingerprint=row["input_fingerprint"],
            parent_run_id=row.get("parent_run_id"),
            status=row["status"],
            output=json.loads(row.get("output_json") or "{}"),
            covered_capture_ids=list(row.get("covered_capture_ids") or []),
            evidence_capture_ids=list(row.get("evidence_capture_ids") or []),
            error=row.get("error"),
            created_at=cls._datetime(row["created_at"]),
            completed_at=cls._datetime(row["completed_at"]),
        )

    def get_mining_run(self, run_key: str) -> MiningRun | None:
        rows = self._command(
            "SELECT FROM V2MiningRun WHERE run_key = :run_key LIMIT 1",
            {"run_key": run_key},
        ).get("result", [])
        return self._mining_run_from_row(rows[0]) if rows else None

    def latest_successful_mining_run(
        self,
        scope_id: str,
        *,
        before_generation: int,
        stage: str,
        miner_version: str,
        config_fingerprint: str,
    ) -> MiningRun | None:
        rows = self._command(
            "SELECT FROM V2MiningRun WHERE scope_id = :scope_id AND generation < :generation "
            "AND stage = :stage AND miner_version = :miner_version "
            "AND config_fingerprint = :config_fingerprint AND status = 'succeeded' "
            "ORDER BY generation DESC, completed_at DESC LIMIT 1",
            {
                "scope_id": scope_id,
                "generation": before_generation,
                "stage": stage,
                "miner_version": miner_version,
                "config_fingerprint": config_fingerprint,
            },
        ).get("result", [])
        return self._mining_run_from_row(rows[0]) if rows else None

    def put_mining_run(self, run: MiningRun) -> MiningRun:
        existing = self.get_mining_run(run.run_key)
        if existing is not None:
            if existing.status == "succeeded" or run.status != "succeeded":
                return existing
            run = replace(run, run_id=existing.run_id, created_at=existing.created_at)
        params = {
            "run_id": run.run_id,
            "run_key": run.run_key,
            "salience_id": run.salience_id,
            "scope_id": run.scope_id,
            "generation": run.generation,
            "stage": run.stage,
            "miner_version": run.miner_version,
            "config_fingerprint": run.config_fingerprint,
            "input_fingerprint": run.input_fingerprint,
            "parent_run_id": run.parent_run_id,
            "status": run.status,
            "output_json": json.dumps(run.output, separators=(",", ":"), sort_keys=True),
            "covered_capture_ids": run.covered_capture_ids,
            "evidence_capture_ids": run.evidence_capture_ids,
            "error": run.error,
            "created_at": _epoch_millis(run.created_at),
            "completed_at": _epoch_millis(run.completed_at),
        }
        if existing is not None:
            self._command(
                "UPDATE V2MiningRun SET status = :status, output_json = :output_json, "
                "covered_capture_ids = :covered_capture_ids, "
                "evidence_capture_ids = :evidence_capture_ids, error = :error, "
                "completed_at = :completed_at WHERE run_key = :run_key",
                params,
            )
            return run
        try:
            self._command(
                "INSERT INTO V2MiningRun SET run_id = :run_id, run_key = :run_key, "
                "salience_id = :salience_id, scope_id = :scope_id, generation = :generation, "
                "stage = :stage, miner_version = :miner_version, "
                "config_fingerprint = :config_fingerprint, input_fingerprint = :input_fingerprint, "
                "parent_run_id = :parent_run_id, status = :status, output_json = :output_json, "
                "covered_capture_ids = :covered_capture_ids, "
                "evidence_capture_ids = :evidence_capture_ids, error = :error, "
                "created_at = :created_at, completed_at = :completed_at",
                params,
            )
        except RuntimeError:
            existing = self.get_mining_run(run.run_key)
            if existing is None:
                raise
            return existing
        return run

    def list_mining_runs(self, *, limit: int = 50) -> list[MiningRun]:
        bounded = max(1, min(limit, 250))
        rows = self._command(
            f"SELECT FROM V2MiningRun ORDER BY completed_at DESC, run_id DESC LIMIT {bounded}"
        ).get("result", [])
        return [self._mining_run_from_row(row) for row in rows]

    @classmethod
    def _entity_from_row(cls, row: dict) -> Entity:
        return Entity(
            entity_id=row["entity_id"],
            entity_key=row["entity_key"],
            canonical_surface=row["canonical_surface"],
            normalized_surface=row["normalized_surface"],
            entity_type=row["entity_type"],
            created_at=cls._datetime(row["created_at"]),
        )

    @classmethod
    def _mention_from_row(cls, row: dict) -> ResolvedMention:
        return ResolvedMention(
            mention_id=row["mention_id"],
            mention_key=row["mention_key"],
            run_id=row["run_id"],
            capture_id=row["capture_id"],
            source_start=int(row["source_start"]),
            source_end=int(row["source_end"]),
            surface=row["surface"],
            normalized_surface=row["normalized_surface"],
            entity_type=row["entity_type"],
            context=row["context"],
            embedding=[float(value) for value in row.get("embedding") or []],
            embedding_model=row["embedding_model"],
            resolver_version=row["resolver_version"],
            entity_id=row["entity_id"],
            resolution_method=row["resolution_method"],
            vector_score=float(row["vector_score"]) if row.get("vector_score") is not None else None,
            string_score=float(row["string_score"]) if row.get("string_score") is not None else None,
            combined_score=float(row["combined_score"]) if row.get("combined_score") is not None else None,
            decision_details=json.loads(row.get("decision_json") or "{}"),
            created_at=cls._datetime(row["created_at"]),
        )

    def get_resolved_mention(self, mention_key: str) -> ResolvedMention | None:
        rows = self._command(
            "SELECT FROM V2Mention WHERE mention_key = :mention_key LIMIT 1",
            {"mention_key": mention_key},
        ).get("result", [])
        return self._mention_from_row(rows[0]) if rows else None

    def put_resolved_mention(self, mention: ResolvedMention) -> ResolvedMention:
        existing = self.get_resolved_mention(mention.mention_key)
        if existing is not None:
            return existing
        params = {
            **mention.__dict__,
            "decision_json": json.dumps(
                mention.decision_details, separators=(",", ":"), sort_keys=True
            ),
            "created_at": _epoch_millis(mention.created_at),
        }
        try:
            self._command(
                "INSERT INTO V2Mention SET mention_id = :mention_id, mention_key = :mention_key, "
                "run_id = :run_id, capture_id = :capture_id, source_start = :source_start, "
                "source_end = :source_end, surface = :surface, normalized_surface = :normalized_surface, "
                "entity_type = :entity_type, context = :context, embedding = :embedding, "
                "embedding_model = :embedding_model, resolver_version = :resolver_version, "
                "entity_id = :entity_id, "
                "resolution_method = :resolution_method, vector_score = :vector_score, "
                "string_score = :string_score, combined_score = :combined_score, "
                "decision_json = :decision_json, "
                "created_at = :created_at",
                params,
            )
        except RuntimeError:
            existing = self.get_resolved_mention(mention.mention_key)
            if existing is None:
                raise
            return existing
        return mention

    def get_entity(self, entity_id: str) -> Entity | None:
        rows = self._command(
            "SELECT FROM V2Entity WHERE entity_id = :entity_id LIMIT 1",
            {"entity_id": entity_id},
        ).get("result", [])
        return self._entity_from_row(rows[0]) if rows else None

    def find_entity_by_surface(self, normalized_surface: str, entity_type: str) -> Entity | None:
        rows = self._command(
            "SELECT FROM V2Entity WHERE normalized_surface = :normalized_surface "
            "AND entity_type = :entity_type ORDER BY created_at LIMIT 1",
            {"normalized_surface": normalized_surface, "entity_type": entity_type},
        ).get("result", [])
        return self._entity_from_row(rows[0]) if rows else None

    def put_entity(self, entity: Entity) -> Entity:
        rows = self._command(
            "SELECT FROM V2Entity WHERE entity_key = :entity_key LIMIT 1",
            {"entity_key": entity.entity_key},
        ).get("result", [])
        if rows:
            return self._entity_from_row(rows[0])
        try:
            self._command(
                "INSERT INTO V2Entity SET entity_id = :entity_id, entity_key = :entity_key, "
                "canonical_surface = :canonical_surface, normalized_surface = :normalized_surface, "
                "entity_type = :entity_type, created_at = :created_at",
                {
                    **entity.__dict__,
                    "created_at": _epoch_millis(entity.created_at),
                },
            )
        except RuntimeError:
            rows = self._command(
                "SELECT FROM V2Entity WHERE entity_key = :entity_key LIMIT 1",
                {"entity_key": entity.entity_key},
            ).get("result", [])
            if not rows:
                raise
            return self._entity_from_row(rows[0])
        return entity

    def find_entity_candidates(
        self, embedding: list[float], entity_type: str, *, limit: int = 10
    ) -> list[EntityCandidate]:
        if len(embedding) != self.embedding_dimensions:
            raise ValueError(
                f"embedding has {len(embedding)} dimensions; expected {self.embedding_dimensions}"
            )
        search_limit = min(max(limit * 5, 20), 250)
        rows = self._command(
            # ArcadeDB 26.3.x registers the compatibility alias without a
            # namespace; newer releases also expose vector.neighbors().
            "SELECT expand(vectorNeighbors('V2Mention[embedding]', :embedding, :limit))",
            {"embedding": embedding, "limit": search_limit},
        ).get("result", [])
        candidates: dict[str, EntityCandidate] = {}
        for row in rows:
            if row.get("entity_type") != entity_type:
                continue
            entity = self.get_entity(row.get("entity_id", ""))
            if entity is None:
                continue
            distance = float(row.get("distance") or 0.0)
            candidate = EntityCandidate(
                entity=entity,
                vector_score=max(-1.0, min(1.0, 1.0 - distance)),
                mention_surface=row.get("surface") or entity.canonical_surface,
            )
            existing = candidates.get(entity.entity_id)
            if existing is None or candidate.vector_score > existing.vector_score:
                candidates[entity.entity_id] = candidate
        return sorted(
            candidates.values(), key=lambda item: item.vector_score, reverse=True
        )[:max(1, min(limit, 50))]

    def list_resolved_mentions(self, *, limit: int = 50) -> list[ResolvedMention]:
        bounded = max(1, min(limit, 250))
        rows = self._command(
            f"SELECT FROM V2Mention ORDER BY created_at DESC, mention_id DESC LIMIT {bounded}"
        ).get("result", [])
        return [self._mention_from_row(row) for row in rows]

    def list_entities(self, *, limit: int = 50) -> list[Entity]:
        bounded = max(1, min(limit, 250))
        rows = self._command(
            f"SELECT FROM V2Entity ORDER BY created_at DESC, entity_id DESC LIMIT {bounded}"
        ).get("result", [])
        return [self._entity_from_row(row) for row in rows]

    def mark_resolution_complete(
        self, run_id: str, resolver_version: str, mention_count: int, *, completed_at: datetime
    ) -> None:
        if self.is_resolution_complete(run_id):
            return
        try:
            self._command(
                "INSERT INTO V2ResolutionRun SET run_id = :run_id, "
                "resolver_version = :resolver_version, mention_count = :mention_count, "
                "completed_at = :completed_at",
                {
                    "run_id": run_id,
                    "resolver_version": resolver_version,
                    "mention_count": mention_count,
                    "completed_at": _epoch_millis(completed_at),
                },
            )
        except RuntimeError:
            if not self.is_resolution_complete(run_id):
                raise

    def is_resolution_complete(self, run_id: str) -> bool:
        rows = self._command(
            "SELECT run_id FROM V2ResolutionRun WHERE run_id = :run_id LIMIT 1",
            {"run_id": run_id},
        ).get("result", [])
        return bool(rows)

    def resolved_mentions_for_run(self, run_id: str) -> list[ResolvedMention]:
        rows = self._command(
            "SELECT FROM V2Mention WHERE run_id = :run_id ORDER BY created_at, mention_id",
            {"run_id": run_id},
        ).get("result", [])
        return [self._mention_from_row(row) for row in rows]

    @classmethod
    def _claim_from_row(cls, row: dict) -> Claim:
        return Claim(
            claim_id=row["claim_id"],
            claim_key=row["claim_key"],
            subject_entity_id=row["subject_entity_id"],
            relation=row["relation"],
            object_kind=row["object_kind"],
            object_entity_id=row.get("object_entity_id"),
            object_literal=row.get("object_literal"),
            object_literal_norm=row.get("object_literal_norm"),
            valid_from=cls._datetime(row.get("valid_from")),
            valid_to=cls._datetime(row.get("valid_to")),
            recorded_at=cls._datetime(row["recorded_at"]),
            statefulness=row["statefulness"],
            cardinality=row["cardinality"],
            schema_version=row["schema_version"],
            lifecycle=row["lifecycle"],
            first_run_id=row["first_run_id"],
        )

    @classmethod
    def _evidence_from_row(cls, row: dict) -> Evidence:
        return Evidence(
            evidence_id=row["evidence_id"],
            evidence_key=row["evidence_key"],
            claim_id=row["claim_id"],
            capture_id=row["capture_id"],
            source_start=int(row["source_start"]),
            source_end=int(row["source_end"]),
            run_id=row["run_id"],
            miner_version=row["miner_version"],
            schema_version=row["schema_version"],
            lifecycle=row["lifecycle"],
            recorded_at=cls._datetime(row["recorded_at"]),
        )

    def put_claim(self, claim: Claim) -> Claim:
        rows = self._command(
            "SELECT FROM V2Claim WHERE claim_key = :claim_key LIMIT 1",
            {"claim_key": claim.claim_key},
        ).get("result", [])
        if rows:
            return self._claim_from_row(rows[0])
        params = {
            **claim.__dict__,
            "valid_from": _epoch_millis(claim.valid_from) if claim.valid_from else None,
            "valid_to": _epoch_millis(claim.valid_to) if claim.valid_to else None,
            "recorded_at": _epoch_millis(claim.recorded_at),
        }
        try:
            self._command(
                "INSERT INTO V2Claim SET claim_id = :claim_id, claim_key = :claim_key, "
                "subject_entity_id = :subject_entity_id, relation = :relation, "
                "object_kind = :object_kind, object_entity_id = :object_entity_id, "
                "object_literal = :object_literal, object_literal_norm = :object_literal_norm, "
                "valid_from = :valid_from, valid_to = :valid_to, recorded_at = :recorded_at, "
                "statefulness = :statefulness, cardinality = :cardinality, "
                "schema_version = :schema_version, lifecycle = :lifecycle, "
                "first_run_id = :first_run_id",
                params,
            )
        except RuntimeError:
            rows = self._command(
                "SELECT FROM V2Claim WHERE claim_key = :claim_key LIMIT 1",
                {"claim_key": claim.claim_key},
            ).get("result", [])
            if not rows:
                raise
            return self._claim_from_row(rows[0])
        return claim

    def put_evidence(self, evidence: Evidence) -> Evidence:
        rows = self._command(
            "SELECT FROM V2Evidence WHERE evidence_key = :evidence_key LIMIT 1",
            {"evidence_key": evidence.evidence_key},
        ).get("result", [])
        if rows:
            return self._evidence_from_row(rows[0])
        params = {**evidence.__dict__, "recorded_at": _epoch_millis(evidence.recorded_at)}
        try:
            self._command(
                "INSERT INTO V2Evidence SET evidence_id = :evidence_id, "
                "evidence_key = :evidence_key, claim_id = :claim_id, capture_id = :capture_id, "
                "source_start = :source_start, source_end = :source_end, run_id = :run_id, "
                "miner_version = :miner_version, schema_version = :schema_version, "
                "lifecycle = :lifecycle, recorded_at = :recorded_at",
                params,
            )
        except RuntimeError:
            rows = self._command(
                "SELECT FROM V2Evidence WHERE evidence_key = :evidence_key LIMIT 1",
                {"evidence_key": evidence.evidence_key},
            ).get("result", [])
            if not rows:
                raise
            return self._evidence_from_row(rows[0])
        return evidence

    def list_claims(self, *, limit: int = 50) -> list[Claim]:
        bounded = max(1, min(limit, 250))
        rows = self._command(
            f"SELECT FROM V2Claim ORDER BY recorded_at DESC, claim_id DESC LIMIT {bounded}"
        ).get("result", [])
        return [self._claim_from_row(row) for row in rows]

    def list_evidence(self, *, limit: int = 50) -> list[Evidence]:
        bounded = max(1, min(limit, 250))
        rows = self._command(
            f"SELECT FROM V2Evidence ORDER BY recorded_at DESC, evidence_id DESC LIMIT {bounded}"
        ).get("result", [])
        return [self._evidence_from_row(row) for row in rows]

    def mark_claim_commit_complete(
        self,
        run_id: str,
        committer_version: str,
        schema_version: str,
        claim_count: int,
        evidence_count: int,
        *,
        completed_at: datetime,
    ) -> None:
        if self.is_claim_commit_complete(run_id):
            return
        try:
            self._command(
                "INSERT INTO V2ClaimCommitRun SET run_id = :run_id, "
                "committer_version = :committer_version, schema_version = :schema_version, "
                "claim_count = :claim_count, evidence_count = :evidence_count, "
                "completed_at = :completed_at",
                {
                    "run_id": run_id,
                    "committer_version": committer_version,
                    "schema_version": schema_version,
                    "claim_count": claim_count,
                    "evidence_count": evidence_count,
                    "completed_at": _epoch_millis(completed_at),
                },
            )
        except RuntimeError:
            if not self.is_claim_commit_complete(run_id):
                raise

    def is_claim_commit_complete(self, run_id: str) -> bool:
        rows = self._command(
            "SELECT run_id FROM V2ClaimCommitRun WHERE run_id = :run_id LIMIT 1",
            {"run_id": run_id},
        ).get("result", [])
        return bool(rows)

    @classmethod
    def _notice_from_row(cls, row: dict) -> MaintenanceNotice:
        return MaintenanceNotice(
            notice_id=row["notice_id"], notice_key=row["notice_key"], kind=row["kind"],
            status=row["status"], scope_id=row["scope_id"], claim_id=row.get("claim_id"),
            run_id=row.get("run_id"), details=json.loads(row.get("details_json") or "{}"),
            created_at=cls._datetime(row["created_at"]),
            resolved_at=cls._datetime(row["resolved_at"]) if row.get("resolved_at") else None,
        )

    def put_maintenance_notice(self, notice: MaintenanceNotice) -> MaintenanceNotice:
        rows = self._command(
            "SELECT FROM V2MaintenanceNotice WHERE notice_key = :notice_key LIMIT 1",
            {"notice_key": notice.notice_key},
        ).get("result", [])
        if rows:
            return self._notice_from_row(rows[0])
        self._command(
            "INSERT INTO V2MaintenanceNotice SET notice_id = :notice_id, notice_key = :notice_key, "
            "kind = :kind, status = :status, scope_id = :scope_id, claim_id = :claim_id, "
            "run_id = :run_id, details_json = :details_json, created_at = :created_at, resolved_at = :resolved_at",
            {**notice.__dict__, "details_json": json.dumps(notice.details, separators=(",", ":"), sort_keys=True),
             "created_at": _epoch_millis(notice.created_at),
             "resolved_at": _epoch_millis(notice.resolved_at) if notice.resolved_at else None},
        )
        return notice

    def list_maintenance_notices(self, *, limit: int = 50) -> list[MaintenanceNotice]:
        bounded = max(1, min(limit, 250))
        rows = self._command(
            f"SELECT FROM V2MaintenanceNotice ORDER BY created_at DESC, notice_id DESC LIMIT {bounded}"
        ).get("result", [])
        return [self._notice_from_row(row) for row in rows]

    def active_claims_for_subject_relation(self, subject_entity_id: str, relation: str) -> list[Claim]:
        rows = self._command(
            "SELECT FROM V2Claim WHERE subject_entity_id = :subject_entity_id "
            "AND relation = :relation AND lifecycle = 'active'",
            {"subject_entity_id": subject_entity_id, "relation": relation},
        ).get("result", [])
        return [self._claim_from_row(row) for row in rows]

    def get_mining_run_by_id(self, run_id: str) -> MiningRun | None:
        rows = self._command(
            "SELECT FROM V2MiningRun WHERE run_id = :run_id LIMIT 1", {"run_id": run_id}
        ).get("result", [])
        return self._mining_run_from_row(rows[0]) if rows else None

    @classmethod
    def _remining_from_row(cls, row: dict) -> ReminingRequest:
        return ReminingRequest(
            request_id=row["request_id"], request_key=row["request_key"],
            source_run_id=row["source_run_id"], salience_id=row["salience_id"],
            scope_id=row["scope_id"], reason=row["reason"], reprocess_token=row["reprocess_token"],
            status=row["status"], replacement_run_id=row.get("replacement_run_id"),
            created_at=cls._datetime(row["created_at"]),
            completed_at=cls._datetime(row["completed_at"]) if row.get("completed_at") else None,
            error=row.get("error"),
        )

    def request_remining(self, request: ReminingRequest) -> ReminingRequest:
        rows = self._command(
            "SELECT FROM V2ReminingRequest WHERE request_key = :request_key LIMIT 1",
            {"request_key": request.request_key},
        ).get("result", [])
        if rows:
            existing = self._remining_from_row(rows[0])
            if existing.status == "pending":
                self._command(
                    "UPDATE V2MiningWork SET state = 'pending', available_at = :now, updated_at = :now, "
                    "lease_owner = NULL, leased_until = NULL, last_error = NULL "
                    "WHERE salience_id = :salience_id AND state = 'completed'",
                    {"now": _epoch_millis(existing.created_at), "salience_id": existing.salience_id},
                )
            return existing
        work = self._command(
            "SELECT mining_work_id FROM V2MiningWork WHERE salience_id = :salience_id LIMIT 1",
            {"salience_id": request.salience_id},
        ).get("result", [])
        if not work:
            raise ValueError("source run has no mining work")
        self._command(
            "INSERT INTO V2ReminingRequest SET request_id = :request_id, request_key = :request_key, "
            "source_run_id = :source_run_id, salience_id = :salience_id, scope_id = :scope_id, "
            "reason = :reason, reprocess_token = :reprocess_token, status = :status, "
            "replacement_run_id = :replacement_run_id, created_at = :created_at, "
            "completed_at = :completed_at, error = :error",
            {**request.__dict__, "created_at": _epoch_millis(request.created_at), "completed_at": None},
        )
        self._command(
            "UPDATE V2MiningWork SET state = 'pending', available_at = :now, updated_at = :now, "
            "lease_owner = NULL, leased_until = NULL, last_error = NULL "
            "WHERE salience_id = :salience_id AND state = 'completed'",
            {"now": _epoch_millis(request.created_at), "salience_id": request.salience_id},
        )
        return request

    def active_remining_for_salience(self, salience_id: str) -> ReminingRequest | None:
        rows = self._command(
            "SELECT FROM V2ReminingRequest WHERE salience_id = :salience_id AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT 1", {"salience_id": salience_id},
        ).get("result", [])
        return self._remining_from_row(rows[0]) if rows else None

    def complete_remining(
        self, request_id: str, replacement_run_id: str, replacement_claim_ids: list[str], *, now: datetime
    ) -> ReminingRequest:
        rows = self._command(
            "SELECT FROM V2ReminingRequest WHERE request_id = :request_id LIMIT 1",
            {"request_id": request_id},
        ).get("result", [])
        if not rows:
            raise ValueError("re-mining request not found")
        request = self._remining_from_row(rows[0])
        if request.status == "completed":
            return request
        old_evidence = self._command(
            "SELECT FROM V2Evidence WHERE run_id = :run_id",
            {"run_id": request.source_run_id},
        ).get("result", [])
        replacement = set(replacement_claim_ids)
        affected = sorted({row["claim_id"] for row in old_evidence if row["claim_id"] not in replacement})
        for claim_id in affected:
            self._command(
                "UPDATE V2Evidence SET lifecycle = 'superseded' WHERE run_id = :run_id "
                "AND claim_id = :claim_id AND lifecycle = 'active'",
                {"run_id": request.source_run_id, "claim_id": claim_id},
            )
            remaining = self._command(
                "SELECT evidence_id FROM V2Evidence WHERE claim_id = :claim_id AND lifecycle = 'active' LIMIT 1",
                {"claim_id": claim_id},
            ).get("result", [])
            if not remaining:
                self._command(
                    "UPDATE V2Claim SET lifecycle = 'superseded' WHERE claim_id = :claim_id AND lifecycle = 'active'",
                    {"claim_id": claim_id},
                )
        self._command(
            "UPDATE V2ReminingRequest SET status = 'completed', replacement_run_id = :replacement_run_id, "
            "completed_at = :completed_at WHERE request_id = :request_id",
            {"replacement_run_id": replacement_run_id, "completed_at": _epoch_millis(now), "request_id": request_id},
        )
        self.put_maintenance_notice(MaintenanceNotice(
            notice_id=_new_uuid7(), notice_key=f"remining:{request_id}", kind="remining_applied",
            status="resolved", scope_id=request.scope_id, claim_id=None, run_id=replacement_run_id,
            details={"source_run_id": request.source_run_id, "replacement_run_id": replacement_run_id,
                     "superseded_claim_ids": affected}, created_at=now, resolved_at=now,
        ))
        return replace(request, status="completed", replacement_run_id=replacement_run_id, completed_at=now)

    def list_remining_requests(self, *, limit: int = 50) -> list[ReminingRequest]:
        bounded = max(1, min(limit, 250))
        rows = self._command(
            f"SELECT FROM V2ReminingRequest ORDER BY created_at DESC, request_id DESC LIMIT {bounded}"
        ).get("result", [])
        return [self._remining_from_row(row) for row in rows]

    @classmethod
    def _extension_from_row(cls, row: dict) -> ExtensionRelation:
        return ExtensionRelation(
            relation=row["relation"], definition=json.loads(row["definition_json"]), status=row["status"],
            schema_version=row["schema_version"], promoted_relation=row.get("promoted_relation"),
            schema_commit=row.get("schema_commit"), migration_plan=json.loads(row.get("migration_plan_json") or "{}"),
            created_at=cls._datetime(row["created_at"]), updated_at=cls._datetime(row["updated_at"]),
        )

    def put_extension_relation(self, relation: ExtensionRelation) -> ExtensionRelation:
        existing = self.get_extension_relation(relation.relation)
        if existing is not None:
            return existing
        self._command(
            "INSERT INTO V2ExtensionRelation SET relation = :relation, definition_json = :definition_json, "
            "status = :status, schema_version = :schema_version, promoted_relation = :promoted_relation, "
            "schema_commit = :schema_commit, migration_plan_json = :migration_plan_json, "
            "created_at = :created_at, updated_at = :updated_at",
            {**relation.__dict__, "definition_json": json.dumps(relation.definition, separators=(",", ":"), sort_keys=True),
             "migration_plan_json": json.dumps(relation.migration_plan, separators=(",", ":"), sort_keys=True),
             "created_at": _epoch_millis(relation.created_at), "updated_at": _epoch_millis(relation.updated_at)},
        )
        return relation

    def get_extension_relation(self, relation: str) -> ExtensionRelation | None:
        rows = self._command(
            "SELECT FROM V2ExtensionRelation WHERE relation = :relation LIMIT 1", {"relation": relation}
        ).get("result", [])
        return self._extension_from_row(rows[0]) if rows else None

    def list_extension_relations(self, *, limit: int = 100) -> list[ExtensionRelation]:
        bounded = max(1, min(limit, 250))
        rows = self._command(
            f"SELECT FROM V2ExtensionRelation ORDER BY updated_at DESC, relation LIMIT {bounded}"
        ).get("result", [])
        return [self._extension_from_row(row) for row in rows]

    def update_extension_relation(self, relation: ExtensionRelation) -> ExtensionRelation:
        if self.get_extension_relation(relation.relation) is None:
            raise ValueError("extension relation is not registered")
        self._command(
            "UPDATE V2ExtensionRelation SET definition_json = :definition_json, status = :status, "
            "schema_version = :schema_version, promoted_relation = :promoted_relation, "
            "schema_commit = :schema_commit, migration_plan_json = :migration_plan_json, "
            "updated_at = :updated_at WHERE relation = :relation",
            {**relation.__dict__, "definition_json": json.dumps(relation.definition, separators=(",", ":"), sort_keys=True),
             "migration_plan_json": json.dumps(relation.migration_plan, separators=(",", ":"), sort_keys=True),
             "updated_at": _epoch_millis(relation.updated_at)},
        )
        return relation

    def supersede_claims_for_relation(self, relation: str) -> list[str]:
        rows = self._command(
            "SELECT claim_id FROM V2Claim WHERE relation = :relation AND lifecycle = 'active'",
            {"relation": relation},
        ).get("result", [])
        affected = [row["claim_id"] for row in rows]
        for claim_id in affected:
            self._command(
                "UPDATE V2Evidence SET lifecycle = 'superseded' WHERE claim_id = :claim_id AND lifecycle = 'active'",
                {"claim_id": claim_id},
            )
            self._command(
                "UPDATE V2Claim SET lifecycle = 'superseded' WHERE claim_id = :claim_id AND lifecycle = 'active'",
                {"claim_id": claim_id},
            )
        return affected


class CaptureService:
    def __init__(
        self,
        store: CaptureStore,
        policy: InstancePolicy,
        *,
        clock_skew_tolerance: timedelta = timedelta(hours=6),
        session_quiescence: timedelta = timedelta(minutes=5),
    ) -> None:
        self.store = store
        self.policy = policy
        self.clock_skew_tolerance = clock_skew_tolerance
        self.session_quiescence = session_quiescence

    def load_policy(self) -> None:
        stored = self.store.get_config("policy")
        if stored:
            if stored.get("mining_status") not in {"active", "paused"}:
                stored = {**stored, "mining_status": "paused"}
                self.store.set_config("policy", stored)
            self.policy = InstancePolicy.model_validate(stored)

    def update_policy(self, policy: InstancePolicy) -> InstancePolicy:
        policy.version = self.policy.version + 1
        self.store.set_config("policy", policy.model_dump(mode="json"))
        self.policy = policy
        return policy

    def ingest(self, envelope: CaptureEnvelope, now: datetime | None = None) -> CaptureReceipt:
        received_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        expected_upload_at = envelope.occurred_at + timedelta(seconds=envelope.meta.queue_age_seconds)
        clock_adjusted = abs(received_at - expected_upload_at) > self.clock_skew_tolerance
        normalized_at = (
            received_at - timedelta(seconds=envelope.meta.queue_age_seconds)
            if clock_adjusted
            else envelope.occurred_at
        )
        existing_capture = self.store.get_capture(envelope.capture_id)
        if existing_capture is not None:
            if existing_capture.state == "staged":
                self.store.schedule_capture(
                    existing_capture,
                    quiescence=self.session_quiescence,
                )
            return CaptureReceipt(
                capture_id=envelope.capture_id,
                status="duplicate",
                received_at=received_at,
                normalized_at=normalized_at,
                clock_adjusted=clock_adjusted,
            )
        class_policy = self.policy.classes.get(envelope.capture_class)
        if class_policy is not None and not class_policy.capture:
            return CaptureReceipt(
                capture_id=envelope.capture_id,
                status="policy_blocked",
                received_at=received_at,
                normalized_at=normalized_at,
                clock_adjusted=clock_adjusted,
            )
        known_class = class_policy is not None
        state = "staged" if known_class else "quarantined_unknown_class"
        stored_capture = StoredCapture(
            envelope=envelope,
            received_at=received_at,
            normalized_at=normalized_at,
            state=state,
        )
        inserted = self.store.put_if_absent(stored_capture)
        if known_class:
            scheduled_capture = stored_capture if inserted else self.store.get_capture(envelope.capture_id)
            if scheduled_capture is not None and scheduled_capture.state == "staged":
                self.store.schedule_capture(
                    scheduled_capture,
                    quiescence=self.session_quiescence,
                )
        status = "duplicate" if not inserted else ("accepted" if known_class else "quarantined")
        return CaptureReceipt(
            capture_id=envelope.capture_id,
            status=status,
            received_at=received_at,
            normalized_at=normalized_at,
            clock_adjusted=clock_adjusted,
        )
