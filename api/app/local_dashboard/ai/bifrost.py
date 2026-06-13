from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Optional

from fastapi import HTTPException

from ..core.config import (
    LOCAL_DASHBOARD_BIFROST_TIMEOUT_SECONDS,
    LOCAL_DASHBOARD_BIFROST_URL,
    LOCAL_DASHBOARD_WIZARD_MODEL,
)
from ..core.models import WizardMessage
from .providers import _dedupe_preserve_order, _filter_llm_model_ids, _infer_model_type


def _bifrost_url(path: str) -> str:
    base = LOCAL_DASHBOARD_BIFROST_URL.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{base}{normalized_path}"


# OpenAI reasoning models reject custom sampling params. Bifrost forwards them
# as-is, so we strip on the client side. Matches `o1`/`o3`/`o4`/`o5+` and the
# entire `gpt-5*` family, with or without a `provider/` prefix.
_REASONING_MODEL_RE = re.compile(r"^(o[1-9]|gpt-5)(?:[._\-]|$)")


def _strip_reasoning_unsupported_params(payload: Optional[dict[str, Any]]) -> None:
    if not payload:
        return
    model = str(payload.get("model") or "")
    bare = model.split("/", 1)[-1].lower()
    if _REASONING_MODEL_RE.match(bare):
        payload.pop("temperature", None)
        payload.pop("top_p", None)


def _bifrost_request(
    path: str,
    *,
    method: str = "GET",
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    _strip_reasoning_unsupported_params(payload)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(
        _bifrost_url(path),
        data=body,
        headers=headers,
        method=method.upper(),
    )
    timeout = max(1.0, LOCAL_DASHBOARD_BIFROST_TIMEOUT_SECONDS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore").strip()
        except Exception:
            detail = ""
        if len(detail) > 300:
            detail = detail[:300] + "..."
        message = f"Bifrost returned HTTP {exc.code}."
        if detail:
            message = f"{message} {detail}"
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "WIZARD_BIFROST_HTTP_ERROR",
                    "message": message,
                }
            },
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "WIZARD_BIFROST_UNAVAILABLE",
                    "message": (
                        "Could not reach local Bifrost proxy. "
                        "Ensure bifrost-proxy is running, then retry."
                    ),
                }
            },
        ) from exc

    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "WIZARD_BIFROST_INVALID_RESPONSE",
                    "message": "Bifrost returned invalid JSON.",
                }
            },
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "WIZARD_BIFROST_INVALID_RESPONSE",
                    "message": "Bifrost returned a non-object response.",
                }
            },
        )
    return parsed


# ---------------------------------------------------------------------------
# Bifrost streaming helpers
# ---------------------------------------------------------------------------

def _bifrost_stream_chunks(path: str, payload: dict[str, Any]):
    """Yield parsed JSON chunks from a Bifrost SSE streaming response.

    Each yielded value is the parsed JSON object from a ``data:`` line.
    The generator terminates when ``data: [DONE]`` is received or the
    connection closes.
    """
    _strip_reasoning_unsupported_params(payload)
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _bifrost_url(path),
        data=body,
        headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
        method="POST",
    )
    timeout = max(1.0, LOCAL_DASHBOARD_BIFROST_TIMEOUT_SECONDS)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore").strip()
        except Exception:
            detail = ""
        if len(detail) > 300:
            detail = detail[:300] + "..."
        message = f"Bifrost returned HTTP {exc.code}."
        if detail:
            message = f"{message} {detail}"
        raise HTTPException(
            status_code=502,
            detail={"error": {"code": "WIZARD_BIFROST_HTTP_ERROR", "message": message}},
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "WIZARD_BIFROST_UNAVAILABLE",
                    "message": "Could not reach local Bifrost proxy. Ensure bifrost-proxy is running, then retry.",
                }
            },
        ) from exc

    try:
        while True:
            raw_line = response.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            if line.startswith("data: "):
                data = line[6:]
                if data.strip() == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
    finally:
        response.close()


def _sse_event(event_type: str, data: Any) -> str:
    """Format a single Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _accumulate_tool_call_delta(
    accumulated: list[dict[str, Any]],
    tc_delta: dict[str, Any],
) -> None:
    """Merge a streaming tool_call delta into the accumulated list in-place."""
    idx = tc_delta.get("index", 0)
    while len(accumulated) <= idx:
        accumulated.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    entry = accumulated[idx]
    if tc_delta.get("id"):
        entry["id"] = tc_delta["id"]
    fn_delta = tc_delta.get("function") or {}
    if fn_delta.get("name"):
        entry["function"]["name"] += fn_delta["name"]
    if fn_delta.get("arguments"):
        entry["function"]["arguments"] += fn_delta["arguments"]


_KNOWN_PROVIDER_PREFIXES = {"openai", "anthropic", "gemini", "groq", "ollama"}


def _collapse_double_provider_prefix(model_id: str) -> str:
    """Collapse ``provider/provider/model`` into ``provider/model``."""
    parts = model_id.split("/", 2)
    if len(parts) >= 3 and parts[0].lower() == parts[1].lower() and parts[0].lower() in _KNOWN_PROVIDER_PREFIXES:
        return f"{parts[0]}/{parts[2]}"
    return model_id


def _bifrost_models() -> list[str]:
    last_error: Optional[HTTPException] = None
    for path in ("/v1/models", "/openai/v1/models"):
        try:
            payload = _bifrost_request(path)
        except HTTPException as exc:
            last_error = exc
            continue

        rows = payload.get("data")
        if not isinstance(rows, list):
            continue

        raw_models: list[str] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id", "")).strip()
            if model_id:
                raw_models.append(model_id)
        if not raw_models:
            continue

        # Deduplicate double-prefixed model IDs returned by the proxy
        # (e.g. both "openai/gpt-4o" and "openai/openai/gpt-4o").
        seen: set[str] = set()
        models: list[str] = []
        for model_id in raw_models:
            canonical = _collapse_double_provider_prefix(model_id)
            if canonical not in seen:
                seen.add(canonical)
                models.append(canonical)
        if models:
            return models

    if last_error:
        raise last_error

    raise HTTPException(
        status_code=503,
        detail={
            "error": {
                "code": "WIZARD_BIFROST_NO_MODELS",
                "message": "Bifrost is reachable but no models are configured yet.",
            }
        },
    )


def _resolve_wizard_model(models: list[str], requested_model: Optional[str] = None) -> str:
    text_models = _filter_llm_model_ids(models)
    if not text_models:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "WIZARD_NO_TEXT_MODELS",
                    "message": "No text-based LLM models are configured for the Setup Wizard.",
                }
            },
        )

    requested = str(requested_model or "").strip()
    if requested:
        if requested not in models:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "WIZARD_MODEL_NOT_CONFIGURED",
                        "message": f"Wizard model '{requested}' is not configured.",
                    }
                },
            )
        if requested not in text_models:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "WIZARD_MODEL_NOT_TEXT",
                        "message": (
                            f"Wizard model '{requested}' is not a text-based model. "
                            "Choose a chat or reasoning model."
                        ),
                    }
                },
            )
        return requested

    env_model = str(LOCAL_DASHBOARD_WIZARD_MODEL or "").strip()
    if env_model and env_model in text_models:
        return env_model
    return text_models[0]


def _resolve_wizard_model_candidates(
    models: list[str],
    requested_model: Optional[str] = None,
) -> list[str]:
    selected = _resolve_wizard_model(models, requested_model=requested_model)
    if requested_model:
        return [selected]
    return _dedupe_preserve_order([selected, *_filter_llm_model_ids(models)])


def _bifrost_error_message(exc: HTTPException, fallback: str) -> str:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = detail.get("error") if isinstance(detail, dict) else {}
    if isinstance(error, dict):
        message = str(error.get("message", "")).strip()
        if message:
            return message
    return fallback


def _bifrost_setup_steps() -> list[str]:
    return [
        "Open Bifrost config: ~/.loreholm/chat-bifrost-config.json (Windows: $env:USERPROFILE\\.loreholm\\chat-bifrost-config.json).",
        "Add at least one provider model and key (OpenAI / Anthropic / Gemini).",
        "Restart gateway: cd ~/.loreholm && docker compose up -d bifrost-proxy (PowerShell: cd $env:USERPROFILE\\.loreholm; docker compose up -d bifrost-proxy).",
        "Click 'Check Bifrost' again to verify a real model response.",
    ]


def _bifrost_sample_config() -> str:
    return (
        "{\n"
        "  \"providers\": {\n"
        "    \"openai\": {\n"
        "      \"keys\": [\n"
        "        {\n"
        "          \"value\": \"sk-your-openai-key\",\n"
        "          \"models\": [\"gpt-4o-mini\", \"openai/gpt-4o-mini\"],\n"
        "          \"weight\": 1.0\n"
        "        }\n"
        "      ]\n"
        "    },\n"
        "    \"anthropic\": {\n"
        "      \"keys\": [\n"
        "        {\n"
        "          \"value\": \"sk-ant-your-anthropic-key\",\n"
        "          \"models\": [\"claude-3-5-sonnet-latest\", \"anthropic/claude-3-5-sonnet-latest\"],\n"
        "          \"weight\": 1.0\n"
        "        }\n"
        "      ]\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def _extract_chat_message_content(message: dict[str, Any]) -> str:
    content: Any = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _extract_first_choice_message(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "WIZARD_BIFROST_INVALID_RESPONSE",
                    "message": "Bifrost chat response has no choices.",
                }
            },
        )
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if not isinstance(message, dict):
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "WIZARD_BIFROST_INVALID_RESPONSE",
                    "message": "Bifrost chat response is missing the assistant message.",
                }
            },
        )
    return message


def _extract_chat_content(payload: dict[str, Any]) -> str:
    message = _extract_first_choice_message(payload)
    return _extract_chat_message_content(message)


def _prepare_wizard_messages(messages: list[WizardMessage]) -> list[dict[str, str]]:
    prepared: list[dict[str, str]] = []
    for message in messages[-20:]:
        content = str(message.content).strip()
        if not content:
            continue
        prepared.append({"role": message.role, "content": content[:6000]})
    if not prepared:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "WIZARD_INVALID_REQUEST",
                    "message": "Wizard requires at least one message.",
                }
            },
        )
    return prepared


def _schema_context_for_prompt(record: dict[str, Any]) -> str:
    from ..db.graph import _schema_payload

    schema = _schema_payload(record)
    labels = [str(item) for item in schema.get("labels", []) if str(item).strip()]
    relationships = [
        str(item) for item in schema.get("relationships", []) if str(item).strip()
    ]
    node_properties_raw = schema.get("node_properties", {})
    node_properties = (
        node_properties_raw if isinstance(node_properties_raw, dict) else {}
    )

    property_lines: list[str] = []
    for label in labels[:8]:
        props = node_properties.get(label, [])
        if not isinstance(props, list):
            continue
        clean_props = [str(prop) for prop in props if str(prop).strip()][:8]
        if clean_props:
            property_lines.append(f"- {label}: {', '.join(clean_props)}")

    lines = [
        f"database_id: {record.get('database_id', 'unknown')}",
        f"labels: {', '.join(labels[:12]) or 'none detected'}",
        f"relationships: {', '.join(relationships[:12]) or 'none detected'}",
    ]
    if property_lines:
        lines.append("node_properties:")
        lines.extend(property_lines)
    return "\n".join(lines)


def _bifrost_chat_completion(
    messages: list[dict[str, str]],
    *,
    system_prompt: str,
    requested_model: Optional[str] = None,
    max_tokens: int = 500,
    temperature: float = 0.2,
) -> tuple[str, str]:
    models = _bifrost_models()
    candidate_models = _resolve_wizard_model_candidates(models, requested_model=requested_model)
    last_http_error: Optional[HTTPException] = None
    for model in candidate_models:
        try:
            payload = _bifrost_request(
                "/v1/chat/completions",
                method="POST",
                payload={
                    "model": model,
                    "messages": [{"role": "system", "content": system_prompt}, *messages],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "drop_params": True,
                },
            )
        except HTTPException as exc:
            last_http_error = exc
            continue

        content = _extract_chat_content(payload)
        if content:
            return content, model

    if last_http_error:
        raise last_http_error

    raise HTTPException(
        status_code=502,
        detail={
            "error": {
                "code": "WIZARD_BIFROST_EMPTY_RESPONSE",
                "message": "Bifrost returned an empty response.",
            }
        },
    )


def _bifrost_probe(models: Optional[list[str]] = None) -> dict[str, Any]:
    resolved_models = models if models is not None else _bifrost_models()
    candidate_models = _resolve_wizard_model_candidates(resolved_models)
    last_http_error: Optional[HTTPException] = None
    for model in candidate_models:
        try:
            payload = _bifrost_request(
                "/v1/chat/completions",
                method="POST",
                payload={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Reply with a single word: ready",
                        }
                    ],
                    "temperature": 0,
                    "max_tokens": 16,
                    "drop_params": True,
                },
            )
        except HTTPException as exc:
            last_http_error = exc
            continue

        content = _extract_chat_content(payload)
        if content:
            return {
                "ready": True,
                "model": model,
                "response_preview": content[:120],
            }

    if last_http_error:
        raise last_http_error

    raise HTTPException(
        status_code=502,
        detail={
            "error": {
                "code": "WIZARD_BIFROST_EMPTY_RESPONSE",
                "message": "Bifrost probe returned an empty response.",
            }
        },
    )
