import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.onboarding.router import get_auth0_config, _normalize_public_api_host  # noqa: E402


def test_auth0_config_supports_csv_audiences(monkeypatch) -> None:
    monkeypatch.setenv("AUTH0_DOMAIN", "example.auth0.com")
    monkeypatch.setenv("AUTH0_AUDIENCE", "aud-one, aud-two, aud-one ")

    config = get_auth0_config()

    assert config["audiences"] == ["aud-one", "aud-two"]


def test_auth0_config_merges_compat_audiences(monkeypatch) -> None:
    monkeypatch.setenv("AUTH0_DOMAIN", "example.auth0.com")
    monkeypatch.setenv("AUTH0_AUDIENCE", "https://api.loreholm.com")
    monkeypatch.setenv(
        "AUTH0_AUDIENCE_COMPAT",
        "https://example.auth0.com/api/v2/, https://api.loreholm.com",
    )

    config = get_auth0_config()

    assert config["audiences"] == [
        "https://api.loreholm.com",
        "https://example.auth0.com/api/v2/",
    ]


def test_auth0_config_no_implicit_compat_audiences(monkeypatch) -> None:
    # Tenant-specific compat defaults were removed from code: without
    # AUTH0_AUDIENCE_COMPAT set, only AUTH0_AUDIENCE values are accepted,
    # regardless of which tenant AUTH0_DOMAIN names.
    monkeypatch.setenv("AUTH0_DOMAIN", "example.auth0.com")
    monkeypatch.setenv("AUTH0_AUDIENCE", "https://api.loreholm.com")
    monkeypatch.delenv("AUTH0_AUDIENCE_COMPAT", raising=False)

    config = get_auth0_config()

    assert config["audiences"] == ["https://api.loreholm.com"]


def test_auth0_config_frontend_audience_defaults_to_primary(monkeypatch) -> None:
    monkeypatch.setenv("AUTH0_DOMAIN", "example.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "client-123")
    monkeypatch.setenv("AUTH0_AUDIENCE", "aud-primary,aud-secondary")
    monkeypatch.delenv("AUTH0_FRONTEND_AUDIENCE", raising=False)

    config = get_auth0_config()

    assert config["frontend_audience"] == "aud-primary"
    assert config["frontend_audience_explicit"] is False


def test_auth0_config_frontend_audience_marks_explicit(monkeypatch) -> None:
    monkeypatch.setenv("AUTH0_DOMAIN", "example.auth0.com")
    monkeypatch.setenv("AUTH0_AUDIENCE", "aud-primary,aud-secondary")
    monkeypatch.setenv("AUTH0_FRONTEND_AUDIENCE", "aud-frontend")

    config = get_auth0_config()

    assert config["frontend_audience"] == "aud-frontend"
    assert config["frontend_audience_explicit"] is True


def test_normalize_public_api_host_prefers_https() -> None:
    assert _normalize_public_api_host("loreholm.com", prefer_https=True) == "https://loreholm.com"
    assert _normalize_public_api_host("http://loreholm.com", prefer_https=True) == "https://loreholm.com"
    assert _normalize_public_api_host("https://loreholm.com/", prefer_https=True) == "https://loreholm.com"


def test_normalize_public_api_host_allows_http_when_not_preferred() -> None:
    assert _normalize_public_api_host("loreholm.local", prefer_https=False) == "http://loreholm.local"
    assert _normalize_public_api_host("http://loreholm.local/", prefer_https=False) == "http://loreholm.local"
