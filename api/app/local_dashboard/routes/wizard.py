from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..core.auth import _now_iso, require_local_session
from ..ai.bifrost import (
    _bifrost_chat_completion,
    _bifrost_error_message,
    _bifrost_models,
    _bifrost_probe,
    _bifrost_request,
    _bifrost_sample_config,
    _bifrost_setup_steps,
    _bifrost_stream_chunks,
    _bifrost_url,
    _extract_chat_message_content,
    _extract_first_choice_message,
    _prepare_wizard_messages,
    _resolve_wizard_model_candidates,
    _schema_context_for_prompt,
    _accumulate_tool_call_delta,
    _sse_event,
)
from ..services import bifrost_client
from ..core.models import (
    BifrostConfigRequest,
    BifrostDiscoverModelsRequest,
    BifrostDisconnectProviderRequest,
    PromptDraftRequest,
    WizardChatRequest,
)
from ..ai.providers import (
    _build_model_descriptor,
    _dedupe_model_descriptors,
    _discover_provider_model_descriptors,
    _expanded_bifrost_providers,
    _normalize_provider_model,
    _provider_prefix,
    _sorted_model_descriptors,
)
from ..db.registry import _find_database, _load_registry
from ..ai.wizard_tools import (
    _TOOLS_REQUIRE_APPROVAL,
    _WIZARD_SYSTEM_PROMPT,
    _bifrost_chat_completion_with_tools,
    _extract_json_object,
    _parse_tool_arguments,
    _wizard_abort_event,
    _wizard_tool_definitions,
    _wizard_tool_result,
)
from ..db.chat_store import (
    add_message as _store_message,
    create_conversation as _store_create_conversation,
    record_usage as _store_record_usage,
    update_conversation as _store_update_conversation,
)
from ..db.registry import _slugify_database_id

router = APIRouter()


@router.get("/wizard/bifrost/status")
def wizard_bifrost_status(_: None = Depends(require_local_session)) -> dict[str, Any]:
    try:
        models = _bifrost_models()
    except HTTPException as exc:
        error_message = _bifrost_error_message(exc, "Bifrost is unavailable.")
        return {
            "available": False,
            "ready": False,
            "probe_success": False,
            "models": [],
            "model_count": 0,
            "error": error_message or "Bifrost is unavailable.",
            "probe_error": error_message or "Bifrost is unavailable.",
            "setup_steps": _bifrost_setup_steps(),
            "sample_config": _bifrost_sample_config(),
            "url": _bifrost_url("/v1"),
        }
    try:
        probe = _bifrost_probe(models)
    except HTTPException as exc:
        probe_error = _bifrost_error_message(
            exc,
            "Bifrost model probe failed. Configure provider keys and retry.",
        )
        return {
            "available": True,
            "ready": False,
            "probe_success": False,
            "models": models,
            "model_count": len(models),
            "error": "",
            "probe_error": probe_error,
            "setup_steps": _bifrost_setup_steps(),
            "sample_config": _bifrost_sample_config(),
            "url": _bifrost_url("/v1"),
        }
    return {
        "available": True,
        "ready": True,
        "probe_success": True,
        "models": models,
        "model_count": len(models),
        "probe_model": probe.get("model"),
        "probe_preview": probe.get("response_preview", ""),
        "error": "",
        "probe_error": "",
        "setup_steps": [],
        "sample_config": "",
        "url": _bifrost_url("/v1"),
    }


@router.post("/wizard/chat")
def wizard_chat(
    payload: WizardChatRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    _wizard_abort_event.clear()
    is_resume = payload.conversation_state is not None
    messages = [] if is_resume else _prepare_wizard_messages(payload.messages)
    content, model, tool_events, pending_approval = _bifrost_chat_completion_with_tools(
        messages,
        system_prompt=_WIZARD_SYSTEM_PROMPT,
        preferred_database_id=(payload.database_id or "").strip() or None,
        requested_model=(payload.model or "").strip() or None,
        max_tokens=600,
        temperature=0.2,
        conversation_state=payload.conversation_state,
        approved_tool_call_id=payload.approved_tool_call_id,
        denied_tool_call_id=payload.denied_tool_call_id,
    )
    if pending_approval is not None:
        return {"pending_approval": pending_approval, "tool_events": tool_events}
    return {
        "assistant_message": content,
        "model": model,
        "tool_events": tool_events,
    }


@router.post("/wizard/chat/abort")
def wizard_chat_abort(
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    """Signal the in-flight wizard chat loop to stop after the current tool round."""
    _wizard_abort_event.set()
    return {"aborted": True}


# ---------------------------------------------------------------------------
# Streaming wizard chat (SSE)
# ---------------------------------------------------------------------------


def _extract_usage_from_payload(payload: dict[str, Any]) -> Optional[dict[str, int]]:
    """Extract usage dict from an OpenAI-compatible response."""
    usage = payload.get("usage")
    if isinstance(usage, dict):
        return {
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
        }
    return None


def _wizard_chat_stream_events(
    messages: list[dict[str, str]],
    *,
    system_prompt: str,
    preferred_database_id: Optional[str],
    requested_model: Optional[str] = None,
    max_tokens: int = 600,
    temperature: float = 0.2,
    max_rounds: int = 8,
    conversation_state: Optional[list[dict[str, Any]]] = None,
    approved_tool_call_id: Optional[str] = None,
    denied_tool_call_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
):
    """Generator that yields SSE events for the wizard chat agentic loop.

    Event types:
        tool_start  - a tool is about to be executed
        tool_end    - a tool finished executing
        text_delta  - incremental text from the assistant
        pending_approval - a tool needs user approval before executing
        done        - stream finished successfully
        error       - an error occurred
    """
    models = _bifrost_models()
    candidate_models = _resolve_wizard_model_candidates(models, requested_model=requested_model)
    tools = _wizard_tool_definitions()
    last_error_message: Optional[str] = None

    # Persist user messages on first call (not resume)
    if conversation_id and conversation_state is None:
        for msg in messages:
            if msg.get("role") in ("user", "assistant"):
                _store_message(conversation_id, role=msg["role"], content=msg.get("content", ""))

    for model in candidate_models:
        # Build (or restore) conversation
        if conversation_state is not None:
            conversation: list[dict[str, Any]] = list(conversation_state)
        else:
            conversation = [{"role": "system", "content": system_prompt}, *messages]

        # Handle resume after approval/denial
        resuming = conversation_state is not None and (
            approved_tool_call_id is not None or denied_tool_call_id is not None
        )
        if resuming:
            last_assistant = next(
                (m for m in reversed(conversation) if m.get("role") == "assistant" and m.get("tool_calls")),
                None,
            )
            answered_ids = {m.get("tool_call_id") for m in conversation if m.get("role") == "tool"}
            if last_assistant:
                for tc in last_assistant.get("tool_calls", []) or []:
                    tc_id = str(tc.get("id", "")).strip() or "tool_call"
                    if tc_id in answered_ids:
                        continue
                    fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                    name = str(fn.get("name", "")).strip()
                    arguments = _parse_tool_arguments(fn.get("arguments"))

                    yield _sse_event("tool_start", {"tool": name or "unknown", "arguments": arguments})

                    if name in _TOOLS_REQUIRE_APPROVAL:
                        if tc_id == approved_tool_call_id:
                            try:
                                result = _wizard_tool_result(name, arguments, preferred_database_id=preferred_database_id)
                            except HTTPException as exc:
                                result = {"error": {"message": _bifrost_error_message(exc, str(exc))}}
                        else:
                            result = {"error": {"code": "USER_DENIED", "message": "The user denied this action."}}
                    else:
                        if not name:
                            result = {"error": {"code": "WIZARD_INVALID_TOOL_CALL", "message": "Tool call missing function name."}}
                        else:
                            try:
                                result = _wizard_tool_result(name, arguments, preferred_database_id=preferred_database_id)
                            except HTTPException as exc:
                                result = {"error": {"message": _bifrost_error_message(exc, str(exc))}}

                    ok = not isinstance(result.get("error"), dict)
                    yield _sse_event("tool_end", {"tool": name or "unknown", "ok": ok})
                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": name or "unknown",
                        "content": json.dumps(result),
                    })

        try:
            for _ in range(max(1, max_rounds)):
                if _wizard_abort_event.is_set():
                    _wizard_abort_event.clear()
                    yield _sse_event("text_delta", {"content": "(Stopped)"})
                    yield _sse_event("done", {"model": model})
                    return

                # --- Stream from Bifrost ---
                accumulated_content = ""
                accumulated_tool_calls: list[dict[str, Any]] = []
                stream_ok = True
                stream_usage: Optional[dict[str, int]] = None

                try:
                    for chunk in _bifrost_stream_chunks(
                        "/v1/chat/completions",
                        payload={
                            "model": model,
                            "messages": conversation,
                            "tools": tools,
                            "tool_choice": "auto",
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "stream": True,
                            "stream_options": {"include_usage": True},
                            "drop_params": True,
                        },
                    ):
                        choices = chunk.get("choices")
                        if isinstance(choices, list) and choices:
                            delta = choices[0].get("delta") or {}

                            # Text content
                            text_piece = delta.get("content")
                            if isinstance(text_piece, str) and text_piece:
                                accumulated_content += text_piece
                                # Only forward text deltas when no tool calls are accumulating
                                if not accumulated_tool_calls:
                                    yield _sse_event("text_delta", {"content": text_piece})

                            # Tool call deltas
                            tc_deltas = delta.get("tool_calls")
                            if isinstance(tc_deltas, list):
                                for tc_delta in tc_deltas:
                                    if isinstance(tc_delta, dict):
                                        _accumulate_tool_call_delta(accumulated_tool_calls, tc_delta)

                        # Usage in final chunk
                        chunk_usage = _extract_usage_from_payload(chunk)
                        if chunk_usage:
                            stream_usage = chunk_usage
                except HTTPException as exc:
                    # Streaming failed — try falling back to non-streaming
                    stream_ok = False
                    try:
                        payload = _bifrost_request(
                            "/v1/chat/completions",
                            method="POST",
                            payload={
                                "model": model,
                                "messages": conversation,
                                "tools": tools,
                                "tool_choice": "auto",
                                "temperature": temperature,
                                "max_tokens": max_tokens,
                                "drop_params": True,
                            },
                        )
                        message = _extract_first_choice_message(payload)
                        accumulated_content = _extract_chat_message_content(message)
                        raw_tc = message.get("tool_calls")
                        accumulated_tool_calls = raw_tc if isinstance(raw_tc, list) and raw_tc else []
                        stream_usage = _extract_usage_from_payload(payload)
                    except HTTPException as inner_exc:
                        raise inner_exc from exc

                # Record usage if available
                if conversation_id and stream_usage:
                    try:
                        _store_record_usage(
                            conversation_id,
                            model=model,
                            prompt_tokens=stream_usage["prompt_tokens"],
                            completion_tokens=stream_usage["completion_tokens"],
                            total_tokens=stream_usage["total_tokens"],
                        )
                    except Exception:
                        pass

                # --- Process result ---
                if accumulated_tool_calls:
                    tool_calls = accumulated_tool_calls[:6]

                    # Check for restricted tools
                    first_restricted = next(
                        (
                            (
                                str(tc.get("id", "")).strip() or "tool_call",
                                str((tc.get("function") or {}).get("name", "") or "").strip(),
                                _parse_tool_arguments((tc.get("function") or {}).get("arguments")),
                            )
                            for tc in tool_calls
                            if str((tc.get("function") or {}).get("name", "") or "").strip()
                            in _TOOLS_REQUIRE_APPROVAL
                        ),
                        None,
                    )

                    conversation.append({
                        "role": "assistant",
                        "content": accumulated_content or None,
                        "tool_calls": tool_calls,
                    })
                    # Persist assistant message with tool calls
                    if conversation_id:
                        try:
                            _store_message(conversation_id, role="assistant", content=accumulated_content or "", tool_calls=tool_calls)
                        except Exception:
                            pass

                    if first_restricted is not None:
                        tc_id, tc_name, tc_args = first_restricted
                        yield _sse_event("pending_approval", {
                            "tool_call_id": tc_id,
                            "tool_name": tc_name,
                            "arguments": tc_args,
                            "conversation_state": conversation,
                        })
                        return

                    # Execute all tools
                    for tool_call in tool_calls:
                        tool_id = str(tool_call.get("id", "")).strip() or "tool_call"
                        fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                        name = str(fn.get("name", "")).strip()
                        arguments = _parse_tool_arguments(fn.get("arguments"))

                        yield _sse_event("tool_start", {"tool": name or "unknown", "arguments": arguments})

                        if not name:
                            result = {"error": {"code": "WIZARD_INVALID_TOOL_CALL", "message": "Tool call missing function name."}}
                        else:
                            try:
                                result = _wizard_tool_result(name, arguments, preferred_database_id=preferred_database_id)
                            except HTTPException as exc:
                                result = {"error": {"message": _bifrost_error_message(exc, str(exc))}}

                        ok = not isinstance(result.get("error"), dict)
                        yield _sse_event("tool_end", {"tool": name or "unknown", "ok": ok})

                        conversation.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "name": name or "unknown",
                            "content": json.dumps(result),
                        })
                        # Persist tool result
                        if conversation_id:
                            try:
                                _store_message(conversation_id, role="tool", content=json.dumps(result), tool_call_id=tool_id, tool_name=name or "unknown")
                            except Exception:
                                pass
                    continue

                # No tool calls — text response (already streamed if stream_ok)
                if not stream_ok and accumulated_content:
                    yield _sse_event("text_delta", {"content": accumulated_content})
                # Persist final assistant response
                if conversation_id and accumulated_content:
                    try:
                        _store_message(conversation_id, role="assistant", content=accumulated_content)
                        _store_update_conversation(conversation_id, model=model)
                    except Exception:
                        pass
                yield _sse_event("done", {"model": model})
                return

            # Max rounds exhausted — make a final summary call (stream it)
            try:
                summary_text = ""
                for chunk in _bifrost_stream_chunks(
                    "/v1/chat/completions",
                    payload={
                        "model": model,
                        "messages": conversation,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "stream": True,
                        "drop_params": True,
                    },
                ):
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text_piece = delta.get("content")
                    if isinstance(text_piece, str) and text_piece:
                        summary_text += text_piece
                        yield _sse_event("text_delta", {"content": text_piece})
                if summary_text:
                    yield _sse_event("done", {"model": model})
                    return
            except HTTPException:
                pass
            # Fallback: send whatever content we have
            if accumulated_content:
                yield _sse_event("text_delta", {"content": accumulated_content})
            yield _sse_event("done", {"model": model})
            return

        except HTTPException as exc:
            last_error_message = _bifrost_error_message(exc, str(exc))
            continue

    # All models exhausted
    yield _sse_event("error", {"message": last_error_message or "All available models failed."})


@router.post("/wizard/chat/stream")
def wizard_chat_stream(
    payload: WizardChatRequest,
    _: None = Depends(require_local_session),
):
    """SSE streaming endpoint for wizard chat."""
    _wizard_abort_event.clear()
    is_resume = payload.conversation_state is not None
    messages = [] if is_resume else _prepare_wizard_messages(payload.messages)

    # Create or reuse a wizard conversation for persistence
    conv_id: Optional[str] = None
    if not is_resume:
        try:
            conv = _store_create_conversation(
                database_id=(payload.database_id or "").strip() or None,
                source="wizard",
                title="Wizard session",
            )
            conv_id = conv["id"]
        except Exception:
            pass

    def generate():
        yield from _wizard_chat_stream_events(
            messages,
            system_prompt=_WIZARD_SYSTEM_PROMPT,
            preferred_database_id=(payload.database_id or "").strip() or None,
            requested_model=(payload.model or "").strip() or None,
            max_tokens=600,
            temperature=0.2,
            conversation_state=payload.conversation_state,
            approved_tool_call_id=payload.approved_tool_call_id,
            denied_tool_call_id=payload.denied_tool_call_id,
            conversation_id=conv_id,
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/wizard/recommendation")
def wizard_recommendation(
    payload: WizardChatRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    messages = _prepare_wizard_messages(payload.messages)
    system_prompt = (
        "You are a database setup planner for loreholm. "
        "Return only JSON with keys: database_id, name, sslmode, ready_to_create, reasoning. "
        "database_id must be lowercase, alphanumeric with optional _ or -. "
        "sslmode must be either disable or require. "
        "ready_to_create must be a boolean. "
        "reasoning must be short."
    )
    content, model = _bifrost_chat_completion(
        messages,
        system_prompt=system_prompt,
        requested_model=(payload.model or "").strip() or None,
        max_tokens=300,
        temperature=0.1,
    )
    parsed = _extract_json_object(content)
    if not parsed:
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "WIZARD_INVALID_RECOMMENDATION",
                    "message": "Wizard model returned non-JSON recommendation output.",
                }
            },
        )

    raw_database_id = str(parsed.get("database_id", "")).strip()
    raw_name = str(parsed.get("name", "")).strip()
    raw_sslmode = str(parsed.get("sslmode", "disable")).strip().lower()
    raw_reasoning = str(parsed.get("reasoning", "")).strip()
    raw_ready = parsed.get("ready_to_create", True)
    ready_to_create = bool(raw_ready)
    if isinstance(raw_ready, str):
        ready_to_create = raw_ready.strip().lower() in {"1", "true", "yes", "y"}

    recommendation = {
        "database_id": _slugify_database_id(raw_database_id or raw_name or "memory-db"),
        "name": (raw_name or "Memory").strip()[:100],
        "sslmode": "require" if raw_sslmode == "require" else "disable",
        "ready_to_create": ready_to_create,
        "reasoning": raw_reasoning or "Recommended from onboarding conversation.",
        "model": model,
    }
    return recommendation


@router.post("/wizard/prompt-draft")
def wizard_prompt_draft(
    payload: PromptDraftRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    goal = payload.goal.strip()
    audience = (payload.audience or "").strip()
    constraints = (payload.constraints or "").strip()
    extra_context = (payload.context or "").strip()
    database_context = ""

    if payload.database_id:
        registry = _load_registry()
        record = _find_database(registry, payload.database_id)
        database_context = _schema_context_for_prompt(record)

    request_parts = [
        f"Goal:\n{goal}",
        f"Audience:\n{audience or 'general technical user'}",
        f"Constraints:\n{constraints or 'none'}",
        f"Additional context:\n{extra_context or 'none'}",
    ]
    if database_context:
        request_parts.append(f"Database schema context:\n{database_context}")
    user_prompt = "\n\n".join(request_parts)

    system_prompt = (
        "You are the loreholm Cypher expert. "
        "Your goal is to write optimized, correct Cypher queries for ArcadeDB based on the provided schema. "
        "Return only JSON with keys: title, prompt, notes. "
        "title: short descriptive name for the query. "
        "prompt: the actual Cypher query text (nothing else). "
        "notes: short explanation of how the query works and any performance tips."
    )
    content, model = _bifrost_chat_completion(
        [{"role": "user", "content": user_prompt}],
        system_prompt=system_prompt,
        requested_model=(payload.model or "").strip() or None,
        max_tokens=700,
        temperature=0.25,
    )
    parsed = _extract_json_object(content)
    if parsed:
        title = str(parsed.get("title", "")).strip() or "Prompt Draft"
        prompt_text = str(parsed.get("prompt", "")).strip()
        notes = str(parsed.get("notes", "")).strip()
    else:
        title = "Prompt Draft"
        prompt_text = content.strip()
        notes = "Model returned plain text instead of JSON."

    if not prompt_text:
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "WIZARD_EMPTY_PROMPT_DRAFT",
                    "message": "Wizard model returned an empty prompt draft.",
                }
            },
        )

    return {
        "title": title[:80],
        "prompt": prompt_text[:12000],
        "notes": notes[:500],
        "model": model,
        "used_database_context": bool(database_context),
    }


@router.get("/wizard/bifrost/providers")
def wizard_bifrost_providers(
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    """Return a UI-safe summary of currently configured providers.

    Used by the dashboard's AI Models view to populate the model grid and
    indicate which providers already have a saved key, without ever sending
    raw key material back to the browser.
    """
    try:
        providers = bifrost_client.get_provider_summaries()
    except bifrost_client.BifrostClientError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "BIFROST_PROVIDERS_LIST_FAILED",
                    "message": str(exc),
                }
            },
        ) from exc
    return {"providers": providers}


@router.post("/wizard/bifrost/config")
def update_bifrost_config(
    payload: BifrostConfigRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    # Backfill missing credentials from the saved Bifrost config so the UI
    # never has to re-transmit a key the user already entered.
    resolved_providers = []
    for entry in payload.providers:
        provider_name = str(entry.provider or "").strip().lower()
        is_local = provider_name in {"local", "ollama"}
        api_key = str(entry.api_key or "").strip()
        base_url = str(entry.base_url or "").strip()
        if is_local and not base_url:
            saved = bifrost_client.get_saved_credential(provider_name)
            if saved:
                entry = entry.model_copy(update={"base_url": saved})
        elif not is_local and not api_key:
            saved = bifrost_client.get_saved_credential(provider_name)
            if saved:
                entry = entry.model_copy(update={"api_key": saved})
        resolved_providers.append(entry)

    expanded_providers, discovered_counts = _expanded_bifrost_providers(resolved_providers)
    configs, configured_model_count = bifrost_client.build_provider_configs(expanded_providers)
    # Merge semantics: only upsert the providers the user touched — leave any
    # other provider (e.g. an ollama entry not in this request) untouched.
    try:
        for name, config in configs.items():
            bifrost_client.upsert_provider(name, config)
    except bifrost_client.BifrostClientError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "BIFROST_CONFIG_UPDATE_FAILED",
                    "message": str(exc),
                }
            },
        ) from exc
    return {
        "ok": True,
        "message": "Bifrost configuration updated.",
        "configured_model_count": configured_model_count,
        "discovered_models": discovered_counts,
    }


@router.post("/wizard/bifrost/discover-models")
def wizard_bifrost_discover_models(
    payload: BifrostDiscoverModelsRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    provider = payload.provider.strip().lower()
    _provider_prefix(provider)
    is_local = provider in {"local", "ollama"}
    api_key = str(payload.api_key or "").strip()
    base_url = str(payload.base_url or "").strip().rstrip("/")
    if not is_local and not api_key:
        api_key = bifrost_client.get_saved_credential(provider) or ""
    if is_local and not base_url:
        base_url = (bifrost_client.get_saved_credential(provider) or "").rstrip("/")
    if not is_local and not api_key:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "BIFROST_API_KEY_REQUIRED",
                    "message": "api_key is required.",
                }
            },
        )
    if is_local and not base_url:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "BIFROST_BASE_URL_REQUIRED",
                    "message": "base_url is required for local providers.",
                }
            },
        )

    preferred_model = _normalize_provider_model(provider, payload.preferred_model or "")
    discovered_entries = _discover_provider_model_descriptors(provider, base_url if is_local else api_key)
    if preferred_model:
        preferred_entry = _build_model_descriptor(provider, preferred_model)
        if preferred_entry:
            discovered_entries = _dedupe_model_descriptors([preferred_entry, *discovered_entries])
    model_entries = _sorted_model_descriptors(discovered_entries)
    models = [str(entry.get("id", "")).strip() for entry in model_entries if str(entry.get("id", "")).strip()]
    if not model_entries:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "BIFROST_NO_MODELS_DISCOVERED",
                    "message": (
                        f"No models discovered for provider '{provider}'. "
                        "Check API key permissions and retry."
                    ),
                }
            },
        )

    return {
        "provider": provider,
        "models": models,
        "model_entries": model_entries,
        "count": len(models),
    }


@router.post("/wizard/bifrost/disconnect-provider")
def wizard_bifrost_disconnect_provider(
    payload: BifrostDisconnectProviderRequest,
    _: None = Depends(require_local_session),
) -> dict[str, Any]:
    provider = str(payload.provider or "").strip().lower()
    bifrost_name = _provider_prefix(provider)
    try:
        bifrost_client.delete_provider(bifrost_name)
    except bifrost_client.BifrostClientError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "BIFROST_DISCONNECT_FAILED",
                    "message": str(exc),
                }
            },
        ) from exc

    return {
        "ok": True,
        "provider": provider,
        "message": f"Disconnected provider '{provider}'.",
    }
