"""HTTP wrapper around the Bifrost management API.

Replaces the old `_write_bifrost_config` + container-restart dance with direct
management-API calls (`/providers`, `/providers/{p}/keys`).

Open risk: Bifrost's management-API writes may or may not
persist across container restarts. If they don't, `sync_providers` needs to
also write the snapshot file. Callers should verify this against the pinned
image when integrating.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..core.config import (
    LOCAL_DASHBOARD_BIFROST_TIMEOUT_SECONDS,
    LOCAL_DASHBOARD_BIFROST_URL,
)

_LOG = logging.getLogger(__name__)


class BifrostClientError(RuntimeError):
    """Raised when the management API returns an unexpected status or payload."""

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def _url(path: str) -> str:
    base = LOCAL_DASHBOARD_BIFROST_URL.rstrip("/")
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{base}{normalized}"


def _request(
    path: str,
    *,
    method: str = "GET",
    payload: Optional[Any] = None,
    timeout: Optional[float] = None,
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        _url(path),
        data=body,
        headers=headers,
        method=method.upper(),
    )
    effective_timeout = max(1.0, timeout or LOCAL_DASHBOARD_BIFROST_TIMEOUT_SECONDS)
    try:
        with urllib.request.urlopen(request, timeout=effective_timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore").strip()
        except Exception:
            detail = ""
        raise BifrostClientError(
            f"Bifrost {method} {path} returned HTTP {exc.code}: {detail[:200]}",
            status=exc.code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BifrostClientError(
            f"Bifrost {method} {path} unreachable: {exc}"
        ) from exc

    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BifrostClientError(
            f"Bifrost {method} {path} returned invalid JSON: {raw[:200]!r}"
        ) from exc


def list_providers() -> Dict[str, Any]:
    """Return the current provider map keyed by provider name."""
    body = _request("/api/providers", method="GET")
    if body is None:
        return {}
    if isinstance(body, dict):
        # Bifrost returns `{"providers": [...]}` (list) or `{"providers": {...}}`
        # (map) depending on build — accept either, plus a bare map.
        inner = body.get("providers") if "providers" in body else body
        if isinstance(inner, dict):
            return dict(inner)
        if isinstance(inner, list):
            result: Dict[str, Any] = {}
            for item in inner:
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("provider") or "").strip()
                    if name:
                        result[name] = item
            return result
    if isinstance(body, list):
        result = {}
        for item in body:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("provider") or "").strip()
                if name:
                    result[name] = item
        return result
    raise BifrostClientError(
        f"Bifrost /api/providers returned unexpected shape: {type(body).__name__}"
    )


# Reverse of `_provider_prefix` in ai.providers — maps a Bifrost-side provider
# name back to the dashboard-side selector name.
_DASHBOARD_PROVIDER_BY_BIFROST_NAME = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google",
    "groq": "groq",
    "ollama": "local",
}


def dashboard_provider_for(bifrost_name: str) -> Optional[str]:
    return _DASHBOARD_PROVIDER_BY_BIFROST_NAME.get(str(bifrost_name or "").strip().lower())


def _first_key_value(config: Dict[str, Any]) -> str:
    keys = config.get("keys") if isinstance(config, dict) else None
    if not isinstance(keys, list):
        return ""
    for entry in keys:
        if isinstance(entry, dict):
            value = str(entry.get("value") or "").strip()
            if value:
                return value
    return ""


def _models_from_keys(config: Dict[str, Any]) -> List[str]:
    keys = config.get("keys") if isinstance(config, dict) else None
    if not isinstance(keys, list):
        return []
    seen: set[str] = set()
    out: List[str] = []
    for entry in keys:
        if not isinstance(entry, dict):
            continue
        models = entry.get("models")
        if not isinstance(models, list):
            continue
        for model in models:
            token = str(model or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
    return out


def get_provider_summaries() -> List[Dict[str, Any]]:
    """Return a UI-safe summary of configured Bifrost providers.

    No raw API keys are returned — only `has_key`, plus the per-provider model
    list and (for local) base_url. Dashboard-side provider names are used
    (e.g. ``google`` instead of ``gemini``).
    """
    from ..ai.providers import _normalize_provider_model

    summaries: List[Dict[str, Any]] = []
    for bifrost_name, config in list_providers().items():
        dashboard_name = dashboard_provider_for(bifrost_name)
        if not dashboard_name:
            continue
        if not isinstance(config, dict):
            config = {}
        first_value = _first_key_value(config)
        is_local = dashboard_name == "local"
        # Local "credential" is a literal "local" placeholder string; the real
        # endpoint lives in network_config.base_url.
        has_key = bool(first_value) and (not is_local or first_value != "local")
        raw_models = _models_from_keys(config)
        normalized_models = []
        seen: set[str] = set()
        for token in raw_models:
            normalized = _normalize_provider_model(dashboard_name, token)
            if normalized and normalized not in seen:
                seen.add(normalized)
                normalized_models.append(normalized)
        network = config.get("network_config") if isinstance(config.get("network_config"), dict) else {}
        base_url = str(network.get("base_url") or "").strip()
        summaries.append(
            {
                "provider": dashboard_name,
                "has_key": has_key if not is_local else bool(base_url),
                "models": normalized_models,
                "base_url": base_url or None,
            }
        )
    return summaries


def get_saved_credential(dashboard_provider: str) -> Optional[str]:
    """Return the saved API key (or base_url for local) for a dashboard
    provider name, or ``None`` if no usable credential is configured.
    """
    from ..ai.providers import _provider_prefix

    name = str(dashboard_provider or "").strip().lower()
    if not name:
        return None
    bifrost_name = _provider_prefix(name)
    config = list_providers().get(bifrost_name)
    if not isinstance(config, dict):
        return None
    if name == "local":
        network = config.get("network_config") if isinstance(config.get("network_config"), dict) else {}
        base_url = str(network.get("base_url") or "").strip().rstrip("/")
        return base_url or None
    value = _first_key_value(config)
    return value or None


def upsert_provider(provider: str, config: Dict[str, Any]) -> None:
    """Create or update a provider entry."""
    name = (provider or "").strip()
    if not name:
        raise ValueError("provider name is required")
    _request(f"/api/providers/{name}", method="PUT", payload=dict(config or {}))


def delete_provider(provider: str) -> None:
    name = (provider or "").strip()
    if not name:
        raise ValueError("provider name is required")
    try:
        _request(f"/api/providers/{name}", method="DELETE")
    except BifrostClientError as exc:
        if exc.status == 404:
            return
        raise


def build_provider_configs(
    providers: List[Any],
) -> tuple[Dict[str, Dict[str, Any]], int]:
    """Group a list of `BifrostProvider` entries into per-provider config
    payloads suitable for `upsert_provider` / `sync_providers`.

    Returns `(configs_by_name, unique_model_count)`. `configs_by_name` is keyed
    by the Bifrost provider key (e.g. "openai", "anthropic"). Each value is the
    same shape `_write_bifrost_config` used to produce: `{"keys": [...],
    "network_config": {...}}`.
    """
    # Imported lazily to avoid a circular import (providers depend on config).
    from ..ai.providers import _dedupe_preserve_order, _normalize_provider_model, _provider_prefix

    grouped: Dict[str, Dict[str, List[str]]] = {}
    base_urls: Dict[str, str] = {}
    unique_models: set[tuple[str, str]] = set()

    for entry in providers or []:
        provider = str(getattr(entry, "provider", "") or "").strip().lower()
        if not provider:
            continue
        api_key = str(getattr(entry, "api_key", "") or "").strip() or "local"
        normalized_model = _normalize_provider_model(provider, getattr(entry, "model", ""))
        if not normalized_model:
            continue
        bifrost_provider = _provider_prefix(provider)
        base_url = str(getattr(entry, "base_url", "") or "").strip().rstrip("/")
        if base_url:
            base_urls[bifrost_provider] = base_url
        model_token = normalized_model.split("/", 1)[1] if "/" in normalized_model else normalized_model
        bucket = grouped.setdefault(bifrost_provider, {})
        existing = bucket.setdefault(api_key, [])
        bucket[api_key] = _dedupe_preserve_order([*existing, model_token])
        unique_models.add((provider, normalized_model))

    configs: Dict[str, Dict[str, Any]] = {}
    for provider_name, keyed_models in grouped.items():
        keys: List[Dict[str, Any]] = []
        for api_key, models in keyed_models.items():
            keys.append(
                {
                    "name": f"{provider_name}-key-{len(keys) + 1}",
                    "value": api_key,
                    "models": models,
                    "weight": 1.0,
                }
            )
        config: Dict[str, Any] = {
            "keys": keys,
            "concurrency_and_buffer_size": {"concurrency": 1, "buffer_size": 10},
        }
        if provider_name in base_urls:
            config["network_config"] = {"base_url": base_urls[provider_name]}
        configs[provider_name] = config
    return configs, len(unique_models)


def sync_providers(desired: Dict[str, Any]) -> Dict[str, Any]:
    """Reconcile the desired provider map against Bifrost's current state.

    Returns a summary of the work done: `{"added": [...], "updated": [...],
    "deleted": [...]}`. No container restart.
    """
    desired = {str(k).strip(): dict(v or {}) for k, v in (desired or {}).items() if str(k).strip()}
    current = list_providers()
    added: List[str] = []
    updated: List[str] = []
    deleted: List[str] = []

    for name, config in desired.items():
        if name not in current:
            upsert_provider(name, config)
            added.append(name)
        elif current[name] != config:
            upsert_provider(name, config)
            updated.append(name)

    for name in list(current.keys()):
        if name not in desired:
            delete_provider(name)
            deleted.append(name)

    return {"added": added, "updated": updated, "deleted": deleted}
