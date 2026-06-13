import asyncio

import pytest

from app.services import local_sync, sync_auth


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response: _Response):
        self._response = response
        self.headers = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, headers: dict, json: dict):
        self.url = url
        self.headers = headers
        self.json_payload = json
        return self._response

    async def get(self, url: str, headers: dict):
        self.url = url
        self.headers = headers
        return self._response


def test_fetch_local_database_sync_payload_success(monkeypatch) -> None:
    # Per-user derivation pulls the secret from the environment and HMACs
    # it with the user sub. The test just needs a signing secret to be set.
    monkeypatch.setenv("LOCAL_SYNC_SIGNING_SECRET", "test-signing-secret")

    async def _fake_ip(_user_id: str):
        return "100.64.0.25"

    captured_client: dict = {}

    def _client_factory(**_kwargs):
        client = _Client(
            _Response(
                200,
                {
                    "database_id": "work-db",
                    "profile": {"profile_id": "memory-work", "profile_version": 3},
                },
            )
        )
        captured_client["client"] = client
        return client

    monkeypatch.setattr(local_sync, "get_user_tailscale_ip", _fake_ip)
    monkeypatch.setattr(local_sync.httpx, "AsyncClient", _client_factory)

    payload = asyncio.run(
        local_sync.fetch_local_database_sync_payload("user-1", "work-db")
    )

    assert payload["database_id"] == "work-db"
    assert payload["profile"]["profile_version"] == 3

    # The bearer token must be the HMAC derivation for user-1, not a
    # fleet-wide static string. Re-derive with the same secret to verify.
    expected_token = sync_auth.derive_user_sync_token("user-1")
    assert captured_client["client"].headers["Authorization"] == f"Bearer {expected_token}"


def test_fetch_local_database_sync_payload_per_user_tokens_differ(monkeypatch) -> None:
    """Two different users must receive different bearer tokens."""
    monkeypatch.setenv("LOCAL_SYNC_SIGNING_SECRET", "test-signing-secret")

    token_a = sync_auth.derive_user_sync_token("user-a")
    token_b = sync_auth.derive_user_sync_token("user-b")
    assert token_a != token_b
    # And they must be stable (idempotent) for the same user.
    assert sync_auth.derive_user_sync_token("user-a") == token_a


def test_fetch_local_database_sync_payload_surfaces_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_SYNC_SIGNING_SECRET", raising=False)
    monkeypatch.setattr(
        sync_auth,
        "LOCAL_SYNC_SIGNING_SECRET_FILE",
        "/nonexistent/does-not-exist",
    )

    async def _fake_ip(_user_id: str):
        return "100.64.0.25"

    monkeypatch.setattr(local_sync, "get_user_tailscale_ip", _fake_ip)

    with pytest.raises(local_sync.LocalSyncError) as excinfo:
        asyncio.run(local_sync.fetch_local_database_sync_payload("user-1", "work-db"))

    assert excinfo.value.code == "LOCAL_SYNC_NOT_CONFIGURED"


def test_fetch_local_database_sync_payload_rejects_unauthorized(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_SYNC_SIGNING_SECRET", "test-signing-secret")

    async def _fake_ip(_user_id: str):
        return "100.64.0.25"

    monkeypatch.setattr(local_sync, "get_user_tailscale_ip", _fake_ip)
    monkeypatch.setattr(
        local_sync.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(_Response(401, {"detail": "Unauthorized"})),
    )

    with pytest.raises(local_sync.LocalSyncError) as excinfo:
        asyncio.run(local_sync.fetch_local_database_sync_payload("user-1", "work-db"))

    assert excinfo.value.code == "LOCAL_SYNC_UNAUTHORIZED"


def test_fetch_local_database_sync_payload_rejects_invalid_response(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_SYNC_SIGNING_SECRET", "test-signing-secret")

    async def _fake_ip(_user_id: str):
        return "100.64.0.25"

    monkeypatch.setattr(local_sync, "get_user_tailscale_ip", _fake_ip)
    monkeypatch.setattr(
        local_sync.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(_Response(200, {"database_id": "work-db"})),
    )

    with pytest.raises(local_sync.LocalSyncError) as excinfo:
        asyncio.run(local_sync.fetch_local_database_sync_payload("user-1", "work-db"))

    assert excinfo.value.code == "LOCAL_SYNC_INVALID_RESPONSE"


def test_fetch_local_database_inventory_returns_items(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_SYNC_SIGNING_SECRET", "test-signing-secret")

    async def _fake_ip(_user_id: str):
        return "100.64.0.25"

    monkeypatch.setattr(local_sync, "get_user_tailscale_ip", _fake_ip)
    monkeypatch.setattr(
        local_sync.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(
            _Response(
                200,
                {
                    "databases": [
                        {
                            "database_id": "work-db",
                            "name": "Work",
                            "profile_id": "memory-work",
                            "profile_hash": "h1",
                            "status": "online",
                            "last_seen_at": "2026-04-11T00:00:00Z",
                        },
                        {
                            "database_id": "scratch",
                            "name": "Scratch",
                            "profile_id": "memory-default",
                            "profile_hash": None,
                            "status": "offline",
                            "last_seen_at": None,
                        },
                    ],
                    "count": 2,
                },
            )
        ),
    )

    items = asyncio.run(local_sync.fetch_local_database_inventory("user-1"))

    assert len(items) == 2
    assert items[0]["database_id"] == "work-db"
    assert items[0]["status"] == "online"
    assert items[1]["status"] == "offline"
