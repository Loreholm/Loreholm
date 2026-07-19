from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from .service import CaptureStore, SalienceRecord, StoredCapture, WorkItem, _new_uuid7


ALGORITHM_VERSION = "mechanical-salience-v1"
_WHITESPACE = re.compile(r"\s+")
_TEXT_FIELDS = ("content", "text", "body", "selection", "title")
_TOOL_ROLES = {"tool", "function"}


@dataclass(frozen=True)
class SalienceConfig:
    max_capture_chars: int = 12_000
    max_scope_chars: int = 50_000
    minimum_user_chars: int = 40
    minimum_turns: int = 3

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if value < 1:
                raise ValueError(f"{name} must be at least 1")


def _capture_text(capture: StoredCapture) -> str:
    parts: list[str] = []
    for field in _TEXT_FIELDS:
        value = capture.envelope.payload.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n\n".join(parts)


def _normalized_repeat_key(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip().casefold()


def mechanically_trim(
    captures: list[StoredCapture],
    config: SalienceConfig,
) -> tuple[list[dict], dict]:
    """Build a bounded derived view without modifying stored capture bytes."""
    trimmed: list[dict] = []
    seen_text: set[str] = set()
    scope_chars = 0
    removed_tool_items = 0
    removed_repeats = 0
    truncated_items = 0
    omitted_for_scope_limit = 0

    for capture in captures:
        role_value = capture.envelope.payload.get("role")
        role = role_value.strip().lower() if isinstance(role_value, str) else "unknown"
        if role in _TOOL_ROLES:
            removed_tool_items += 1
            continue
        text = _capture_text(capture)
        if not text:
            continue
        repeat_key = _normalized_repeat_key(text)
        if repeat_key in seen_text:
            removed_repeats += 1
            continue
        seen_text.add(repeat_key)

        original_chars = len(text)
        if original_chars > config.max_capture_chars:
            text = text[: config.max_capture_chars]
            truncated_items += 1
        remaining = config.max_scope_chars - scope_chars
        if remaining <= 0:
            omitted_for_scope_limit += 1
            continue
        if len(text) > remaining:
            text = text[:remaining]
            truncated_items += 1
        scope_chars += len(text)
        trimmed.append({
            "capture_id": capture.envelope.capture_id,
            "normalized_at": capture.normalized_at.isoformat(),
            "role": role,
            "content": text,
            "original_chars": original_chars,
            "retained_chars": len(text),
        })

    signals = {
        "source_capture_count": len(captures),
        "retained_capture_count": len(trimmed),
        "retained_chars": scope_chars,
        "removed_tool_items": removed_tool_items,
        "removed_repeats": removed_repeats,
        "truncated_items": truncated_items,
        "omitted_for_scope_limit": omitted_for_scope_limit,
    }
    return trimmed, signals


def evaluate_salience(
    work: WorkItem,
    captures: list[StoredCapture],
    *,
    config: SalienceConfig = SalienceConfig(),
    now: datetime | None = None,
) -> SalienceRecord:
    trimmed, trim_signals = mechanically_trim(captures, config)
    user_items = [item for item in trimmed if item["role"] in {"user", "human"}]
    user_chars = sum(int(item["retained_chars"]) for item in user_items)
    turn_count = len(trimmed)
    explicit_push = work.work_type == "push_admission"
    admitted = explicit_push or user_chars >= config.minimum_user_chars or (
        bool(user_items) and turn_count >= config.minimum_turns
    )
    if explicit_push:
        reason = "explicit_push"
    elif user_chars >= config.minimum_user_chars:
        reason = "user_authored_volume"
    elif user_items and turn_count >= config.minimum_turns:
        reason = "conversation_turns"
    else:
        reason = "below_salience_threshold"

    signals = {
        **trim_signals,
        "surface": captures[0].envelope.surface if captures else None,
        "user_authored_chars": user_chars,
        "user_turns": len(user_items),
        "turn_count": turn_count,
        "explicit_push": explicit_push,
        "thresholds": asdict(config),
    }
    fingerprint_payload = {
        "algorithm_version": ALGORITHM_VERSION,
        "work_key": work.work_key,
        "captures": [
            {
                "capture_id": item["capture_id"],
                "content_sha256": hashlib.sha256(item["content"].encode()).hexdigest(),
            }
            for item in trimmed
        ],
        "signals": signals,
    }
    input_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return SalienceRecord(
        salience_id=_new_uuid7(),
        work_id=work.work_id,
        work_key=work.work_key,
        work_type=work.work_type,
        scope_id=work.scope_id,
        generation=work.generation,
        status="admitted" if admitted else "skipped",
        reason=reason,
        algorithm_version=ALGORITHM_VERSION,
        input_fingerprint=input_fingerprint,
        signals=signals,
        trimmed=trimmed,
        created_at=(now or datetime.now(timezone.utc)).astimezone(timezone.utc),
    )


class SalienceWorker:
    def __init__(
        self,
        store: CaptureStore,
        worker_id: str,
        *,
        config: SalienceConfig = SalienceConfig(),
        lease_for: timedelta = timedelta(minutes=2),
        retry_after: timedelta = timedelta(seconds=30),
        batch_size: int = 10,
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.config = config
        self.lease_for = lease_for
        self.retry_after = retry_after
        self.batch_size = batch_size

    def process_once(self, now: datetime | None = None) -> list[SalienceRecord]:
        claimed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        work_items = self.store.claim_ready_work(
            self.worker_id,
            now=claimed_at,
            lease_for=self.lease_for,
            limit=self.batch_size,
        )
        processed: list[SalienceRecord] = []
        for work in work_items:
            try:
                record = self.store.get_salience_record(work.work_id)
                if record is None:
                    captures = self.store.captures_for_work(work)
                    if not captures:
                        raise RuntimeError("admission work has no source captures")
                    record = evaluate_salience(work, captures, config=self.config, now=claimed_at)
                    record = self.store.put_salience_record(record)
                if not self.store.complete_work(work.work_id, self.worker_id, now=claimed_at):
                    raise RuntimeError("worker lost its lease before completion")
                processed.append(record)
            except Exception as exc:
                self.store.retry_work(
                    work.work_id,
                    self.worker_id,
                    now=claimed_at,
                    retry_after=self.retry_after,
                    error=str(exc),
                )
        return processed
