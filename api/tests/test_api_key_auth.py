import base64
import asyncio

from app.services import api_key_auth


class _StubApiKeyStore:
    async def is_key_revoked(self, _key_id: str) -> bool:
        return False


def test_create_and_validate_api_key_with_database_ref_claim(monkeypatch) -> None:
    signing_secret = base64.b64encode(b"b" * 32).decode("ascii")
    monkeypatch.setenv("API_KEY_SIGNING_SECRET", signing_secret)

    async def _fake_get_store():
        return _StubApiKeyStore()

    monkeypatch.setattr(api_key_auth, "get_api_key_store", _fake_get_store)

    created = api_key_auth.create_api_key(
        user_id="user-456",
        email="user@example.com",
        name="Target Key",
        expires_days=30,
        database_target_id="dt_abc123",
    )

    payload = asyncio.run(api_key_auth.validate_api_key(created["api_key"]))

    assert payload["sub"] == "user-456"
    assert payload["kid"] == created["key_id"]
    assert payload["db_ref"] == "dt_abc123"
    assert "db" not in payload
