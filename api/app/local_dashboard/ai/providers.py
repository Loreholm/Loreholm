from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from ..core.config import LOCAL_DASHBOARD_PROVIDER_DISCOVERY_TIMEOUT_SECONDS
from ..core.models import BifrostProvider


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _provider_prefix(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    mapping = {
        "openai": "openai",
        "anthropic": "anthropic",
        "google": "gemini",
        "groq": "groq",
        "local": "ollama",
        "ollama": "ollama",
    }
    prefix = mapping.get(normalized)
    if not prefix:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "BIFROST_UNSUPPORTED_PROVIDER",
                    "message": f"Unsupported provider '{provider}'.",
                }
            },
        )
    return prefix


def _normalize_provider_model(provider: str, model_id: str) -> str:
    raw = str(model_id or "").strip()
    if not raw:
        return ""
    prefix = _provider_prefix(provider)
    lower_raw = raw.lower()
    if lower_raw.startswith(f"{prefix}/"):
        return raw
    if lower_raw.startswith("models/"):
        raw = raw.split("/", 1)[1]
    return f"{prefix}/{raw}"


def _fetch_json(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    timeout: Optional[float] = None,
    provider: str,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", **(headers or {})},
    )
    request_timeout = timeout if timeout is not None else LOCAL_DASHBOARD_PROVIDER_DISCOVERY_TIMEOUT_SECONDS
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, request_timeout)) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        if status in {401, 403}:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "BIFROST_PROVIDER_AUTH_FAILED",
                        "message": f"{provider} API key was rejected while listing models.",
                    }
                },
            ) from exc
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "BIFROST_PROVIDER_DISCOVERY_FAILED",
                    "message": f"Failed to list {provider} models (HTTP {status or 'error'}).",
                }
            },
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "BIFROST_PROVIDER_DISCOVERY_UNAVAILABLE",
                    "message": f"Could not reach {provider} model listing endpoint.",
                }
            },
        ) from exc

    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "BIFROST_PROVIDER_DISCOVERY_INVALID_RESPONSE",
                    "message": f"{provider} model listing returned invalid JSON.",
                }
            },
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "BIFROST_PROVIDER_DISCOVERY_INVALID_RESPONSE",
                    "message": f"{provider} model listing returned an invalid response object.",
                }
            },
        )
    return payload


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_iso_datetime(value: Any) -> Optional[str]:
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    parsed = _parse_iso_datetime(value)
    return parsed.isoformat() if parsed else None


def _age_label(created_at_iso: Optional[str]) -> str:
    parsed = _parse_iso_datetime(created_at_iso or "")
    if not parsed:
        return "Age unknown"
    days = max(0, (datetime.now(timezone.utc) - parsed).days)
    if days <= 30:
        return "New"
    if days <= 180:
        return "Recent"
    return "Older"


def _infer_model_type(
    provider: str,
    model_id: str,
    supported_methods: Optional[list[str]] = None,
) -> str:
    provider_normalized = str(provider or "").strip().lower()
    token = str(model_id or "").strip().lower()
    methods = {
        str(item).strip().lower()
        for item in (supported_methods or [])
        if str(item).strip()
    }

    if "embedcontent" in methods or "embeddings" in token or "embedding" in token:
        return "embedding"
    if "generateimages" in methods or "imagegeneration" in methods or "dall-e" in token:
        return "image"
    if "generatevideo" in methods or "sora" in token:
        return "video"
    if "generateaudio" in methods or "audio" in token or "whisper" in token or "tts" in token:
        return "audio"
    if "realtime" in token:
        return "audio"
    if "transcribe" in token or "diarize" in token:
        return "audio"
    if "moderation" in token:
        return "other"
    if "search-preview" in token or "search-api" in token or "deep-research" in token:
        return "other"
    if "reason" in token or "thinking" in token or re.search(r"(^|[-_/])o[134]($|[-_/])", token):
        return "reasoning"
    if "vision" in token or "vl" in token:
        return "vision"
    # Legacy completions-only models that are NOT chat models
    if re.search(r"(^|[-_/])(babbage|davinci|ada|curie)(-\d+)?$", token):
        return "completion"
    if "instruct" in token and "gpt" in token:
        return "completion"
    if "gpt-3.5-turbo-16k" in token:
        return "completion"
    if provider_normalized in {"openai", "anthropic", "google"}:
        return "chat"
    return "other"


# Model types that accept text input and produce text output — i.e. usable as
# the LLM behind any text-based agent (chat, wizard, prompt drafter, etc.).
LLM_MODEL_TYPES: frozenset[str] = frozenset({"chat", "reasoning"})


def _classify_model_id(model_id: str) -> str:
    """Infer the model type from a (possibly provider-prefixed) model id."""
    token = str(model_id or "").strip()
    if not token:
        return "other"
    provider = ""
    if "/" in token:
        provider_prefix = token.split("/", 1)[0].strip().lower()
        if provider_prefix in {"openai", "anthropic"}:
            provider = provider_prefix
        elif provider_prefix in {"gemini", "google"}:
            provider = "google"
    return _infer_model_type(provider, token)


def _filter_llm_model_ids(models: list[str]) -> list[str]:
    """Keep only model ids classified as text-in/text-out LLMs."""
    seen: set[str] = set()
    result: list[str] = []
    for model in models:
        model_id = str(model or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        if _classify_model_id(model_id) in LLM_MODEL_TYPES:
            result.append(model_id)
    return result


def _build_model_descriptor(
    provider: str,
    model_id: Any,
    *,
    created_at: Any = None,
    supported_methods: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    normalized_id = _normalize_provider_model(provider, str(model_id or ""))
    if not normalized_id:
        return None
    created_at_iso = _to_iso_datetime(created_at)
    descriptor = {
        "id": normalized_id,
        "type": _infer_model_type(provider, normalized_id, supported_methods=supported_methods),
        "created_at": created_at_iso,
        "age_label": _age_label(created_at_iso),
    }
    return descriptor


def _dedupe_model_descriptors(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in entries:
        model_id = str(entry.get("id", "")).strip()
        if not model_id:
            continue
        existing = deduped.get(model_id)
        if existing is None:
            deduped[model_id] = entry
            order.append(model_id)
            continue
        if not existing.get("created_at") and entry.get("created_at"):
            existing["created_at"] = entry.get("created_at")
            existing["age_label"] = entry.get("age_label")
        if existing.get("type") in {"", "other"} and entry.get("type"):
            existing["type"] = entry.get("type")
    return [deduped[model_id] for model_id in order]


def _model_descriptor_sort_key(entry: dict[str, Any]) -> tuple[int, float, str]:
    type_order = {
        "reasoning": 0,
        "chat": 1,
        "vision": 2,
        "image": 3,
        "audio": 4,
        "embedding": 5,
        "other": 6,
    }
    type_name = str(entry.get("type", "other")).strip().lower() or "other"
    type_rank = type_order.get(type_name, type_order["other"])
    created = _parse_iso_datetime(entry.get("created_at"))
    timestamp = created.timestamp() if created else 0.0
    model_id = str(entry.get("id", "")).strip().lower()
    return (type_rank, -timestamp, model_id)


def _sorted_model_descriptors(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=_model_descriptor_sort_key)


def _discover_openai_model_descriptors(api_key: str) -> list[dict[str, Any]]:
    payload = _fetch_json(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        provider="OpenAI",
    )
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    descriptors: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        descriptor = _build_model_descriptor(
            "openai",
            item.get("id"),
            created_at=item.get("created"),
        )
        if descriptor:
            descriptors.append(descriptor)
    return _sorted_model_descriptors(_dedupe_model_descriptors(descriptors))


def _discover_anthropic_model_descriptors(api_key: str) -> list[dict[str, Any]]:
    payload = _fetch_json(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        provider="Anthropic",
    )
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    descriptors: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        descriptor = _build_model_descriptor(
            "anthropic",
            item.get("id"),
            created_at=item.get("created_at"),
        )
        if descriptor:
            descriptors.append(descriptor)
    return _sorted_model_descriptors(_dedupe_model_descriptors(descriptors))


def _discover_google_model_descriptors(api_key: str) -> list[dict[str, Any]]:
    base_url = "https://generativelanguage.googleapis.com/v1beta/models"
    page_token = ""
    descriptors: list[dict[str, Any]] = []
    for _ in range(5):
        query = {"pageSize": "1000"}
        if page_token:
            query["pageToken"] = page_token
        url = f"{base_url}?{urllib.parse.urlencode(query)}"
        payload = _fetch_json(
            url,
            headers={"x-goog-api-key": api_key},
            provider="Google",
        )
        rows = payload.get("models")
        if isinstance(rows, list):
            for item in rows:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name.startswith("models/"):
                    continue
                short_name = name.split("/", 1)[1]
                methods = item.get("supportedGenerationMethods")
                normalized_methods = [
                    str(method).strip()
                    for method in (methods if isinstance(methods, list) else [])
                    if str(method).strip()
                ]
                descriptor = _build_model_descriptor(
                    "google",
                    short_name,
                    supported_methods=normalized_methods,
                )
                if descriptor:
                    descriptors.append(descriptor)
        page_token = str(payload.get("nextPageToken", "")).strip()
        if not page_token:
            break
    return _sorted_model_descriptors(_dedupe_model_descriptors(descriptors))


def _discover_groq_model_descriptors(api_key: str) -> list[dict[str, Any]]:
    payload = _fetch_json(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        provider="Groq",
    )
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    descriptors: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        descriptor = _build_model_descriptor(
            "groq",
            item.get("id"),
            created_at=item.get("created"),
        )
        if descriptor:
            descriptors.append(descriptor)
    return _sorted_model_descriptors(_dedupe_model_descriptors(descriptors))


def _discover_ollama_model_descriptors(base_url: str) -> list[dict[str, Any]]:
    url = base_url.rstrip("/") + "/v1/models"
    payload = _fetch_json(url, provider="Ollama")
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    descriptors: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        descriptor = _build_model_descriptor(
            "local",
            item.get("id") or item.get("name"),
        )
        if descriptor:
            descriptors.append(descriptor)
    return _sorted_model_descriptors(_dedupe_model_descriptors(descriptors))


def _discover_provider_model_descriptors(provider: str, api_key: str) -> list[dict[str, Any]]:
    normalized = str(provider or "").strip().lower()
    if normalized == "openai":
        return _discover_openai_model_descriptors(api_key)
    if normalized == "anthropic":
        return _discover_anthropic_model_descriptors(api_key)
    if normalized == "google":
        return _discover_google_model_descriptors(api_key)
    if normalized == "groq":
        return _discover_groq_model_descriptors(api_key)
    if normalized in {"local", "ollama"}:
        return _discover_ollama_model_descriptors(api_key)
    _provider_prefix(normalized)
    return []


def _discover_provider_models(provider: str, api_key: Optional[str], base_url: Optional[str] = None) -> list[str]:
    is_local = str(provider or "").strip().lower() in {"local", "ollama"}
    credential = str(base_url or api_key or "").strip() if is_local else str(api_key or "").strip()
    descriptors = _discover_provider_model_descriptors(provider, credential)
    return [str(entry.get("id", "")).strip() for entry in descriptors if str(entry.get("id", "")).strip()]


def _expanded_bifrost_providers(providers: list[BifrostProvider]) -> tuple[list[BifrostProvider], dict[str, int]]:
    expanded: list[BifrostProvider] = []
    counts: dict[str, int] = {}
    for provider_entry in providers:
        normalized_provider = provider_entry.provider.strip().lower()
        preferred_model = _normalize_provider_model(normalized_provider, provider_entry.model)
        discovered_models = _discover_provider_models(normalized_provider, provider_entry.api_key, provider_entry.base_url)
        ordered_models = _dedupe_preserve_order([preferred_model, *discovered_models])
        if not ordered_models:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "BIFROST_NO_MODELS_DISCOVERED",
                        "message": (
                            f"No models discovered for provider '{normalized_provider}'. "
                            "Check API key permissions and retry."
                        ),
                    }
                },
            )
        for model in ordered_models:
            expanded.append(
                BifrostProvider(
                    provider=normalized_provider,
                    api_key=provider_entry.api_key,
                    model=model,
                )
            )
        counts[normalized_provider] = len(ordered_models)
    return expanded, counts
