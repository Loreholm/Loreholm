"""Chat routes — conversation CRUD and streaming chat with agentic loop.

These endpoints serve both the cloud chat proxy (sync-token auth) and
could be called directly from the local dashboard (session auth).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..core.auth import _load_preferences, _save_preferences, require_sync_auth
from ..ai.bifrost import (
    _accumulate_tool_call_delta,
    _bifrost_chat_completion,
    _bifrost_error_message,
    _bifrost_models,
    _bifrost_request,
    _bifrost_stream_chunks,
    _extract_chat_message_content,
    _extract_first_choice_message,
    _resolve_wizard_model_candidates,
    _schema_context_for_prompt,
    _sse_event,
)
from ..ai.providers import _filter_llm_model_ids
from ..ai.chat_tools import (
    _build_chat_system_prompt,
    _chat_abort_event,
    _chat_tool_definitions,
    _chat_tool_result,
)
from ..ai.wizard_tools import _parse_tool_arguments
from ..db.chat_store import (
    add_message,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_messages,
    get_usage,
    get_usage_summary,
    list_conversations,
    record_usage,
    update_conversation,
)
from ..db.registry import _find_database, _load_registry, _registry_lock, _save_registry
from ..core.models import WizardMessage

from pydantic import BaseModel, Field

router = APIRouter()


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------

class ChatCreateRequest(BaseModel):
    database_id: str = Field(..., min_length=1, max_length=100)
    title: str = Field(default="", max_length=200)
    model: Optional[str] = Field(default=None, max_length=200)


class ChatStreamRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    messages: list[WizardMessage] = Field(default_factory=list)
    model: Optional[str] = Field(default=None, max_length=200)


class SystemPromptRequest(BaseModel):
    system_prompt: str = Field(default="", max_length=20000)


class ChatPreferencesRequest(BaseModel):
    favorite_model: Optional[str] = Field(default=None, max_length=200)


class SystemPromptDraftRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=2000)
    current: str = Field(default="", max_length=20000)
    mode: Optional[str] = Field(default="draft", max_length=16)  # "draft" or "refine"
    model: Optional[str] = Field(default=None, max_length=200)


# ------------------------------------------------------------------
# Conversation CRUD
# ------------------------------------------------------------------

@router.get("/chat/conversations")
def chat_list_conversations(
    source: Optional[str] = Query(default=None),
    database_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    convs = list_conversations(source=source, database_id=database_id, limit=limit, offset=offset)
    return {"conversations": convs, "count": len(convs)}


@router.post("/chat/conversations")
def chat_create_conversation(
    payload: ChatCreateRequest,
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    # Validate the database exists
    registry = _load_registry()
    _find_database(registry, payload.database_id)
    conv = create_conversation(
        database_id=payload.database_id,
        source="chat",
        title=payload.title,
        model=payload.model or "",
    )
    return conv


@router.get("/chat/conversations/{conversation_id}")
def chat_get_conversation(
    conversation_id: str,
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Conversation not found."}})
    msgs = get_messages(conversation_id)
    conv["messages"] = msgs
    return conv


@router.delete("/chat/conversations/{conversation_id}")
def chat_delete_conversation(
    conversation_id: str,
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    deleted = delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Conversation not found."}})
    return {"deleted": True}


# ------------------------------------------------------------------
# Per-database chat system prompt
# ------------------------------------------------------------------

@router.get("/chat/databases/{database_id}/system-prompt")
def chat_get_system_prompt(
    database_id: str,
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    registry = _load_registry()
    record = _find_database(registry, database_id)
    return {
        "database_id": database_id,
        "system_prompt": str(record.get("system_prompt") or ""),
    }


@router.put("/chat/databases/{database_id}/system-prompt")
def chat_set_system_prompt(
    database_id: str,
    payload: SystemPromptRequest,
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    with _registry_lock:
        registry = _load_registry()
        record = _find_database(registry, database_id)
        record["system_prompt"] = payload.system_prompt.strip()
        _save_registry(registry)
    return {
        "database_id": database_id,
        "system_prompt": record["system_prompt"],
    }


_PROMPT_DRAFTER_SYSTEM = (
    "You are a prompt-engineering helper for the loreholm chat agent. "
    "The loreholm chat agent is a graph-database assistant that stores and "
    "retrieves information in a graph database using Cypher tools "
    "(get_database_schema, run_readonly_query, run_query). Your job is to "
    "write a SYSTEM PROMPT that will be given to that agent.\n\n"
    "Output ONLY the system prompt text itself. No JSON, no markdown fences, no "
    "preamble, no quoting, no commentary. Do NOT describe what you wrote. Do NOT "
    "include the database schema — the caller appends that automatically. "
    "Focus on persona, tone, goals, and behavioral guidelines. Keep it concise "
    "(usually 4–12 sentences) and action-oriented."
)


@router.post("/chat/databases/{database_id}/system-prompt/draft")
def chat_draft_system_prompt(
    database_id: str,
    payload: SystemPromptDraftRequest,
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    """Use Bifrost to draft (or refine) a system prompt for this database's
    chat agent based on a short user instruction. Returns the drafted text;
    the caller is responsible for saving it via the PUT endpoint.
    """
    registry = _load_registry()
    record = _find_database(registry, database_id)
    schema_context = _schema_context_for_prompt(record)

    mode = (payload.mode or "draft").strip().lower()
    parts: list[str] = [
        f"Database name: {record.get('name') or database_id}",
    ]
    if schema_context:
        parts.append(f"Database schema (for your reference; do NOT echo into the prompt):\n{schema_context}")

    user_instruction = payload.instruction.strip()
    current = payload.current.strip()

    if mode == "refine" and current:
        parts.append(
            "Existing system prompt (refine it according to the user's instruction; "
            "preserve what works, change what's asked):\n" + current
        )
        parts.append("User's refinement request:\n" + user_instruction)
    else:
        parts.append(
            "Write a fresh system prompt based on this description of the desired "
            "agent behavior:\n" + user_instruction
        )

    try:
        content, model = _bifrost_chat_completion(
            [{"role": "user", "content": "\n\n".join(parts)}],
            system_prompt=_PROMPT_DRAFTER_SYSTEM,
            requested_model=(payload.model or "").strip() or None,
            max_tokens=800,
            temperature=0.4,
        )
    except HTTPException as exc:
        raise exc

    drafted = content.strip()
    # Strip accidental markdown fences the model sometimes adds despite the
    # instruction.
    if drafted.startswith("```"):
        lines = drafted.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        drafted = "\n".join(lines).strip()

    if not drafted:
        raise HTTPException(
            status_code=502,
            detail={"error": {"code": "PROMPT_DRAFT_EMPTY", "message": "Drafter returned empty text."}},
        )

    return {
        "database_id": database_id,
        "prompt": drafted[:20000],
        "model": model,
        "mode": "refine" if mode == "refine" and current else "draft",
    }


# ------------------------------------------------------------------
# Usage
# ------------------------------------------------------------------

@router.get("/chat/usage")
def chat_get_usage(
    conversation_id: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    summary = get_usage_summary(conversation_id=conversation_id, source=source)
    return summary


# ------------------------------------------------------------------
# Preferences (favorite model)
# ------------------------------------------------------------------
#
# The favorite model is a single user-wide setting shared between the
# wizard and the chat SPA. Storage lives in the dashboard preferences
# file under the legacy key ``favorite_wizard_model``; the chat API
# exposes it as ``favorite_model`` to decouple clients from that name.

def _chat_preferences_payload() -> dict[str, Any]:
    prefs = _load_preferences()
    return {"favorite_model": str(prefs.get("favorite_wizard_model") or "")}


@router.get("/chat/preferences")
def chat_get_preferences(
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    return _chat_preferences_payload()


@router.put("/chat/preferences")
def chat_update_preferences(
    payload: ChatPreferencesRequest,
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    if payload.favorite_model is not None:
        prefs = _load_preferences()
        value = payload.favorite_model.strip()
        if value:
            prefs["favorite_wizard_model"] = value
        else:
            prefs.pop("favorite_wizard_model", None)
        _save_preferences(prefs)
    return _chat_preferences_payload()


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------

@router.get("/chat/models")
def chat_list_models(
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    """Return the Bifrost-reachable model list for the chat model picker.

    Filtered to text-in/text-out LLMs — embeddings, image, audio, etc. are
    excluded until the chat client supports those modalities.
    """
    try:
        models = _filter_llm_model_ids(_bifrost_models())
    except HTTPException:
        # Keep the chat UI usable even if Bifrost is unreachable — the
        # wizard prompt endpoint already surfaces the configuration error.
        models = []
    return {"models": models}


# ------------------------------------------------------------------
# Streaming chat
# ------------------------------------------------------------------

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


_HISTORY_MESSAGE_LIMIT = 40


def _history_to_model_messages(
    conversation_id: str,
    *,
    limit: int = _HISTORY_MESSAGE_LIMIT,
) -> list[dict[str, Any]]:
    """Load stored messages for a conversation and convert them to the
    OpenAI-compatible message shape expected by Bifrost.

    Keeps only the last ``limit`` messages so token cost stays bounded.
    If the trimmed window begins with an orphan tool message (whose matching
    assistant tool_calls message fell off the edge), drop leading tool
    messages — some providers reject a tool message without its preceding
    assistant tool_calls.
    """
    raw = get_messages(conversation_id)
    if not raw:
        return []
    trimmed = raw[-limit:] if len(raw) > limit else raw
    while trimmed and trimmed[0].get("role") == "tool":
        trimmed = trimmed[1:]

    converted: list[dict[str, Any]] = []
    for msg in trimmed:
        role = msg.get("role") or ""
        content = msg.get("content") or ""
        if role == "user":
            converted.append({"role": "user", "content": content})
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                converted.append({
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": tool_calls,
                })
            elif content:
                converted.append({"role": "assistant", "content": content})
        elif role == "tool":
            converted.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id") or "",
                "name": msg.get("tool_name") or "unknown",
                "content": content,
            })
    return converted


def _chat_stream_events(
    conversation_id: str,
    messages: list[dict[str, str]],
    *,
    database_id: str,
    requested_model: Optional[str] = None,
    max_tokens: int = 1200,
    temperature: float = 0.2,
    max_rounds: int = 8,
):
    """Generator yielding SSE events for the chat agentic loop."""
    registry = _load_registry()
    record = _find_database(registry, database_id)
    system_prompt = _build_chat_system_prompt(record)

    models = _bifrost_models()
    candidate_models = _resolve_wizard_model_candidates(models, requested_model=requested_model)
    tools = _chat_tool_definitions()
    last_error_message: Optional[str] = None

    # Persist the newly-received user message(s) before loading history so
    # they appear in the conversation sent to the model.
    for msg in messages:
        add_message(conversation_id, role=msg["role"], content=msg["content"])

    history_messages = _history_to_model_messages(conversation_id)

    for model in candidate_models:
        conversation: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *history_messages,
        ]

        try:
            for _ in range(max(1, max_rounds)):
                if _chat_abort_event.is_set():
                    _chat_abort_event.clear()
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
                            text_piece = delta.get("content")
                            if isinstance(text_piece, str) and text_piece:
                                accumulated_content += text_piece
                                if not accumulated_tool_calls:
                                    yield _sse_event("text_delta", {"content": text_piece})
                            tc_deltas = delta.get("tool_calls")
                            if isinstance(tc_deltas, list):
                                for tc_delta in tc_deltas:
                                    if isinstance(tc_delta, dict):
                                        _accumulate_tool_call_delta(accumulated_tool_calls, tc_delta)
                        # Usage in final chunk
                        chunk_usage = _extract_usage_from_payload(chunk)
                        if chunk_usage:
                            stream_usage = chunk_usage
                except HTTPException:
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
                        raise inner_exc

                # Record usage if available
                if stream_usage:
                    record_usage(
                        conversation_id,
                        model=model,
                        prompt_tokens=stream_usage["prompt_tokens"],
                        completion_tokens=stream_usage["completion_tokens"],
                        total_tokens=stream_usage["total_tokens"],
                    )

                # --- Process result ---
                if accumulated_tool_calls:
                    tool_calls = accumulated_tool_calls[:6]
                    conversation.append({
                        "role": "assistant",
                        "content": accumulated_content or None,
                        "tool_calls": tool_calls,
                    })
                    # Persist assistant message with tool calls
                    add_message(
                        conversation_id,
                        role="assistant",
                        content=accumulated_content or "",
                        tool_calls=tool_calls,
                    )

                    for tool_call in tool_calls:
                        tool_id = str(tool_call.get("id", "")).strip() or "tool_call"
                        fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                        name = str(fn.get("name", "")).strip()
                        arguments = _parse_tool_arguments(fn.get("arguments"))

                        yield _sse_event("tool_start", {"tool": name or "unknown", "arguments": arguments})

                        if not name:
                            result = {"error": {"code": "CHAT_INVALID_TOOL_CALL", "message": "Tool call missing function name."}}
                        else:
                            try:
                                result = _chat_tool_result(name, arguments, database_id=database_id)
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
                        add_message(
                            conversation_id,
                            role="tool",
                            content=json.dumps(result),
                            tool_call_id=tool_id,
                            tool_name=name or "unknown",
                        )
                    continue

                # No tool calls — text response
                if not stream_ok and accumulated_content:
                    yield _sse_event("text_delta", {"content": accumulated_content})

                # Persist assistant response
                if accumulated_content:
                    add_message(conversation_id, role="assistant", content=accumulated_content)

                # Update conversation model
                update_conversation(conversation_id, model=model)

                yield _sse_event("done", {"model": model})
                return

            # Max rounds exhausted — final summary
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
                    add_message(conversation_id, role="assistant", content=summary_text)
                    update_conversation(conversation_id, model=model)
                    yield _sse_event("done", {"model": model})
                    return
            except HTTPException:
                pass
            if accumulated_content:
                yield _sse_event("text_delta", {"content": accumulated_content})
            yield _sse_event("done", {"model": model})
            return

        except HTTPException as exc:
            last_error_message = _bifrost_error_message(exc, str(exc))
            continue

    yield _sse_event("error", {"message": last_error_message or "All available models failed."})


@router.post("/chat/stream")
def chat_stream(
    payload: ChatStreamRequest,
    _: None = Depends(require_sync_auth),
):
    """SSE streaming endpoint for chat."""
    _chat_abort_event.clear()

    conv = get_conversation(payload.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Conversation not found."}})

    database_id = conv["database_id"]
    if not database_id:
        raise HTTPException(status_code=400, detail={"error": {"code": "NO_DATABASE", "message": "Conversation has no database_id."}})

    messages: list[dict[str, str]] = []
    for msg in payload.messages[-20:]:
        content = str(msg.content).strip()
        if content:
            messages.append({"role": msg.role, "content": content[:6000]})
    if not messages:
        raise HTTPException(status_code=400, detail={"error": {"code": "EMPTY_MESSAGES", "message": "At least one message is required."}})

    def generate():
        yield from _chat_stream_events(
            payload.conversation_id,
            messages,
            database_id=database_id,
            requested_model=(payload.model or "").strip() or None,
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/abort")
def chat_abort(
    _: None = Depends(require_sync_auth),
) -> dict[str, Any]:
    """Signal the in-flight chat loop to stop."""
    _chat_abort_event.set()
    return {"aborted": True}
