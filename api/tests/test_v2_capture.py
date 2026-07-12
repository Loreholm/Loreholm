from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.v2.models import CaptureEnvelope, InstancePolicy
from app.v2.service import CaptureService, MemoryCaptureStore


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
