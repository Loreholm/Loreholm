import pytest
from fastapi import HTTPException

from app.llm.router import _extract_loreholm_key_id, _require_provider_key


def test_extract_loreholm_key_id_prefers_header() -> None:
    payload = {"loreholm_key_id": "payload-key"}
    selected = _extract_loreholm_key_id(payload, "header-key")
    assert selected == "header-key"
    assert "loreholm_key_id" not in payload


def test_extract_loreholm_key_id_allows_local_without_redis(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_HOST", raising=False)
    payload = {}
    selected = _extract_loreholm_key_id(payload, None)
    assert selected == "byodb-local-key"


def test_extract_loreholm_key_id_requires_key_with_redis(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_HOST", "localhost")
    with pytest.raises(HTTPException) as exc_info:
        _extract_loreholm_key_id({}, None)
    assert exc_info.value.status_code == 400


def test_require_provider_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("OPENAI_API_KEY_FILE", raising=False)
    assert _require_provider_key("OpenAI", "OPENAI_API_KEY") == "test-openai-key"


def test_require_provider_key_from_file(monkeypatch, tmp_path) -> None:
    key_path = tmp_path / "openai_key"
    key_path.write_text("file-openai-key\n", encoding="utf-8")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY_FILE", str(key_path))

    assert _require_provider_key("OpenAI", "OPENAI_API_KEY") == "file-openai-key"


def test_require_provider_key_raises_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_FILE", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        _require_provider_key("OpenAI", "OPENAI_API_KEY")

    assert exc_info.value.status_code == 503
