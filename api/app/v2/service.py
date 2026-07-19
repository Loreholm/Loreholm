from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
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


class MemoryCaptureStore(CaptureStore):
    """Deterministic development/test store, never selected in production."""

    def __init__(self) -> None:
        self._captures: dict[str, StoredCapture] = {}
        self._config: dict[str, dict] = {}
        self._sessions: dict[str, StoredSession] = {}
        self._session_by_scope: dict[str, str] = {}
        self._work: dict[str, WorkItem] = {}
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


class ArcadeCaptureStore(CaptureStore):
    """ArcadeDB document-store adapter for the append-only capture region."""

    def __init__(self, base_url: str, database: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.database = database
        self.command_url = f"{self.base_url}/api/v1/command/{database}"
        self.auth = (username, password)
        self.timeout = httpx.Timeout(15.0, connect=3.0)

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
            if stored.get("mining_status") != "unavailable_not_implemented":
                stored = {**stored, "mining_status": "unavailable_not_implemented"}
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
