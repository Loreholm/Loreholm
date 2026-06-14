import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.onboarding.router import get_oidc_config, _normalize_public_api_host  # noqa: E402
from app.services import user_id_to_namespace  # noqa: E402


def _clear_oidc_env(monkeypatch) -> None:
    for name in (
        "OIDC_ISSUER",
        "OIDC_CLIENT_ID",
        "OIDC_AUDIENCE",
        "OIDC_FRONTEND_AUDIENCE",
        "OIDC_AUDIENCE_CLAIM",
    ):
        monkeypatch.delenv(name, raising=False)


def test_oidc_config_requires_only_three_values(monkeypatch) -> None:
    # Issuer, client id and audience are the whole required surface; the
    # rest is discovered from the issuer at verification time.
    _clear_oidc_env(monkeypatch)
    monkeypatch.setenv("OIDC_ISSUER", "https://example.auth0.com")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client-123")
    monkeypatch.setenv("OIDC_AUDIENCE", "https://api.loreholm.com")

    config = get_oidc_config()

    assert config["issuer"] == "https://example.auth0.com"
    assert config["client_id"] == "client-123"
    assert config["audiences"] == ["https://api.loreholm.com"]
    assert config["audience_claim"] == "aud"


def test_oidc_config_frontend_audience_defaults_to_api_audience(monkeypatch) -> None:
    _clear_oidc_env(monkeypatch)
    monkeypatch.setenv("OIDC_ISSUER", "https://example.auth0.com")
    monkeypatch.setenv("OIDC_AUDIENCE", "https://api.loreholm.com")

    config = get_oidc_config()

    assert config["frontend_audience"] == "https://api.loreholm.com"
    assert config["frontend_audience_explicit"] is False


def test_oidc_config_frontend_audience_override(monkeypatch) -> None:
    _clear_oidc_env(monkeypatch)
    monkeypatch.setenv("OIDC_ISSUER", "https://example.auth0.com")
    monkeypatch.setenv("OIDC_AUDIENCE", "https://api.loreholm.com")
    monkeypatch.setenv("OIDC_FRONTEND_AUDIENCE", "aud-frontend")

    config = get_oidc_config()

    assert config["frontend_audience"] == "aud-frontend"
    assert config["frontend_audience_explicit"] is True


def test_oidc_config_normalizes_issuer(monkeypatch) -> None:
    # Scheme is added and the trailing slash dropped so the discovery URL is
    # well-formed regardless of how the operator writes the issuer.
    _clear_oidc_env(monkeypatch)
    monkeypatch.setenv("OIDC_ISSUER", "example.auth0.com/")
    monkeypatch.setenv("OIDC_AUDIENCE", "aud-one")

    config = get_oidc_config()

    assert config["issuer"] == "https://example.auth0.com"


def test_oidc_config_audience_claim_override(monkeypatch) -> None:
    _clear_oidc_env(monkeypatch)
    monkeypatch.setenv("OIDC_ISSUER", "https://example.auth0.com")
    monkeypatch.setenv("OIDC_AUDIENCE", "aud-one")
    monkeypatch.setenv("OIDC_AUDIENCE_CLAIM", "azp")

    config = get_oidc_config()

    assert config["audience_claim"] == "azp"


def test_user_id_to_namespace_sanitizes_arbitrary_subs() -> None:
    # Provider subs with `|`, `:`, `/` or uppercase must become a valid
    # Headscale namespace ([a-z0-9-]) and stay deterministic.
    ns = user_id_to_namespace("google-oauth2|123456789")
    assert re.fullmatch(r"user-[a-z0-9-]+", ns)
    assert ns == user_id_to_namespace("google-oauth2|123456789")

    # Distinct subs that slugify identically must not collide.
    assert user_id_to_namespace("ABC|1") != user_id_to_namespace("abc:1")


def test_normalize_public_api_host_prefers_https() -> None:
    assert _normalize_public_api_host("loreholm.com", prefer_https=True) == "https://loreholm.com"
    assert _normalize_public_api_host("http://loreholm.com", prefer_https=True) == "https://loreholm.com"
    assert _normalize_public_api_host("https://loreholm.com/", prefer_https=True) == "https://loreholm.com"


def test_normalize_public_api_host_allows_http_when_not_preferred() -> None:
    assert _normalize_public_api_host("loreholm.local", prefer_https=False) == "http://loreholm.local"
    assert _normalize_public_api_host("http://loreholm.local/", prefer_https=False) == "http://loreholm.local"
