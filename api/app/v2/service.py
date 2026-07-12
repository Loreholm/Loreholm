from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
import json
import httpx

from .models import CaptureEnvelope, CaptureReceipt, InstancePolicy


@dataclass(frozen=True)
class StoredCapture:
    envelope: CaptureEnvelope
    received_at: datetime
    normalized_at: datetime
    state: str


class CaptureStore:
    """Persistence seam; the V2 ArcadeDB adapter implements this contract."""

    def put_if_absent(self, capture: StoredCapture) -> bool:
        raise NotImplementedError

    def get_config(self, key: str) -> dict | None:
        raise NotImplementedError

    def set_config(self, key: str, value: dict) -> None:
        raise NotImplementedError


class MemoryCaptureStore(CaptureStore):
    """Deterministic development/test store, never selected in production."""

    def __init__(self) -> None:
        self._captures: dict[str, StoredCapture] = {}
        self._config: dict[str, dict] = {}
        self._lock = Lock()

    def put_if_absent(self, capture: StoredCapture) -> bool:
        with self._lock:
            if capture.envelope.capture_id in self._captures:
                return False
            self._captures[capture.envelope.capture_id] = capture
            return True

    def get_config(self, key: str) -> dict | None:
        return self._config.get(key)

    def set_config(self, key: str, value: dict) -> None:
        self._config[key] = value


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
                    "occurred_at": capture.envelope.occurred_at.isoformat(),
                    "received_at": capture.received_at.isoformat(),
                    "normalized_at": capture.normalized_at.isoformat(),
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


class CaptureService:
    def __init__(
        self,
        store: CaptureStore,
        policy: InstancePolicy,
        *,
        clock_skew_tolerance: timedelta = timedelta(hours=6),
    ) -> None:
        self.store = store
        self.policy = policy
        self.clock_skew_tolerance = clock_skew_tolerance

    def load_policy(self) -> None:
        stored = self.store.get_config("policy")
        if stored:
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
        known_class = envelope.capture_class in self.policy.classes
        state = "staged" if known_class else "quarantined_unknown_class"
        inserted = self.store.put_if_absent(StoredCapture(
            envelope=envelope,
            received_at=received_at,
            normalized_at=normalized_at,
            state=state,
        ))
        status = "duplicate" if not inserted else ("accepted" if known_class else "quarantined")
        return CaptureReceipt(
            capture_id=envelope.capture_id,
            status=status,
            received_at=received_at,
            normalized_at=normalized_at,
            clock_adjusted=clock_adjusted,
        )
