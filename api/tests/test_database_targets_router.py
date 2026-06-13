import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app.database_targets import router as database_targets_router  # noqa: E402
from conftest import make_async_client  # noqa: E402


def _target_record() -> dict:
    return {
        "target_id": "dt_123",
        "name": "work-db",
        "database_id": "work-db",
        "created_at": "2026-02-14T00:00:00+00:00",
        "updated_at": "2026-02-14T00:00:00+00:00",
    }


@pytest.mark.anyio
async def test_list_database_targets(monkeypatch) -> None:
    async def _fake_user(_request):
        return {"sub": "user-1"}

    async def _fake_list(user_id: str):
        assert user_id == "user-1"
        return [_target_record()]

    monkeypatch.setattr(database_targets_router, "get_current_user", _fake_user)
    monkeypatch.setattr(database_targets_router, "list_database_targets", _fake_list)

    async with await make_async_client(app) as client:
        response = await client.get("/database-targets")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["targets"][0]["target_id"] == "dt_123"
    assert body["targets"][0]["database_id"] == "work-db"


@pytest.mark.anyio
async def test_delete_database_target_blocks_when_in_use(monkeypatch) -> None:
    async def _fake_user(_request):
        return {"sub": "user-1"}

    async def _fake_get_target(user_id: str, target_id: str):
        assert user_id == "user-1"
        assert target_id == "dt_123"
        return _target_record()

    class _Store:
        async def count_active_keys_for_target(self, user_id: str, target_id: str) -> int:
            assert user_id == "user-1"
            assert target_id == "dt_123"
            return 2

    async def _fake_get_store():
        return _Store()

    async def _should_not_delete(_user_id: str, _target_id: str):
        raise AssertionError("delete_database_target should not be called when target is in use")

    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setattr(database_targets_router, "get_current_user", _fake_user)
    monkeypatch.setattr(database_targets_router, "get_database_target", _fake_get_target)
    monkeypatch.setattr(database_targets_router, "get_api_key_store", _fake_get_store)
    monkeypatch.setattr(database_targets_router, "delete_database_target", _should_not_delete)

    async with await make_async_client(app) as client:
        response = await client.delete("/database-targets/dt_123")

    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["error"]["code"] == "TARGET_IN_USE"
