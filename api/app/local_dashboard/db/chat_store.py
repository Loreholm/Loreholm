"""SQLite-backed conversation and usage store for chat and wizard agents.

The database lives at ``LOCAL_DASHBOARD_CHAT_DB_FILE`` (default
``/opt/loreholm/chat.db``). It is volume-mounted from the host so data
persists through container updates, uninstalls, and reinstalls.

All public functions acquire their own connection — the module is safe to
call from any thread without external locking.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..core.config import LOCAL_DASHBOARD_CHAT_DB_FILE

_SCHEMA_VERSION = 1
_db_initialized = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(LOCAL_DASHBOARD_CHAT_DB_FILE), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_db() -> None:
    """Lazily initialize the database on first use."""
    global _db_initialized
    if _db_initialized:
        return
    _init_db()
    _db_initialized = True


def _init_db() -> None:
    """Create tables if they don't exist. Idempotent."""
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id              TEXT PRIMARY KEY,
                database_id     TEXT,
                source          TEXT NOT NULL DEFAULT 'chat',
                title           TEXT NOT NULL DEFAULT '',
                model           TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL DEFAULT '',
                tool_calls      TEXT,
                tool_call_id    TEXT,
                tool_name       TEXT,
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id, created_at);

            CREATE TABLE IF NOT EXISTS usage (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                message_id      TEXT REFERENCES messages(id) ON DELETE SET NULL,
                model           TEXT NOT NULL DEFAULT '',
                prompt_tokens   INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens    INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_usage_conversation
                ON usage(conversation_id, created_at);
        """)
    finally:
        conn.close()


# ------------------------------------------------------------------
# Conversations
# ------------------------------------------------------------------

def create_conversation(
    *,
    database_id: Optional[str] = None,
    source: str = "chat",
    title: str = "",
    model: str = "",
) -> dict[str, Any]:
    _ensure_db()
    conv_id = uuid.uuid4().hex
    now = _now_iso()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO conversations (id, database_id, source, title, model, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conv_id, database_id, source, title, model, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "id": conv_id,
        "database_id": database_id,
        "source": source,
        "title": title,
        "model": model,
        "created_at": now,
        "updated_at": now,
    }


def update_conversation(
    conversation_id: str,
    *,
    title: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    _ensure_db()
    sets: list[str] = ["updated_at = ?"]
    params: list[Any] = [_now_iso()]
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if model is not None:
        sets.append("model = ?")
        params.append(model)
    params.append(conversation_id)
    conn = _connect()
    try:
        conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def list_conversations(
    *,
    source: Optional[str] = None,
    database_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    _ensure_db()
    clauses: list[str] = []
    params: list[Any] = []
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if database_id is not None:
        clauses.append("database_id = ?")
        params.append(database_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM conversations {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_conversation(conversation_id: str) -> Optional[dict[str, Any]]:
    _ensure_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_conversation(conversation_id: str) -> bool:
    _ensure_db()
    conn = _connect()
    try:
        cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ------------------------------------------------------------------
# Messages
# ------------------------------------------------------------------

def add_message(
    conversation_id: str,
    *,
    role: str,
    content: str = "",
    tool_calls: Optional[list[dict[str, Any]]] = None,
    tool_call_id: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> str:
    _ensure_db()
    msg_id = uuid.uuid4().hex
    now = _now_iso()
    tc_json = json.dumps(tool_calls) if tool_calls else None
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, tool_calls, tool_call_id, tool_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, conversation_id, role, content, tc_json, tool_call_id, tool_name, now),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        conn.commit()
    finally:
        conn.close()
    return msg_id


def get_messages(conversation_id: str) -> list[dict[str, Any]]:
    _ensure_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("tool_calls"):
                d["tool_calls"] = json.loads(d["tool_calls"])
            result.append(d)
        return result
    finally:
        conn.close()


# ------------------------------------------------------------------
# Usage tracking
# ------------------------------------------------------------------

def record_usage(
    conversation_id: str,
    *,
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    message_id: Optional[str] = None,
) -> str:
    _ensure_db()
    usage_id = uuid.uuid4().hex
    now = _now_iso()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO usage (id, conversation_id, message_id, model, prompt_tokens, completion_tokens, total_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (usage_id, conversation_id, message_id, model, prompt_tokens, completion_tokens, total_tokens, now),
        )
        conn.commit()
    finally:
        conn.close()
    return usage_id


def get_usage(
    *,
    conversation_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _ensure_db()
    if conversation_id:
        rows_sql = "SELECT * FROM usage WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?"
        params: list[Any] = [conversation_id, limit]
    else:
        rows_sql = "SELECT * FROM usage ORDER BY created_at DESC LIMIT ?"
        params = [limit]
    conn = _connect()
    try:
        rows = conn.execute(rows_sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_usage_summary(
    *,
    conversation_id: Optional[str] = None,
    source: Optional[str] = None,
) -> dict[str, Any]:
    """Aggregate usage totals, optionally filtered."""
    _ensure_db()
    clauses: list[str] = []
    params: list[Any] = []
    if conversation_id:
        clauses.append("u.conversation_id = ?")
        params.append(conversation_id)
    if source:
        clauses.append("c.source = ?")
        params.append(source)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT COALESCE(SUM(u.prompt_tokens), 0) AS prompt_tokens, "
            f"COALESCE(SUM(u.completion_tokens), 0) AS completion_tokens, "
            f"COALESCE(SUM(u.total_tokens), 0) AS total_tokens, "
            f"COUNT(*) AS request_count "
            f"FROM usage u JOIN conversations c ON u.conversation_id = c.id {where}",
            params,
        ).fetchone()
        return dict(row) if row else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "request_count": 0}
    finally:
        conn.close()
