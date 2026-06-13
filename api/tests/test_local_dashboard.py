import errno
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.local_dashboard import main as local_dashboard_main  # noqa: E402
from app.local_dashboard.core import auth as _auth_mod  # noqa: E402
from app.local_dashboard.ai import bifrost as _bifrost_mod  # noqa: E402
from app.local_dashboard.core import config as _config_mod  # noqa: E402
from app.local_dashboard.db import graph as _graph_mod  # noqa: E402
from app.local_dashboard.db import registry as _registry_mod  # noqa: E402
from app.local_dashboard.ai import wizard_tools as _wizard_tools_mod  # noqa: E402
from app.local_dashboard.routes import agent as _routes_agent_mod  # noqa: E402
from app.local_dashboard.routes import databases as _routes_databases_mod  # noqa: E402
from app.local_dashboard.routes import sync as _routes_sync_mod  # noqa: E402
from app.local_dashboard.routes import wizard as _routes_wizard_mod  # noqa: E402
from conftest import make_async_client  # noqa: E402


def _patch_config(monkeypatch, name, value):
    """Patch a config constant everywhere it's been imported."""
    monkeypatch.setattr(_config_mod, name, value)
    # Also patch on any module that re-imports the constant directly.
    for mod in [_auth_mod, _registry_mod, _graph_mod,
                _routes_agent_mod, _routes_databases_mod,
                _routes_sync_mod, _routes_wizard_mod, local_dashboard_main]:
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, value)


def _write_registry(path: Path) -> None:
    payload = {
        "version": 1,
        "databases": [
            {
                "database_id": "default",
                "name": "default",
                "host": "127.0.0.1",
                "port": 2480,
                "profile_id": "memory-default",
                "profile_version": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


async def _handshake(client, token: str = "test-token") -> None:
    response = await client.post(
        "/api/auth/handshake",
        headers={"X-Local-Token": token},
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is True


@pytest.mark.anyio
async def test_list_databases_requires_token(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / "local-dashboard.token"
    registry_file = tmp_path / "databases.json"
    token_file.write_text("test-token", encoding="utf-8")
    _write_registry(registry_file)

    _patch_config(monkeypatch, "LOCAL_DASHBOARD_TOKEN_FILE", token_file)
    _patch_config(monkeypatch, "LOCAL_DASHBOARD_REGISTRY_FILE", registry_file)

    async with await make_async_client(local_dashboard_main.app) as client:
        response = await client.get("/api/databases")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_list_databases_returns_registry_entries(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / "local-dashboard.token"
    registry_file = tmp_path / "databases.json"
    token_file.write_text("test-token", encoding="utf-8")
    _write_registry(registry_file)

    _patch_config(monkeypatch, "LOCAL_DASHBOARD_TOKEN_FILE", token_file)
    _patch_config(monkeypatch, "LOCAL_DASHBOARD_REGISTRY_FILE", registry_file)
    monkeypatch.setattr(_graph_mod, "_server_port_open", lambda *_args, **_kwargs: True)

    async with await make_async_client(local_dashboard_main.app) as client:
        await _handshake(client)
        response = await client.get("/api/databases")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["databases"][0]["database_id"] == "default"
    assert body["databases"][0]["status"] == "online"


@pytest.mark.anyio
async def test_profile_endpoint_returns_compat_payload(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / "local-dashboard.token"
    registry_file = tmp_path / "databases.json"
    token_file.write_text("test-token", encoding="utf-8")
    _write_registry(registry_file)

    _patch_config(monkeypatch, "LOCAL_DASHBOARD_TOKEN_FILE", token_file)
    _patch_config(monkeypatch, "LOCAL_DASHBOARD_REGISTRY_FILE", registry_file)

    async with await make_async_client(local_dashboard_main.app) as client:
        await _handshake(client)
        response = await client.get("/api/databases/default/profile")

    assert response.status_code == 200
    body = response.json()
    assert body["profile_id"] == "memory-default"
    assert body["profile_version"] == 1
    assert body["tool_schema_status"] == "deferred"


def test_sync_resolve_requires_bearer_token(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / "local-dashboard.token"
    sync_token_file = tmp_path / "local-sync.token"
    registry_file = tmp_path / "databases.json"
    token_file.write_text("test-token", encoding="utf-8")
    sync_token_file.write_text("sync-token", encoding="utf-8")
    _write_registry(registry_file)

    _patch_config(monkeypatch, "LOCAL_DASHBOARD_TOKEN_FILE", token_file)
    _patch_config(monkeypatch, "LOCAL_SYNC_TOKEN_FILE", sync_token_file)
    _patch_config(monkeypatch, "LOCAL_DASHBOARD_REGISTRY_FILE", registry_file)

    with pytest.raises(HTTPException) as excinfo:
        local_dashboard_main._verify_sync_bearer_token(None)
    assert excinfo.value.status_code == 401


def test_sync_resolve_returns_target_profile(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / "local-dashboard.token"
    sync_token_file = tmp_path / "local-sync.token"
    registry_file = tmp_path / "databases.json"
    token_file.write_text("test-token", encoding="utf-8")
    sync_token_file.write_text("sync-token", encoding="utf-8")
    _write_registry(registry_file)

    _patch_config(monkeypatch, "LOCAL_DASHBOARD_TOKEN_FILE", token_file)
    _patch_config(monkeypatch, "LOCAL_SYNC_TOKEN_FILE", sync_token_file)
    _patch_config(monkeypatch, "LOCAL_DASHBOARD_REGISTRY_FILE", registry_file)

    body = local_dashboard_main.sync_resolve_database_target(
        local_dashboard_main.SyncResolveRequest(database_id="default"),
        _=None,
    )
    assert body["database_id"] == "default"
    assert "target" not in body
    assert body["profile"]["profile_id"] == "memory-default"


def test_create_database_success(tmp_path, monkeypatch) -> None:
    registry_file = tmp_path / "databases.json"
    _write_registry(registry_file)
    _patch_config(monkeypatch, "LOCAL_DASHBOARD_REGISTRY_FILE", registry_file)

    from app.local_dashboard.db import arcadedb_server as _arcadedb_server_mod

    monkeypatch.setattr(_arcadedb_server_mod, "wait_for_server_ready", lambda timeout_s=30.0: None)
    created: list[str] = []
    monkeypatch.setattr(
        _arcadedb_server_mod,
        "create_database",
        lambda database_id: created.append(database_id),
    )
    monkeypatch.setattr(
        _routes_databases_mod,
        "bootstrap_database",
        lambda host, port, database_id: {
            "embedding_model": "harrier-270m",
            "embedding_dimension": 640,
        },
    )

    payload = local_dashboard_main.CreateDatabaseRequest(
        database_id="personal-db",
        name="Personal",
    )
    body = local_dashboard_main.create_database(payload, _=None)

    assert body["database"]["database_id"] == "personal-db"
    assert body["database_created"] is True
    assert created == ["personal-db"]

    persisted = json.loads(registry_file.read_text(encoding="utf-8"))
    assert len(persisted["databases"]) == 2
    assert persisted["databases"][1]["database_id"] == "personal-db"
    assert persisted["databases"][1]["embedding_model"] == "harrier-270m"


def test_create_database_rejects_duplicate_database_id(tmp_path, monkeypatch) -> None:
    registry_file = tmp_path / "databases.json"
    _write_registry(registry_file)
    _patch_config(monkeypatch, "LOCAL_DASHBOARD_REGISTRY_FILE", registry_file)

    payload = local_dashboard_main.CreateDatabaseRequest(
        database_id="default",
        name="Default",
    )
    with pytest.raises(HTTPException) as excinfo:
        local_dashboard_main.create_database(payload, _=None)

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"]["code"] == "DATABASE_ALREADY_EXISTS"


def test_create_database_rolls_back_on_registry_write_failure(tmp_path, monkeypatch) -> None:
    registry_file = tmp_path / "databases.json"
    _write_registry(registry_file)
    _patch_config(monkeypatch, "LOCAL_DASHBOARD_REGISTRY_FILE", registry_file)

    from app.local_dashboard.db import arcadedb_server as _arcadedb_server_mod

    monkeypatch.setattr(_arcadedb_server_mod, "wait_for_server_ready", lambda timeout_s=30.0: None)
    monkeypatch.setattr(_arcadedb_server_mod, "create_database", lambda _db_id: None)
    dropped: list[str] = []
    monkeypatch.setattr(
        _arcadedb_server_mod,
        "drop_database",
        lambda db_id: dropped.append(db_id),
    )
    monkeypatch.setattr(
        _routes_databases_mod,
        "bootstrap_database",
        lambda host, port, database_id: {
            "embedding_model": "harrier-270m",
            "embedding_dimension": 640,
        },
    )
    monkeypatch.setattr(
        _routes_databases_mod,
        "_save_registry",
        lambda _registry: (_ for _ in ()).throw(RuntimeError("disk full")),
    )

    payload = local_dashboard_main.CreateDatabaseRequest(
        database_id="archive-db",
        name="Archive",
    )
    with pytest.raises(HTTPException) as excinfo:
        local_dashboard_main.create_database(payload, _=None)

    assert excinfo.value.status_code == 500
    assert excinfo.value.detail["error"]["code"] == "REGISTRY_WRITE_FAILED"
    assert dropped == ["archive-db"]


def test_save_registry_falls_back_to_in_place_write_on_busy_mount(tmp_path, monkeypatch) -> None:
    registry_file = tmp_path / "databases.json"
    _write_registry(registry_file)
    _patch_config(monkeypatch, "LOCAL_DASHBOARD_REGISTRY_FILE", registry_file)

    def _busy_replace(_src, _dst):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(_registry_mod.os, "replace", _busy_replace)

    local_dashboard_main._save_registry(
        {
            "version": 1,
            "databases": [
                {"database_id": "default", "name": "default"},
                {"database_id": "team-db", "name": "Team DB"},
            ],
        }
    )

    persisted = json.loads(registry_file.read_text(encoding="utf-8"))
    database_ids = [record["database_id"] for record in persisted["databases"]]
    assert database_ids == ["default", "team-db"]
    assert list(tmp_path.glob("databases.json.tmp-*")) == []


def test_wizard_bifrost_status_unavailable(monkeypatch) -> None:
    def _raise_models():
        raise HTTPException(
            status_code=503,
            detail={"error": {"message": "Bifrost offline"}},
        )

    monkeypatch.setattr(_routes_wizard_mod, "_bifrost_models", _raise_models)
    body = local_dashboard_main.wizard_bifrost_status(_=None)
    assert body["available"] is False
    assert "offline" in body["error"].lower()


def test_wizard_chat_returns_assistant_message(monkeypatch) -> None:
    monkeypatch.setattr(
        _routes_wizard_mod,
        "_bifrost_chat_completion_with_tools",
        lambda _messages, **_kwargs: (
            "What kind of entities will you store?",
            "openai/test",
            [{"tool": "list_databases", "arguments": {}, "ok": True}],
            None,
        ),
    )
    payload = local_dashboard_main.WizardChatRequest(
        messages=[local_dashboard_main.WizardMessage(role="user", content="I store support tickets.")]
    )
    body = local_dashboard_main.wizard_chat(payload, _=None)
    assert "entities" in body["assistant_message"].lower()
    assert body["model"] == "openai/test"
    assert isinstance(body["tool_events"], list)


def test_wizard_bifrost_status_probe_failure_returns_setup_steps(monkeypatch) -> None:
    monkeypatch.setattr(_routes_wizard_mod, "_bifrost_models", lambda: ["openai/test"])

    def _raise_probe(_models=None):
        raise HTTPException(
            status_code=503,
            detail={"error": {"message": "provider key missing"}},
        )

    monkeypatch.setattr(_routes_wizard_mod, "_bifrost_probe", _raise_probe)
    body = local_dashboard_main.wizard_bifrost_status(_=None)
    assert body["available"] is True
    assert body["ready"] is False
    assert body["probe_success"] is False
    assert "provider key missing" in body["probe_error"]
    assert isinstance(body["setup_steps"], list)
    assert body["sample_config"]


def test_wizard_recommendation_normalizes_database_id(monkeypatch) -> None:
    monkeypatch.setattr(
        _routes_wizard_mod,
        "_bifrost_chat_completion",
        lambda _messages, **_kwargs: (
            '{"database_id":"Team Sales Notes","name":"Team Sales Notes","sslmode":"disable","ready_to_create":true,"reasoning":"fit for sales memory"}',
            "openai/test",
        ),
    )
    payload = local_dashboard_main.WizardChatRequest(
        messages=[local_dashboard_main.WizardMessage(role="user", content="Sales conversations and follow-ups.")]
    )
    body = local_dashboard_main.wizard_recommendation(payload, _=None)
    assert body["database_id"] == "team-sales-notes"
    assert body["name"] == "Team Sales Notes"
    assert body["ready_to_create"] is True
