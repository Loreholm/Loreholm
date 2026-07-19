from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.v2.models import CaptureEnvelope, InstancePolicy
from app.v2.service import ArcadeCaptureStore, CaptureService, MemoryCaptureStore, _epoch_millis


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


def test_mining_is_unavailable_until_a_worker_exists() -> None:
    assert InstancePolicy().mining_status == "unavailable_not_implemented"
    with pytest.raises(ValidationError, match="unavailable_not_implemented"):
        InstancePolicy.model_validate({"mining_status": "active"})


def test_stored_active_mining_policy_is_migrated_closed() -> None:
    store = MemoryCaptureStore()
    stored = InstancePolicy().model_dump(mode="json")
    stored["mining_status"] = "active"
    store.set_config("policy", stored)
    service = CaptureService(store, InstancePolicy())

    service.load_policy()

    assert service.policy.mining_status == "unavailable_not_implemented"
    assert store.get_config("policy")["mining_status"] == "unavailable_not_implemented"


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
