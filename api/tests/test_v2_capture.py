import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.v2.claims import ClaimCommitter, load_relation_schema
from app.v2.mining import BifrostExtractionGateway, MiningRunCoordinator, MiningWorker
from app.v2.maintenance import KnowledgeMaintainer
from app.v2.models import (
    CaptureEnvelope,
    GroundedQueryRequest,
    InstancePolicy,
    ModelEndpointConfig,
)
from app.v2.query import BifrostQueryGateway, GroundedQueryService
from app.v2.resolution import (
    BifrostResolutionGateway,
    EntityResolver,
    ResolutionConfig,
    normalize_surface,
)
from app.v2.salience import SalienceConfig, SalienceWorker, mechanically_trim
from app.v2.service import (
    ArcadeCaptureStore,
    CaptureService,
    Claim,
    Entity,
    Evidence,
    MemoryCaptureStore,
    ResolvedMention,
    _epoch_millis,
)


CAPTURE_ID = "018f5e2a-1234-7abc-8def-1234567890ab"


def envelope(**overrides):
    body = {
        "capture_id": CAPTURE_ID,
        "kind": "event",
        "class": "transcript.message",
        "surface": "codex",
        "session_ref": "session-1",
        "occurred_at": "2026-07-10T12:00:00Z",
        "payload": {"role": "user", "content": "Remember this"},
        "refs": [],
        "hints": [],
        "meta": {
            "contract_version": "2.0",
            "spine_version": "2.0.0",
            "adapter_id": "codex",
            "adapter_version": "2.0.0",
            "device_id": "device-1",
            "user_id": "user-1",
            "queue_age_seconds": 0,
            "policy_version": 1,
        },
    }
    body.update(overrides)
    return CaptureEnvelope.model_validate(body)


def test_ingest_is_idempotent() -> None:
    service = CaptureService(MemoryCaptureStore(), InstancePolicy())
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    assert service.ingest(envelope(), now).status == "accepted"
    assert service.ingest(envelope(), now).status == "duplicate"


def test_unknown_class_is_durably_quarantined() -> None:
    service = CaptureService(MemoryCaptureStore(), InstancePolicy())
    receipt = service.ingest(envelope(**{"class": "future.sensor"}), datetime.now(timezone.utc))
    assert receipt.status == "quarantined"


def test_gross_clock_skew_uses_received_time_less_queue_age() -> None:
    service = CaptureService(MemoryCaptureStore(), InstancePolicy())
    capture = envelope()
    capture.meta.queue_age_seconds = 120
    now = capture.occurred_at + timedelta(days=2)
    receipt = service.ingest(capture, now)
    assert receipt.clock_adjusted is True
    assert receipt.normalized_at == now - timedelta(seconds=120)


def test_snapshot_requires_content_hash() -> None:
    with pytest.raises(ValidationError, match="content_hash"):
        envelope(kind="snapshot", **{"class": "push.document"})


def test_arcadedb_datetime_binding_round_trips_milliseconds() -> None:
    value = datetime(2026, 7, 10, 12, 0, 0, 123000, tzinfo=timezone.utc)

    assert ArcadeCaptureStore._datetime(_epoch_millis(value)) == value


def test_disabled_capture_class_is_blocked_without_storage() -> None:
    store = MemoryCaptureStore()
    policy = InstancePolicy()
    policy.classes["transcript.message"].capture = False
    service = CaptureService(store, policy)

    receipt = service.ingest(envelope(), datetime(2026, 7, 10, 12, tzinfo=timezone.utc))

    assert receipt.status == "policy_blocked"
    assert store.captures == ()
    assert store.sessions == ()
    assert store.work_items == ()


def test_existing_capture_remains_a_duplicate_after_policy_is_disabled() -> None:
    store = MemoryCaptureStore()
    policy = InstancePolicy()
    service = CaptureService(store, policy)
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    assert service.ingest(envelope(), now).status == "accepted"
    policy.classes["transcript.message"].capture = False

    assert service.ingest(envelope(), now).status == "duplicate"
    assert len(store.captures) == 1


def test_mining_is_paused_by_default_and_can_be_activated() -> None:
    assert InstancePolicy().mining_status == "paused"
    assert InstancePolicy.model_validate({"mining_status": "active"}).mining_status == "active"


def test_legacy_unavailable_mining_policy_is_migrated_to_paused() -> None:
    store = MemoryCaptureStore()
    stored = InstancePolicy().model_dump(mode="json")
    stored["mining_status"] = "unavailable_not_implemented"
    store.set_config("policy", stored)
    service = CaptureService(store, InstancePolicy())

    service.load_policy()

    assert service.policy.mining_status == "paused"
    assert store.get_config("policy")["mining_status"] == "paused"


def test_transcript_session_assembles_out_of_order_and_waits_for_quiescence() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy(), session_quiescence=timedelta(minutes=5))
    first_received = datetime(2026, 7, 10, 12, 2, tzinfo=timezone.utc)
    earlier = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890ac",
        occurred_at="2026-07-10T12:01:00Z",
    )
    later = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890ad",
        occurred_at="2026-07-10T12:03:00Z",
    )

    service.ingest(later, first_received)
    service.ingest(earlier, first_received + timedelta(minutes=1))

    assert len(store.sessions) == 1
    session = store.sessions[0]
    assert session.capture_count == 2
    assert session.first_normalized_at == earlier.occurred_at
    assert session.last_normalized_at == later.occurred_at
    assert len(store.work_items) == 1
    work = store.work_items[0]
    assert work.available_at == first_received + timedelta(minutes=6)
    assert store.claim_ready_work(
        "worker-a", now=first_received + timedelta(minutes=5), lease_for=timedelta(minutes=1)
    ) == []
    assert [item.work_id for item in store.claim_ready_work(
        "worker-a", now=first_received + timedelta(minutes=6), lease_for=timedelta(minutes=1)
    )] == [work.work_id]


def test_duplicate_capture_does_not_duplicate_session_work() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy())
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)

    assert service.ingest(envelope(), now).status == "accepted"
    assert service.ingest(envelope(), now).status == "duplicate"

    assert store.sessions[0].capture_count == 1
    assert len(store.work_items) == 1


def test_explicit_push_is_ready_immediately() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy())
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    pushed = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890ae",
        kind="snapshot",
        **{
            "class": "push.document",
            "payload": {"content_hash": f"sha256:{'a' * 64}", "content": "Remember this now"},
        },
    )

    assert service.ingest(pushed, now).status == "accepted"
    claimed = store.claim_ready_work("worker-a", now=now, lease_for=timedelta(minutes=1))

    assert len(claimed) == 1
    assert claimed[0].work_type == "push_admission"
    assert claimed[0].scope_id == pushed.capture_id


def test_work_lease_recovers_and_requires_its_owner() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy(), session_quiescence=timedelta(seconds=1))
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    service.ingest(envelope(), now)
    ready_at = now + timedelta(seconds=1)

    first = store.claim_ready_work("worker-a", now=ready_at, lease_for=timedelta(minutes=1))[0]
    assert store.complete_work(first.work_id, "worker-b", now=ready_at) is False
    assert store.claim_ready_work(
        "worker-b", now=ready_at + timedelta(seconds=59), lease_for=timedelta(minutes=1)
    ) == []
    recovered = store.claim_ready_work(
        "worker-b", now=ready_at + timedelta(minutes=1), lease_for=timedelta(minutes=1)
    )[0]
    assert recovered.attempts == 2
    assert store.retry_work(
        recovered.work_id,
        "worker-b",
        now=ready_at + timedelta(minutes=1),
        retry_after=timedelta(seconds=30),
        error="temporary model outage",
    ) is True
    assert store.claim_ready_work(
        "worker-c", now=ready_at + timedelta(minutes=1, seconds=29), lease_for=timedelta(minutes=1)
    ) == []
    final = store.claim_ready_work(
        "worker-c", now=ready_at + timedelta(minutes=1, seconds=30), lease_for=timedelta(minutes=1)
    )[0]
    assert final.last_error == "temporary model outage"
    assert store.complete_work(final.work_id, "worker-c", now=ready_at + timedelta(minutes=2)) is True


def test_late_session_capture_creates_a_new_generation() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy(), session_quiescence=timedelta(seconds=1))
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    service.ingest(envelope(), now)
    first = store.claim_ready_work(
        "worker-a", now=now + timedelta(seconds=1), lease_for=timedelta(minutes=1)
    )[0]
    assert store.complete_work(first.work_id, "worker-a", now=now + timedelta(seconds=2)) is True

    service.ingest(
        envelope(
            capture_id="018f5e2a-1234-7abc-8def-1234567890af",
            occurred_at="2026-07-10T12:00:03Z",
        ),
        now + timedelta(seconds=3),
    )

    assert store.sessions[0].generation == 2
    assert store.sessions[0].capture_count == 2
    assert sorted(item.generation for item in store.work_items) == [1, 2]
    assert [item.envelope.capture_id for item in store.captures_for_work(first)] == [CAPTURE_ID]


def test_mechanical_trim_removes_tool_repeats_and_bounds_derived_text() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy())
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    original = "A" * 20
    captures = [
        envelope(payload={"role": "user", "content": original}),
        envelope(
            capture_id="018f5e2a-1234-7abc-8def-1234567890b0",
            payload={"role": "tool", "content": "large tool result"},
        ),
        envelope(
            capture_id="018f5e2a-1234-7abc-8def-1234567890b1",
            payload={"role": "assistant", "content": original.lower()},
        ),
    ]
    for offset, capture in enumerate(captures):
        service.ingest(capture, now + timedelta(seconds=offset))

    trimmed, signals = mechanically_trim(
        list(store.captures),
        SalienceConfig(max_capture_chars=10, max_scope_chars=50),
    )

    assert [item["content"] for item in trimmed] == ["A" * 10]
    assert signals["removed_tool_items"] == 1
    assert signals["removed_repeats"] == 1
    assert signals["truncated_items"] == 1
    assert store.captures[0].envelope.payload["content"] == original


def test_salience_worker_persists_skip_and_completes_work_idempotently() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy(), session_quiescence=timedelta(seconds=1))
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    service.ingest(envelope(payload={"role": "user", "content": "short"}), now)
    worker = SalienceWorker(store, "worker-a")

    records = worker.process_once(now + timedelta(seconds=1))

    assert len(records) == 1
    assert records[0].status == "skipped"
    assert records[0].reason == "below_salience_threshold"
    assert store.work_items[0].state == "completed"
    assert worker.process_once(now + timedelta(seconds=2)) == []
    assert len(store.salience_records) == 1


def test_salience_worker_admits_user_volume_and_orders_session_captures() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy(), session_quiescence=timedelta(seconds=1))
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    later = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890b2",
        occurred_at="2026-07-10T12:00:02Z",
        payload={"role": "assistant", "content": "Acknowledged"},
    )
    earlier = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890b3",
        occurred_at="2026-07-10T12:00:01Z",
        payload={"role": "user", "content": "Please remember this detailed decision for later retrieval."},
    )
    service.ingest(later, now)
    service.ingest(earlier, now + timedelta(seconds=1))

    record = SalienceWorker(store, "worker-a").process_once(now + timedelta(seconds=2))[0]

    assert record.status == "admitted"
    assert record.reason == "user_authored_volume"
    assert [item["capture_id"] for item in record.trimmed] == [
        earlier.capture_id,
        later.capture_id,
    ]


def test_explicit_push_bypasses_salience_threshold() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy())
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    pushed = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890b4",
        kind="snapshot",
        **{
            "class": "push.document",
            "payload": {"content_hash": f"sha256:{'b' * 64}", "content": "x"},
        },
    )
    service.ingest(pushed, now)

    record = SalienceWorker(store, "worker-a").process_once(now)[0]

    assert record.status == "admitted"
    assert record.reason == "explicit_push"


def test_next_generation_reuses_prior_mining_output_with_delta_and_context() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy(), session_quiescence=timedelta(seconds=1))
    salience_worker = SalienceWorker(store, "salience-a")
    coordinator = MiningRunCoordinator(store, context_turns=2)
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    decision = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890b5",
        payload={
            "role": "user",
            "content": "We should keep the deployment local because privacy matters most.",
        },
    )
    question = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890b6",
        occurred_at="2026-07-10T12:00:01Z",
        payload={"role": "assistant", "content": "Should I record that as the final decision?"},
    )
    service.ingest(decision, now)
    service.ingest(question, now + timedelta(seconds=1))
    first_salience = salience_worker.process_once(now + timedelta(seconds=2))[0]
    first_preparation = coordinator.prepare(
        first_salience,
        stage="interpret",
        miner_version="miner-v1",
        config={"temperature": 0},
    )
    first_run = coordinator.record_success(
        first_preparation,
        output={"episodes": [{"decision": "keep deployment local"}]},
        evidence_capture_ids=[decision.capture_id],
        now=now + timedelta(seconds=3),
    )

    answer = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890b7",
        occurred_at="2026-07-10T12:30:00Z",
        payload={"role": "user", "content": "yes"},
    )
    service.ingest(answer, now + timedelta(minutes=30))
    second_salience = salience_worker.process_once(now + timedelta(minutes=30, seconds=1))[0]
    second_preparation = coordinator.prepare(
        second_salience,
        stage="interpret",
        miner_version="miner-v1",
        config={"temperature": 0},
    )

    assert second_preparation.parent_run_id == first_run.run_id
    assert second_preparation.prior_output == first_run.output
    assert [item["capture_id"] for item in second_preparation.delta] == [answer.capture_id]
    assert [item["capture_id"] for item in second_preparation.context] == [
        decision.capture_id,
        question.capture_id,
    ]
    assert second_preparation.evidence_eligible_capture_ids == [answer.capture_id]
    with pytest.raises(ValueError, match="context-only"):
        coordinator.record_success(
            second_preparation,
            output={"confirmed": True},
            evidence_capture_ids=[question.capture_id],
        )

    second_run = coordinator.record_success(
        second_preparation,
        output={"confirmed": True},
        evidence_capture_ids=[answer.capture_id],
        now=now + timedelta(minutes=30, seconds=2),
    )
    repeated = coordinator.prepare(
        second_salience,
        stage="interpret",
        miner_version="miner-v1",
        config={"temperature": 0},
    )
    assert repeated.reusable_run == second_run
    assert second_run.parent_run_id == first_run.run_id


def test_incompatible_miner_configuration_falls_back_to_full_generation() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy(), session_quiescence=timedelta(seconds=1))
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    service.ingest(
        envelope(payload={"role": "user", "content": "Remember this sufficiently long decision."}),
        now,
    )
    salience = SalienceWorker(store, "salience-a").process_once(now + timedelta(seconds=1))[0]
    coordinator = MiningRunCoordinator(store)
    first = coordinator.prepare(
        salience,
        stage="interpret",
        miner_version="miner-v1",
        config={"schema": 1},
    )
    coordinator.record_success(first, output={"ok": True}, evidence_capture_ids=[CAPTURE_ID])

    later = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890b8",
        occurred_at="2026-07-10T12:01:00Z",
        payload={"role": "user", "content": "A later addition"},
    )
    service.ingest(later, now + timedelta(minutes=1))
    next_salience = SalienceWorker(store, "salience-b").process_once(
        now + timedelta(minutes=1, seconds=1)
    )[0]
    changed = coordinator.prepare(
        next_salience,
        stage="interpret",
        miner_version="miner-v1",
        config={"schema": 2},
    )

    assert changed.parent_run_id is None
    assert changed.context == []
    assert [item["capture_id"] for item in changed.delta] == [CAPTURE_ID, later.capture_id]


def test_failed_mining_run_can_retry_without_hiding_successful_output() -> None:
    store = MemoryCaptureStore()
    service = CaptureService(store, InstancePolicy(), session_quiescence=timedelta(seconds=1))
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    service.ingest(
        envelope(payload={"role": "user", "content": "Remember this sufficiently long decision."}),
        now,
    )
    salience = SalienceWorker(store, "salience-a").process_once(now + timedelta(seconds=1))[0]
    coordinator = MiningRunCoordinator(store)
    preparation = coordinator.prepare(
        salience,
        stage="interpret",
        miner_version="miner-v1",
        config={"schema": 1},
    )

    failed = coordinator.record_failure(preparation, error="temporary model outage", now=now)
    assert coordinator.prepare(
        salience,
        stage="interpret",
        miner_version="miner-v1",
        config={"schema": 1},
    ).reusable_run is None

    succeeded = coordinator.record_success(
        preparation,
        output={"decision": "remembered"},
        evidence_capture_ids=[CAPTURE_ID],
        now=now + timedelta(seconds=1),
    )
    assert succeeded.run_id == failed.run_id
    assert succeeded.status == "succeeded"
    assert coordinator.prepare(
        salience,
        stage="interpret",
        miner_version="miner-v1",
        config={"schema": 1},
    ).reusable_run == succeeded


class StubExtractionGateway:
    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = outputs
        self.calls = []

    def extract(self, preparation, endpoint) -> dict:
        self.calls.append((preparation, endpoint))
        output = self.outputs[min(len(self.calls) - 1, len(self.outputs) - 1)]
        if isinstance(output, Exception):
            raise output
        return output


def admitted_mining_fixture(policy: InstancePolicy | None = None):
    store = MemoryCaptureStore()
    selected_policy = policy or InstancePolicy()
    service = CaptureService(
        store,
        selected_policy,
        session_quiescence=timedelta(seconds=1),
    )
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    capture = envelope(
        payload={"role": "user", "content": "We decided to keep all deployment data local."}
    )
    service.ingest(capture, now)
    SalienceWorker(store, "salience-a").process_once(now + timedelta(seconds=1))
    return store, selected_policy, capture, now + timedelta(seconds=1)


def extraction_for(capture_id: str) -> dict:
    quote = "We decided to keep all deployment data local."
    evidence = {"capture_id": capture_id, "start": 0, "end": len(quote), "quote": quote}
    return {
        "episodes": [{
            "summary": "A local deployment decision was made.",
            "evidence": [evidence],
            "valid_from": None,
            "valid_to": None,
        }],
        "mentions": [{
            "surface": "deployment data",
            "entity_type": "data_collection",
            "context": "keep all deployment data local",
            "evidence": evidence,
        }],
        "candidate_claims": [{
            "subject": "deployment data",
            "relation": "storage_location",
            "object": "local",
            "object_kind": "literal",
            "evidence": [evidence],
            "valid_from": None,
            "valid_to": None,
        }],
    }


def test_paused_mining_leaves_durable_work_unclaimed() -> None:
    store, policy, capture, now = admitted_mining_fixture()
    gateway = StubExtractionGateway([extraction_for(capture.capture_id)])
    worker = MiningWorker(
        store,
        gateway,
        worker_id="mining-a",
        policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(),
    )

    assert worker.process_once(now) == []
    assert gateway.calls == []
    assert store.mining_work_items[0].state == "pending"


def test_startup_backfills_mining_work_for_existing_admission() -> None:
    store, _, _, _ = admitted_mining_fixture()
    store._mining_work.clear()

    assert store.backfill_mining_work() == 1
    assert len(store.mining_work_items) == 1
    assert store.backfill_mining_work() == 0


def test_active_local_mining_persists_strict_candidate_output() -> None:
    policy = InstancePolicy(mining_status="active")
    store, policy, capture, now = admitted_mining_fixture(policy)
    gateway = StubExtractionGateway([extraction_for(capture.capture_id)])
    worker = MiningWorker(
        store,
        gateway,
        worker_id="mining-a",
        policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(processing_location="local"),
    )

    runs = worker.process_once(now)

    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    assert runs[0].evidence_capture_ids == [capture.capture_id]
    assert runs[0].output["candidate_claims"][0]["relation"] == "storage_location"
    assert store.mining_work_items[0].state == "completed"
    assert len(gateway.calls) == 1
    assert store.list_mining_runs(limit=1) == runs


def test_remote_mining_fails_closed_without_unrestricted_policy() -> None:
    policy = InstancePolicy(mining_status="active")
    store, policy, capture, now = admitted_mining_fixture(policy)
    gateway = StubExtractionGateway([extraction_for(capture.capture_id)])
    worker = MiningWorker(
        store,
        gateway,
        worker_id="mining-a",
        policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(processing_location="remote"),
    )

    assert worker.process_once(now) == []
    assert gateway.calls == []
    assert store.mining_work_items[0].state == "pending"
    assert "sanitized_remote" in store.mining_work_items[0].last_error


def test_local_mining_rechecks_disabled_capture_class() -> None:
    policy = InstancePolicy(mining_status="active")
    store, policy, capture, now = admitted_mining_fixture(policy)
    policy.classes["transcript.message"].capture = False
    gateway = StubExtractionGateway([extraction_for(capture.capture_id)])
    worker = MiningWorker(
        store,
        gateway,
        worker_id="mining-a",
        policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(processing_location="local"),
    )

    assert worker.process_once(now) == []
    assert gateway.calls == []
    assert "capture_disabled" in store.mining_work_items[0].last_error


def test_malformed_extraction_is_recorded_and_retried() -> None:
    policy = InstancePolicy(mining_status="active")
    store, policy, capture, now = admitted_mining_fixture(policy)
    invalid = extraction_for(capture.capture_id)
    invalid["candidate_claims"][0]["unexpected"] = True
    gateway = StubExtractionGateway([invalid, extraction_for(capture.capture_id)])
    worker = MiningWorker(
        store,
        gateway,
        worker_id="mining-a",
        policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(),
        retry_after=timedelta(seconds=5),
    )

    assert worker.process_once(now) == []
    assert store.mining_runs[0].status == "failed"
    assert store.mining_work_items[0].state == "pending"

    runs = worker.process_once(now + timedelta(seconds=5))

    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    assert runs[0].run_id == store.mining_runs[0].run_id
    assert len(gateway.calls) == 2


def test_extraction_cannot_cite_context_only_capture() -> None:
    policy = InstancePolicy(mining_status="active")
    store, policy, capture, now = admitted_mining_fixture(policy)
    invalid = extraction_for("018f5e2a-1234-7abc-8def-1234567890ff")
    gateway = StubExtractionGateway([invalid])
    worker = MiningWorker(
        store,
        gateway,
        worker_id="mining-a",
        policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(),
    )

    assert worker.process_once(now) == []
    assert "context-only or unknown" in store.mining_work_items[0].last_error


def test_extraction_source_span_must_match_delta_text() -> None:
    policy = InstancePolicy(mining_status="active")
    store, policy, capture, now = admitted_mining_fixture(policy)
    invalid = extraction_for(capture.capture_id)
    invalid["episodes"][0]["evidence"][0]["quote"] = "invented quote"
    gateway = StubExtractionGateway([invalid])
    worker = MiningWorker(
        store,
        gateway,
        worker_id="mining-a",
        policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(),
    )

    assert worker.process_once(now) == []
    assert "source quote does not match" in store.mining_work_items[0].last_error


def test_bifrost_gateway_requests_strict_json_schema(monkeypatch) -> None:
    store, _, capture, _ = admitted_mining_fixture()
    salience = store.salience_records[0]
    preparation = MiningRunCoordinator(store).prepare(
        salience,
        stage="interpret_extract",
        miner_version="structured-extractor-v1",
        config={"temperature": 0},
    )
    seen = {}

    class Response:
        is_error = False

        def json(self):
            return {"choices": [{"message": {
                "content": json.dumps(extraction_for(capture.capture_id))
            }}]}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["payload"] = kwargs["json"]
        return Response()

    monkeypatch.setattr("app.v2.mining.httpx.post", fake_post)

    output = BifrostExtractionGateway("http://bifrost:8080", ("user", "pass")).extract(
        preparation,
        ModelEndpointConfig(),
    )

    assert output["episodes"][0]["evidence"][0]["capture_id"] == capture.capture_id
    assert seen["url"] == "http://bifrost:8080/v1/chat/completions"
    assert seen["payload"]["response_format"]["type"] == "json_schema"
    assert seen["payload"]["response_format"]["json_schema"]["strict"] is True
    assert seen["payload"]["messages"][1]["content"].find('"schema_version":"core-v1"') > 0
    assert seen["payload"]["messages"][1]["content"].find('"storage_location"') > 0


class StubResolutionGateway:
    def __init__(self, vectors: list[list[float]], judgments: list[dict] | None = None) -> None:
        self.vectors = vectors
        self.judgments = judgments or []
        self.embed_calls = []
        self.judge_calls = []

    def embed(self, texts, endpoint):
        self.embed_calls.append((texts, endpoint))
        return self.vectors[:len(texts)]

    def judge(self, mention, candidates, endpoint):
        self.judge_calls.append((mention, candidates, endpoint))
        return self.judgments[min(len(self.judge_calls) - 1, len(self.judgments) - 1)]


def successful_extraction_run():
    policy = InstancePolicy(mining_status="active")
    store, policy, capture, now = admitted_mining_fixture(policy)
    worker = MiningWorker(
        store,
        StubExtractionGateway([extraction_for(capture.capture_id)]),
        worker_id="mining-a",
        policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(embedding_dimensions=3),
    )
    run = worker.process_once(now)[0]
    return store, policy, capture, run


def test_entity_resolution_mints_durable_entity_and_is_idempotent() -> None:
    store, policy, _, run = successful_extraction_run()
    gateway = StubResolutionGateway([[3.0, 4.0, 0.0]])
    resolver = EntityResolver(store, gateway)
    endpoint = ModelEndpointConfig(embedding_dimensions=3)

    first = resolver.resolve_run(run, endpoint, policy)
    repeated = resolver.resolve_run(run, endpoint, policy)

    assert len(first) == 1
    assert first == repeated
    assert first[0].resolution_method == "automatic_mint"
    assert first[0].embedding == pytest.approx([0.6, 0.8, 0.0])
    assert first[0].entity_id == store.entities[0].entity_id
    assert first[0].resolver_version == "entity-resolver-v1"
    assert first[0].decision_details["thresholds"]["high"] == 0.90
    assert len(store.entities) == 1
    assert len(store.resolved_mentions) == 1
    assert len(gateway.embed_calls) == 1
    assert store.list_resolved_mentions(limit=1) == first
    assert store.list_entities(limit=1) == list(store.entities)


def test_embedding_region_rejects_model_or_dimension_changes() -> None:
    store, policy, _, run = successful_extraction_run()
    resolver = EntityResolver(store, StubResolutionGateway([[1.0, 0.0, 0.0]]))
    resolver.resolve_run(run, ModelEndpointConfig(embedding_dimensions=3), policy)

    with pytest.raises(ValueError, match="requires a new vector region"):
        resolver.resolve_run(
            replace(run, run_id="changed-region-run", run_key="changed-region-key"),
            ModelEndpointConfig(embedding_model_name="different", embedding_dimensions=3),
            policy,
        )


def test_startup_reopens_successful_extraction_missing_resolution_marker() -> None:
    store, _, _, _ = successful_extraction_run()

    assert store.mining_work_items[0].state == "completed"
    assert store.backfill_mining_work() == 1
    assert store.mining_work_items[0].state == "pending"


def test_resolution_without_claim_commit_reopens_completed_work() -> None:
    store, policy, _, run = successful_extraction_run()
    EntityResolver(store, StubResolutionGateway([[1.0, 0.0, 0.0]])).resolve_run(
        run, ModelEndpointConfig(embedding_dimensions=3), policy
    )

    assert store.is_resolution_complete(run.run_id) is True
    assert store.backfill_mining_work() == 1
    assert store.mining_work_items[0].state == "pending"


def test_claim_commit_marker_prevents_completed_work_from_reopening() -> None:
    store, policy, _, run = successful_extraction_run()
    EntityResolver(store, StubResolutionGateway([[1.0, 0.0, 0.0]])).resolve_run(
        run, ModelEndpointConfig(embedding_dimensions=3), policy
    )
    result = ClaimCommitter(store).commit_run(run)

    assert len(result.claims) == 1
    assert len(result.evidence) == 1
    assert store.is_claim_commit_complete(run.run_id) is True
    assert store.backfill_mining_work() == 0
    assert store.mining_work_items[0].state == "completed"


def test_exact_normalized_surface_reuses_the_stable_entity() -> None:
    store, policy, _, run = successful_extraction_run()
    gateway = StubResolutionGateway([[1.0, 0.0, 0.0], [0.9, 0.1, 0.0]])
    resolver = EntityResolver(store, gateway)
    endpoint = ModelEndpointConfig(embedding_dimensions=3)
    first = resolver.resolve_run(run, endpoint, policy)[0]
    output = json.loads(json.dumps(run.output))
    output["mentions"][0]["surface"] = "  Deployment DATA! "
    later = replace(run, run_id="later-run", run_key="later-key", output=output)

    second = resolver.resolve_run(later, endpoint, policy)[0]

    assert normalize_surface(output["mentions"][0]["surface"]) == "deployment data"
    assert second.entity_id == first.entity_id
    assert second.resolution_method == "exact"
    assert len(store.entities) == 1


def test_high_confidence_vector_candidate_matches_automatically() -> None:
    store, policy, _, run = successful_extraction_run()
    gateway = StubResolutionGateway([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    resolver = EntityResolver(
        store,
        gateway,
        ResolutionConfig(low_threshold=0.50, high_threshold=0.70),
    )
    endpoint = ModelEndpointConfig(embedding_dimensions=3)
    first = resolver.resolve_run(run, endpoint, policy)[0]
    output = json.loads(json.dumps(run.output))
    output["mentions"][0]["surface"] = "private records"
    later = replace(run, run_id="vector-run", run_key="vector-key", output=output)

    second = resolver.resolve_run(later, endpoint, policy)[0]

    assert second.entity_id == first.entity_id
    assert second.resolution_method == "automatic_match"
    assert second.vector_score == pytest.approx(1.0)
    assert gateway.judge_calls == []


def test_middle_band_judge_is_limited_to_retrieved_candidates() -> None:
    store, policy, _, run = successful_extraction_run()
    gateway = StubResolutionGateway([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    resolver = EntityResolver(store, gateway)
    endpoint = ModelEndpointConfig(embedding_dimensions=3)
    first = resolver.resolve_run(run, endpoint, policy)[0]
    gateway.judgments = [{"action": "match", "entity_id": first.entity_id}]
    output = json.loads(json.dumps(run.output))
    output["mentions"][0]["surface"] = "records archive"
    later = replace(run, run_id="judge-run", run_key="judge-key", output=output)

    second = resolver.resolve_run(later, endpoint, policy)[0]

    assert second.entity_id == first.entity_id
    assert second.resolution_method == "judged_match"
    assert gateway.judge_calls[0][1][0]["entity_id"] == first.entity_id


def test_identity_judge_cannot_select_outside_candidate_set() -> None:
    store, policy, _, run = successful_extraction_run()
    gateway = StubResolutionGateway([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    resolver = EntityResolver(store, gateway)
    endpoint = ModelEndpointConfig(embedding_dimensions=3)
    resolver.resolve_run(run, endpoint, policy)
    gateway.judgments = [{"action": "match", "entity_id": "invented"}]
    output = json.loads(json.dumps(run.output))
    output["mentions"][0]["surface"] = "records archive"
    later = replace(run, run_id="invalid-judge-run", run_key="invalid-judge-key", output=output)

    with pytest.raises(ValueError, match="outside the candidate set"):
        resolver.resolve_run(later, endpoint, policy)


def test_remote_resolution_fails_closed_for_unsanitized_policy() -> None:
    store, policy, _, run = successful_extraction_run()
    gateway = StubResolutionGateway([[1.0, 0.0, 0.0]])
    resolver = EntityResolver(store, gateway)

    with pytest.raises(PermissionError, match="derived_only or unrestricted"):
        resolver.resolve_run(
            run,
            ModelEndpointConfig(
                embedding_processing_location="remote", embedding_dimensions=3
            ),
            policy,
        )
    assert gateway.embed_calls == []


def test_mining_work_completes_only_after_entity_resolution() -> None:
    policy = InstancePolicy(mining_status="active")
    store, policy, capture, now = admitted_mining_fixture(policy)
    resolution_gateway = StubResolutionGateway([[1.0, 0.0, 0.0]])
    resolver = EntityResolver(store, resolution_gateway)
    worker = MiningWorker(
        store,
        StubExtractionGateway([extraction_for(capture.capture_id)]),
        worker_id="mining-a",
        policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(embedding_dimensions=3),
        resolver=resolver,
        committer=ClaimCommitter(store),
    )

    runs = worker.process_once(now)

    assert len(runs) == 1
    assert store.mining_work_items[0].state == "completed"
    assert len(store.resolved_mentions) == 1
    assert len(store.entities) == 1
    assert len(store.claims) == 1
    assert len(store.evidence_records) == 1


def test_claim_commit_is_schema_backed_and_idempotent() -> None:
    store, policy, capture, run = successful_extraction_run()
    EntityResolver(store, StubResolutionGateway([[1.0, 0.0, 0.0]])).resolve_run(
        run, ModelEndpointConfig(embedding_dimensions=3), policy
    )
    committer = ClaimCommitter(store)

    first = committer.commit_run(run)
    repeated = committer.commit_run(run)

    assert repeated == first
    assert len(store.claims) == 1
    assert len(store.evidence_records) == 1
    claim = first.claims[0]
    assert claim.subject_entity_id == store.entities[0].entity_id
    assert claim.relation == "storage_location"
    assert claim.object_literal == "local"
    assert claim.object_entity_id is None
    assert claim.statefulness == "stateful"
    assert claim.cardinality == "one"
    assert claim.schema_version == "core-v1"
    evidence = first.evidence[0]
    assert evidence.claim_id == claim.claim_id
    assert evidence.capture_id == capture.capture_id
    assert evidence.run_id == run.run_id
    assert store.list_claims(limit=1) == [claim]
    assert store.list_evidence(limit=1) == [evidence]
    notices = store.list_maintenance_notices()
    assert len(notices) == 1
    assert notices[0].kind == "missing_temporal_bounds"
    assert notices[0].claim_id == claim.claim_id


def test_scoped_remining_keeps_old_claim_until_replacement_commit() -> None:
    policy = InstancePolicy(mining_status="active")
    store, policy, capture, now = admitted_mining_fixture(policy)
    first_output = extraction_for(capture.capture_id)
    replacement_output = json.loads(json.dumps(first_output))
    replacement_output["candidate_claims"][0]["object"] = "remote"
    gateway = StubExtractionGateway([first_output, replacement_output])
    worker = MiningWorker(
        store, gateway, worker_id="mining-a", policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(embedding_dimensions=3),
        resolver=EntityResolver(store, StubResolutionGateway([[1.0, 0.0, 0.0]])),
        committer=ClaimCommitter(store),
    )
    source_run = worker.process_once(now)[0]
    source_claim = store.claims[0]
    request = KnowledgeMaintainer(store, load_relation_schema()).request_remining(
        source_run.run_id, "Correct the storage location", now=now + timedelta(seconds=1)
    )

    assert source_claim.lifecycle == "active"
    assert store.evidence_records[0].lifecycle == "active"
    replacement_run = worker.process_once(now + timedelta(seconds=1))[0]

    assert replacement_run.run_id != source_run.run_id
    assert gateway.calls[1][0].parent_run_id is None
    assert gateway.calls[1][0].evidence_eligible_capture_ids == [capture.capture_id]
    assert {item.object_literal for item in store.claims if item.lifecycle == "active"} == {"remote"}
    assert next(item for item in store.claims if item.claim_id == source_claim.claim_id).lifecycle == "superseded"
    completed = store.list_remining_requests()[0]
    assert completed.status == "completed"
    assert completed.replacement_run_id == replacement_run.run_id
    assert any(item.kind == "remining_applied" for item in store.list_maintenance_notices())


def test_extension_relation_is_conservatively_admitted_promoted_and_reverted() -> None:
    store, policy, _, run = successful_extraction_run()
    output = json.loads(json.dumps(run.output))
    output["candidate_claims"][0]["relation"] = "ext:resides_on"
    changed = replace(run, run_id="extension-run", run_key="extension-key", output=output)
    EntityResolver(store, StubResolutionGateway([[1.0, 0.0, 0.0]])).resolve_run(
        changed, ModelEndpointConfig(embedding_dimensions=3), policy
    )
    claim = ClaimCommitter(store).commit_run(changed).claims[0]
    extension = store.get_extension_relation("ext:resides_on")

    assert extension is not None
    assert extension.status == "admitted"
    assert extension.definition["statefulness"] == "eventive"
    assert extension.definition["cardinality"] == "many"
    assert claim.relation == "ext:resides_on"

    maintainer = KnowledgeMaintainer(store, load_relation_schema())
    promoted = maintainer.promote_extension(
        "ext:resides_on", "storage_location", "abcdef1", now=run.completed_at
    )
    assert promoted.status == "promoted"
    assert promoted.migration_plan["strategy"] == "alias_then_reprocess"
    reverted = maintainer.revert_extension(
        "ext:resides_on", "abcdef2", now=run.completed_at + timedelta(seconds=1)
    )
    assert reverted.status == "reverted"
    assert store.claims[0].lifecycle == "superseded"
    assert store.evidence_records[0].lifecycle == "superseded"


def test_unknown_relation_fails_closed_without_graph_writes() -> None:
    store, policy, _, run = successful_extraction_run()
    resolver = EntityResolver(store, StubResolutionGateway([[1.0, 0.0, 0.0]]))
    output = json.loads(json.dumps(run.output))
    output["candidate_claims"][0]["relation"] = "invented_relation"
    changed = replace(run, run_id="unknown-relation-run", run_key="unknown-relation-key", output=output)
    resolver.resolve_run(changed, ModelEndpointConfig(embedding_dimensions=3), policy)

    with pytest.raises(ValueError, match="not registered"):
        ClaimCommitter(store).commit_run(changed)

    assert store.claims == ()
    assert store.evidence_records == ()
    assert store.is_claim_commit_complete(changed.run_id) is False


def test_all_candidates_validate_before_any_graph_write() -> None:
    store, policy, _, run = successful_extraction_run()
    resolver = EntityResolver(store, StubResolutionGateway([[1.0, 0.0, 0.0]]))
    output = json.loads(json.dumps(run.output))
    invalid = json.loads(json.dumps(output["candidate_claims"][0]))
    invalid["relation"] = "invented_relation"
    output["candidate_claims"].append(invalid)
    changed = replace(run, run_id="atomic-validation-run", run_key="atomic-validation-key", output=output)
    resolver.resolve_run(changed, ModelEndpointConfig(embedding_dimensions=3), policy)

    with pytest.raises(ValueError, match="not registered"):
        ClaimCommitter(store).commit_run(changed)

    assert store.claims == ()
    assert store.evidence_records == ()


def test_schema_invalid_extraction_is_not_reused_on_worker_retry() -> None:
    policy = InstancePolicy(mining_status="active")
    store, policy, capture, now = admitted_mining_fixture(policy)
    invalid = extraction_for(capture.capture_id)
    invalid["candidate_claims"][0]["relation"] = "invented_relation"
    gateway = StubExtractionGateway([invalid, extraction_for(capture.capture_id)])
    worker = MiningWorker(
        store,
        gateway,
        worker_id="mining-a",
        policy=lambda: policy,
        endpoint=lambda: ModelEndpointConfig(embedding_dimensions=3),
        resolver=EntityResolver(store, StubResolutionGateway([[1.0, 0.0, 0.0]])),
        committer=ClaimCommitter(store),
        retry_after=timedelta(seconds=5),
    )

    assert worker.process_once(now) == []
    assert store.mining_runs[0].status == "failed"
    completed = worker.process_once(now + timedelta(seconds=5))

    assert len(completed) == 1
    assert len(gateway.calls) == 2
    assert len(store.claims) == 1
    assert store.mining_work_items[0].state == "completed"


def test_entity_object_claim_requires_both_surfaces_to_be_resolved() -> None:
    store, policy, capture, run = successful_extraction_run()
    evidence = extraction_for(capture.capture_id)["mentions"][0]["evidence"]
    output = json.loads(json.dumps(run.output))
    output["mentions"] = [
        {
            "surface": "Maya",
            "entity_type": "person",
            "context": "Maya works for Northwind",
            "evidence": evidence,
        },
        {
            "surface": "Northwind",
            "entity_type": "organization",
            "context": "Maya works for Northwind",
            "evidence": evidence,
        },
    ]
    output["candidate_claims"] = [{
        "subject": "Maya",
        "relation": "works_for",
        "object": "Northwind",
        "object_kind": "entity",
        "evidence": [evidence],
        "valid_from": None,
        "valid_to": None,
    }]
    changed = replace(run, run_id="entity-object-run", run_key="entity-object-key", output=output)
    EntityResolver(
        store, StubResolutionGateway([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    ).resolve_run(changed, ModelEndpointConfig(embedding_dimensions=3), policy)

    claim = ClaimCommitter(store).commit_run(changed).claims[0]

    assert claim.object_kind == "entity"
    assert claim.object_entity_id is not None
    assert claim.object_literal is None
    assert claim.subject_entity_id != claim.object_entity_id


def test_independent_capture_adds_evidence_to_the_same_claim() -> None:
    store, policy, _, run = successful_extraction_run()
    resolver = EntityResolver(
        store, StubResolutionGateway([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    )
    endpoint = ModelEndpointConfig(embedding_dimensions=3)
    resolver.resolve_run(run, endpoint, policy)
    committer = ClaimCommitter(store)
    first = committer.commit_run(run)

    later_capture = envelope(
        capture_id="018f5e2a-1234-7abc-8def-1234567890dd",
        occurred_at="2026-07-10T13:00:00Z",
        payload={"role": "user", "content": "We decided to keep all deployment data local."},
    )
    CaptureService(store, policy).ingest(later_capture, run.completed_at + timedelta(hours=1))
    output = json.loads(json.dumps(run.output))
    for section in ("episodes", "candidate_claims"):
        for item in output[section]:
            for reference in item["evidence"]:
                reference["capture_id"] = later_capture.capture_id
    output["mentions"][0]["evidence"]["capture_id"] = later_capture.capture_id
    later = replace(
        run,
        run_id="independent-evidence-run",
        run_key="independent-evidence-key",
        output=output,
        evidence_capture_ids=[later_capture.capture_id],
        completed_at=run.completed_at + timedelta(hours=1),
    )
    resolver.resolve_run(later, endpoint, policy)
    second = committer.commit_run(later)

    assert second.claims[0].claim_id == first.claims[0].claim_id
    assert len(store.claims) == 1
    assert len(store.evidence_records) == 2
    assert {item.capture_id for item in store.evidence_records} == {
        run.evidence_capture_ids[0], later_capture.capture_id
    }


def test_relation_schema_is_versioned_and_extraction_temporal_bounds_are_ordered() -> None:
    schema = load_relation_schema()

    assert schema.schema_version == "core-v1"
    assert schema.relations["storage_location"].object_kind == "literal"
    invalid = extraction_for(CAPTURE_ID)
    invalid["candidate_claims"][0]["valid_from"] = "2026-07-11T00:00:00Z"
    invalid["candidate_claims"][0]["valid_to"] = "2026-07-10T00:00:00Z"
    with pytest.raises(ValidationError, match="valid_to"):
        from app.v2.mining import ExtractionOutput
        ExtractionOutput.model_validate(invalid)


class StubQueryGateway:
    def __init__(self, *, selected_index=0, answer=None) -> None:
        self.selected_index = selected_index
        self.answer = answer or {"answer": "Deployment data is stored locally [E1].", "citations": ["E1"]}
        self.embed_calls = []
        self.plan_calls = []
        self.synthesize_calls = []

    def embed(self, texts, endpoint):
        self.embed_calls.append((texts, endpoint))
        return [[1.0, 0.0, 0.0] for _ in texts]

    def plan(self, question, candidates, relations, endpoint):
        self.plan_calls.append((question, candidates, relations, endpoint))
        return {
            "seed_entity_ids": [candidates[self.selected_index]["entity_id"]],
            "relations": ["storage_location"],
            "direction": "outgoing",
        }

    def synthesize(self, question, bundle, token_budget, endpoint):
        self.synthesize_calls.append((question, bundle, token_budget, endpoint))
        return self.answer


def grounded_query_fixture(*, processing_location="local"):
    store = MemoryCaptureStore()
    policy = InstancePolicy(mining_status="active")
    now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    content = "Deployment data is stored locally."
    capture = envelope(payload={"role": "user", "content": content})
    CaptureService(store, policy).ingest(capture, now)
    entity = store.put_entity(Entity(
        entity_id="entity-deployment-data",
        entity_key="entity-key-deployment-data",
        canonical_surface="deployment data",
        normalized_surface="deployment data",
        entity_type="data_collection",
        created_at=now,
    ))
    mention = store.put_resolved_mention(ResolvedMention(
        mention_id="mention-deployment-data",
        mention_key="mention-key-deployment-data",
        run_id="query-source-run",
        capture_id=capture.capture_id,
        source_start=0,
        source_end=len("Deployment data"),
        surface="deployment data",
        normalized_surface="deployment data",
        entity_type="data_collection",
        context=content,
        embedding=[1.0, 0.0, 0.0],
        embedding_model="embeddings-local/loreholm-embeddings",
        resolver_version="entity-resolver-v1",
        entity_id=entity.entity_id,
        resolution_method="automatic_mint",
        vector_score=None,
        string_score=None,
        combined_score=None,
        decision_details={},
        created_at=now,
    ))
    claim = store.put_claim(Claim(
        claim_id="claim-storage",
        claim_key="claim-key-storage",
        subject_entity_id=entity.entity_id,
        relation="storage_location",
        object_kind="literal",
        object_entity_id=None,
        object_literal="local",
        object_literal_norm="local",
        valid_from=None,
        valid_to=None,
        recorded_at=now,
        statefulness="stateful",
        cardinality="one",
        schema_version="core-v1",
        lifecycle="active",
        first_run_id="query-source-run",
    ))
    evidence = store.put_evidence(Evidence(
        evidence_id="evidence-storage",
        evidence_key="evidence-key-storage",
        claim_id=claim.claim_id,
        capture_id=capture.capture_id,
        source_start=0,
        source_end=len(content),
        run_id="query-source-run",
        miner_version="structured-extractor-v1",
        schema_version="core-v1",
        lifecycle="active",
        recorded_at=now,
    ))
    endpoint = ModelEndpointConfig(
        processing_location=processing_location,
        embedding_dimensions=3,
    )
    store.set_config("resolution_embedding_region", {
        "model": "embeddings-local/loreholm-embeddings",
        "dimensions": 3,
    })
    return store, policy, endpoint, entity, mention, claim, evidence, now


def test_grounded_query_uses_vector_mentions_as_graph_seed_and_hydrates_evidence() -> None:
    store, policy, endpoint, entity, _, claim, evidence, now = grounded_query_fixture()
    gateway = StubQueryGateway()
    service = GroundedQueryService(
        store, gateway, load_relation_schema(), policy=lambda: policy, endpoint=lambda: endpoint
    )

    result = service.query(GroundedQueryRequest(
        query="Where is the deployment information stored?",
        as_of=now + timedelta(days=1),
    ))

    assert result.status == "ok"
    assert result.seeds[0].entity_id == entity.entity_id
    assert result.seeds[0].selected is True
    assert result.claims[0].claim_id == claim.claim_id
    assert result.claims[0].evidence_ids == [evidence.evidence_id]
    assert result.evidence[0].excerpt == "Deployment data is stored locally."
    assert result.evidence[0].citation == "E1"
    assert result.answer == "Deployment data is stored locally [E1]."
    assert result.citations == ["E1"]
    assert gateway.plan_calls[0][1][0]["entity_id"] == entity.entity_id
    assert gateway.synthesize_calls[0][1]["evidence"][0]["citation"] == "E1"


def test_query_groups_multiple_vector_mentions_into_one_seed() -> None:
    store, policy, endpoint, entity, mention, _, _, now = grounded_query_fixture()
    store.put_resolved_mention(replace(
        mention,
        mention_id="mention-deployment-data-2",
        mention_key="mention-key-deployment-data-2",
        surface="the deployment records",
        embedding=[0.99, 0.01, 0.0],
    ))
    service = GroundedQueryService(
        store, StubQueryGateway(), load_relation_schema(),
        policy=lambda: policy, endpoint=lambda: endpoint,
    )

    result = service.query(GroundedQueryRequest(
        query="Where are the deployment records?", as_of=now, answer=False
    ))

    assert len([item for item in result.seeds if item.entity_id == entity.entity_id]) == 1
    assert result.seeds[0].hit_count == 2
    assert result.trace.synthesis == "not_requested"


def test_query_excludes_superseded_and_future_claims_from_current_reads() -> None:
    store, policy, endpoint, _, _, claim, evidence, now = grounded_query_fixture()
    future = store.put_claim(replace(
        claim,
        claim_id="claim-future",
        claim_key="claim-key-future",
        object_literal="future location",
        object_literal_norm="future location",
        valid_from=now + timedelta(days=2),
    ))
    superseded = store.put_claim(replace(
        claim,
        claim_id="claim-superseded",
        claim_key="claim-key-superseded",
        object_literal="old location",
        object_literal_norm="old location",
        lifecycle="superseded",
    ))
    store.put_evidence(replace(
        evidence, evidence_id="evidence-future", evidence_key="evidence-key-future",
        claim_id=future.claim_id,
    ))
    store.put_evidence(replace(
        evidence, evidence_id="evidence-superseded", evidence_key="evidence-key-superseded",
        claim_id=superseded.claim_id, lifecycle="superseded",
    ))
    service = GroundedQueryService(
        store, StubQueryGateway(), load_relation_schema(),
        policy=lambda: policy, endpoint=lambda: endpoint,
    )

    result = service.query(GroundedQueryRequest(
        query="Where is it stored?", as_of=now + timedelta(days=1), answer=False
    ))

    assert [item.claim_id for item in result.claims] == [claim.claim_id]


def test_query_warns_when_vector_seed_has_a_near_scoring_alternative() -> None:
    store, policy, endpoint, _, mention, _, _, now = grounded_query_fixture()
    alternative = store.put_entity(Entity(
        entity_id="entity-alternative",
        entity_key="entity-key-alternative",
        canonical_surface="deployment archive",
        normalized_surface="deployment archive",
        entity_type="data_collection",
        created_at=now,
    ))
    store.put_resolved_mention(replace(
        mention,
        mention_id="mention-alternative",
        mention_key="mention-key-alternative",
        entity_id=alternative.entity_id,
        surface="deployment archive",
        normalized_surface="deployment archive",
        embedding=[0.999, 0.001, 0.0],
    ))
    service = GroundedQueryService(
        store, StubQueryGateway(), load_relation_schema(),
        policy=lambda: policy, endpoint=lambda: endpoint,
    )

    result = service.query(GroundedQueryRequest(
        query="deployment", as_of=now, answer=False, ambiguity_margin=0.1
    ))

    assert any(item.code == "ambiguous_seed" for item in result.warnings)


def test_remote_grounded_synthesis_fails_closed_without_unrestricted_policy() -> None:
    store, policy, endpoint, _, _, _, _, now = grounded_query_fixture(
        processing_location="remote"
    )
    policy.classes["transcript.message"].remote_processing = "derived_only"
    gateway = StubQueryGateway()
    service = GroundedQueryService(
        store, gateway, load_relation_schema(), policy=lambda: policy, endpoint=lambda: endpoint
    )

    with pytest.raises(PermissionError, match="unrestricted"):
        service.query(GroundedQueryRequest(query="Where is it stored?", as_of=now))
    assert len(gateway.plan_calls) == 1
    assert gateway.synthesize_calls == []


def test_grounded_answer_rejects_citations_outside_the_evidence_bundle() -> None:
    store, policy, endpoint, _, _, _, _, now = grounded_query_fixture()
    gateway = StubQueryGateway(answer={"answer": "Unsupported [E99].", "citations": ["E99"]})
    service = GroundedQueryService(
        store, gateway, load_relation_schema(), policy=lambda: policy, endpoint=lambda: endpoint
    )

    with pytest.raises(ValueError, match="outside the query bundle"):
        service.query(GroundedQueryRequest(query="Where is it stored?", as_of=now))


def test_bifrost_query_gateway_requests_strict_planner_schema(monkeypatch) -> None:
    seen = {}

    class Response:
        is_error = False

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "seed_entity_ids": ["entity-1"],
                "relations": ["storage_location"],
                "direction": "outgoing",
            })}}]}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["payload"] = kwargs["json"]
        return Response()

    monkeypatch.setattr("app.v2.query.httpx.post", fake_post)
    result = BifrostQueryGateway("http://bifrost:8080", ("user", "pass")).plan(
        "Where is Atlas stored?",
        [{"entity_id": "entity-1", "canonical_surface": "Atlas"}],
        {"storage_location": {"description": "Storage location"}},
        ModelEndpointConfig(),
    )

    assert result["seed_entity_ids"] == ["entity-1"]
    assert seen["url"] == "http://bifrost:8080/v1/chat/completions"
    assert seen["payload"]["response_format"]["json_schema"]["strict"] is True


def test_bifrost_resolution_gateway_uses_embedding_route(monkeypatch) -> None:
    seen = {}

    class Response:
        is_error = False
        text = ""

        def json(self):
            return {"data": [{"index": 0, "embedding": [1, 2, 3]}]}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["payload"] = kwargs["json"]
        return Response()

    monkeypatch.setattr("app.v2.resolution.httpx.post", fake_post)

    vectors = BifrostResolutionGateway("http://bifrost:8080", ("user", "pass")).embed(
        ["mention context"],
        ModelEndpointConfig(embedding_model_name="embed-v1", embedding_dimensions=3),
    )

    assert vectors == [[1.0, 2.0, 3.0]]
    assert seen["url"] == "http://bifrost:8080/v1/embeddings"
    assert seen["payload"]["model"] == "embeddings-local/embed-v1"
    assert seen["payload"]["dimensions"] == 3
