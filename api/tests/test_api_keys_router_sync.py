import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import Request

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.api_keys import router as api_keys_router  # noqa: E402
from app.api_keys.schemas import CreateApiKeyRequest  # noqa: E402
from app.services import api_key_auth  # noqa: E402
from app.services.local_sync import LocalSyncError  # noqa: E402


class _Store:
    def __init__(self):
        self.saved_target_id = None

    async def cleanup_expired_keys(self, _user_id: str) -> int:
        return 0

    async def can_create_key(self, _user_id: str) -> bool:
        return True

    async def store_key_metadata(
        self,
        user_id: str,
        key_id: str,
        name: str,
        expires_at: datetime,
        database=None,
        database_target_id=None,
    ) -> None:
        assert user_id == "user-1"
        assert key_id == "ak_test"
        assert name == "Sync key"
        assert expires_at.tzinfo is not None
        self.saved_target_id = database_target_id


def _build_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api-keys",
            "headers": [],
            "query_string": b"",
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def test_create_key_with_database_sync(monkeypatch) -> None:
    store = _Store()

    async def _fake_user(_request):
        return {"sub": "user-1", "email": "user@example.com"}

    async def _fake_get_store():
        return store

    async def _fake_sync_payload(_user_id: str, _database_id: str):
        return {
            "database_id": "work-db",
            "profile": {"profile_id": "memory-work", "profile_version": 3},
        }

    async def _fake_upsert(_user_id: str, _sync_payload: dict):
        return {
            "target_id": "dt_sync",
            "name": "work-db",
            "database_id": "work-db",
            "created_at": "2026-02-15T00:00:00+00:00",
            "updated_at": "2026-02-15T00:00:00+00:00",
        }

    def _fake_create_api_key(**_kwargs):
        return {
            "api_key": "v4.local.test",
            "key_id": "ak_test",
            "name": "Sync key",
            "created_at": "2026-02-15T00:00:00+00:00",
            "expires_at": datetime.now(timezone.utc).isoformat(),
            "database_target_id": "dt_sync",
        }

    monkeypatch.setenv("API_KEY_SIGNING_SECRET", "dGVzdA==")
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setattr(api_keys_router, "get_current_user", _fake_user)
    monkeypatch.setattr(api_keys_router, "get_api_key_store", _fake_get_store)
    monkeypatch.setattr(api_keys_router, "fetch_local_database_sync_payload", _fake_sync_payload)
    monkeypatch.setattr(api_keys_router, "upsert_database_target_from_sync", _fake_upsert)
    monkeypatch.setattr(api_key_auth, "create_api_key", _fake_create_api_key)

    payload = CreateApiKeyRequest(
        name="Sync key",
        expires_days=365,
        database_sync={"database_id": "work-db"},
    )

    result = asyncio.run(api_keys_router.create_key(_build_request(), payload))

    assert result.key_id == "ak_test"
    assert result.database is not None
    assert result.database.target_id == "dt_sync"
    assert store.saved_target_id == "dt_sync"


def test_create_key_with_database_sync_failure_is_atomic(monkeypatch) -> None:
    store = _Store()

    async def _fake_user(_request):
        return {"sub": "user-1", "email": "user@example.com"}

    async def _fake_get_store():
        return store

    async def _fail_sync(_user_id: str, _database_id: str):
        raise LocalSyncError("LOCAL_SYNC_UNREACHABLE", "cannot reach local node", 502)

    def _should_not_create(**_kwargs):
        raise AssertionError("create_api_key should not run when sync fails")

    monkeypatch.setenv("API_KEY_SIGNING_SECRET", "dGVzdA==")
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setattr(api_keys_router, "get_current_user", _fake_user)
    monkeypatch.setattr(api_keys_router, "get_api_key_store", _fake_get_store)
    monkeypatch.setattr(api_keys_router, "fetch_local_database_sync_payload", _fail_sync)
    monkeypatch.setattr(api_key_auth, "create_api_key", _should_not_create)

    payload = CreateApiKeyRequest(
        name="Sync key",
        expires_days=365,
        database_sync={"database_id": "work-db"},
    )

    with pytest.raises(api_keys_router.HTTPException) as excinfo:
        asyncio.run(api_keys_router.create_key(_build_request(), payload))

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail["error"]["code"] == "LOCAL_SYNC_UNREACHABLE"
