import pytest
from pydantic import ValidationError

from app.api_keys.schemas import CreateApiKeyRequest


def test_create_api_key_request_rejects_database_sync_with_target_id() -> None:
    with pytest.raises(ValidationError):
        CreateApiKeyRequest(
            name="test",
            expires_days=30,
            database_target_id="dt_123",
            database_sync={"database_id": "work-db"},
        )
