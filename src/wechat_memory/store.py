from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from .db import json_text, now_iso
from .message_content import readable_content


def upsert_raw(
    conn: sqlite3.Connection,
    kind: str,
    source_key: str,
    payload: dict[str, Any],
) -> int:
    encoded = json_text(payload)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    observed = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_record_versions(source_key,payload_hash,observed_at,payload_json)
        VALUES(?,?,?,?)
        """,
        (source_key, digest, observed, encoded),
    )
    conn.execute(
        """
        INSERT INTO raw_records(source_kind,source_key,observed_at,payload_json,payload_hash)
        VALUES(?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET
          observed_at=excluded.observed_at,payload_json=excluded.payload_json,payload_hash=excluded.payload_hash
        WHERE raw_records.payload_hash<>excluded.payload_hash
        """,
        (kind, source_key, observed, encoded, digest),
    )
    return int(
        conn.execute(
            "SELECT id FROM raw_records WHERE source_key=?", (source_key,)
        ).fetchone()[0]
    )


def upsert_person(
    conn: sqlite3.Connection,
    external_id: str,
    display_name: str,
    *,
    source: str = "import",
    raw: dict[str, Any] | None = None,
    remark: str | None = None,
    nickname: str | None = None,
    alias: str | None = None,
    avatar_url: str | None = None,
) -> int:
    stamp = now_iso()
    conn.execute(
        """
        INSERT INTO people(wxid,display_name,remark,nickname,alias,avatar_url,source,raw_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(wxid) DO UPDATE SET
          display_name=CASE WHEN excluded.display_name<>'' THEN excluded.display_name ELSE people.display_name END,
          remark=COALESCE(excluded.remark,people.remark),
          nickname=COALESCE(excluded.nickname,people.nickname),
          alias=COALESCE(excluded.alias,people.alias),
          avatar_url=COALESCE(excluded.avatar_url,people.avatar_url),
          source=excluded.source,
          raw_json=CASE WHEN excluded.raw_json<>'{}' THEN excluded.raw_json ELSE people.raw_json END,
          updated_at=excluded.updated_at
        """,
        (
            external_id,
            display_name or external_id,
            remark,
            nickname,
            alias,
            avatar_url,
            source,
            json_text(raw or {}),
            stamp,
            stamp,
        ),
    )
    return int(
        conn.execute("SELECT id FROM people WHERE wxid=?", (external_id,)).fetchone()[0]
    )


def upsert_chat(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    stamp = now_iso()
    external_id = str(item.get("username") or item.get("chat") or "")
    conn.execute(
        """
        INSERT INTO chats(wxid,name,chat_type,last_ts,raw_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(wxid) DO UPDATE SET
          name=excluded.name,chat_type=excluded.chat_type,last_ts=excluded.last_ts,
          raw_json=excluded.raw_json,updated_at=excluded.updated_at
        """,
        (
            external_id,
            str(item.get("chat") or external_id),
            str(
                item.get("chat_type")
                or "private"
            ),
            item.get("timestamp"),
            json_text(item),
            stamp,
            stamp,
        ),
    )
    return int(
        conn.execute("SELECT id FROM chats WHERE wxid=?", (external_id,)).fetchone()[0]
    )


def message_source_key(chat_external_id: str, message: dict[str, Any]) -> str:
    provided = str(message.get("source_key") or "").strip()
    if provided:
        return provided
    local_id = message.get("local_id")
    timestamp = int(message.get("timestamp") or 0)
    if local_id is not None:
        return f"import:{chat_external_id}:{local_id}:{timestamp}"
    sender = str(message.get("sender_username") or message.get("sender") or "")
    content = str(message.get("content") or "")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]
    return f"import:{chat_external_id}:{timestamp}:{sender}:{digest}"


def upsert_message(
    conn: sqlite3.Connection,
    chat_id: int,
    chat: dict[str, Any],
    message: dict[str, Any],
) -> bool:
    chat_external_id = str(chat.get("username") or "")
    sender_external_id = str(message.get("sender_username") or "")
    sender_name = str(
        message.get("sender_contact_display") or message.get("sender") or ""
    )
    sender_person_id: int | None = None
    if sender_external_id:
        sender_person_id = upsert_person(
            conn,
            sender_external_id,
            sender_name or sender_external_id,
            raw={},
        )

    stamp = now_iso()
    source_key = message_source_key(chat_external_id, message)
    content = str(message.get("content") or "")
    message_type = str(message.get("type") or "unknown")
    existing = conn.execute(
        "SELECT id,content,sender_name,message_type FROM messages WHERE source_key=?",
        (source_key,),
    ).fetchone()
    raw_record_id = upsert_raw(conn, "message", source_key, message)
    display_content = readable_content(content, message_type)
    conn.execute(
        """
        INSERT INTO messages(source_key,chat_id,sender_person_id,sender_name,local_id,ts,message_type,content,display_content,raw_json,raw_record_id,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_key) DO UPDATE SET
          sender_person_id=COALESCE(excluded.sender_person_id,messages.sender_person_id),
          sender_name=CASE WHEN excluded.sender_name<>'' THEN excluded.sender_name ELSE messages.sender_name END,
          message_type=excluded.message_type,
          content=excluded.content,display_content=excluded.display_content,
          raw_json=excluded.raw_json,raw_record_id=excluded.raw_record_id,
          updated_at=excluded.updated_at
        """,
        (
            source_key,
            chat_id,
            sender_person_id,
            sender_name,
            message.get("local_id"),
            int(message.get("timestamp") or 0),
            message_type,
            content,
            display_content,
            json_text(message),
            raw_record_id,
            stamp,
            stamp,
        ),
    )
    message_id = int(
        conn.execute(
            "SELECT id FROM messages WHERE source_key=?", (source_key,)
        ).fetchone()[0]
    )
    search_changed = existing is None or (
        existing["content"] != content
        or (existing["sender_name"] or "") != sender_name
        or existing["message_type"] != message_type
    )
    if search_changed:
        conn.execute("DELETE FROM messages_fts WHERE rowid=?", (message_id,))
        conn.execute(
            "INSERT INTO messages_fts(rowid,message_id,content,sender_name,chat_name) VALUES(?,?,?,?,?)",
            (
                message_id,
                message_id,
                display_content,
                sender_name,
                str(chat.get("chat") or ""),
            ),
        )
    return existing is None
