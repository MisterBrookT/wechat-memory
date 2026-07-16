from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .classify import refresh_person_roles
from .db import json_text, now_iso
from .store import upsert_chat, upsert_message, upsert_person


TYPE_NAMES = {
    "text": "文本",
    "image": "图片",
    "voice": "语音",
    "video": "视频",
    "file": "链接/文件",
    "link": "链接/文件",
    "location": "位置",
    "system": "系统",
}


def _required(item: dict[str, Any], key: str, context: str) -> Any:
    value = item.get(key)
    if value is None or value == "":
        raise ValueError(f"{context} 缺少 {key}")
    return value


def _safe_namespace(value: Any) -> str:
    namespace = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "default")).strip("-.")
    return namespace[:80] or "default"


def _identity(namespace: str, external_id: Any) -> str:
    return f"import:{namespace}:identity:{external_id}"


def _timestamp(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError as exc:
        raise ValueError(f"无法解析 timestamp：{text}") from exc


def import_payload(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, int | str]:
    """Import user-provided, already accessible structured data. No extraction occurs here."""
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("仅支持 schema_version=1")
    namespace = _safe_namespace(payload.get("namespace"))
    people = payload.get("people") or []
    chats = payload.get("chats") or []
    messages = payload.get("messages") or []
    if not all(isinstance(items, list) for items in (people, chats, messages)):
        raise ValueError("people、chats、messages 必须是数组")

    people_by_external: dict[str, str] = {}
    people_written = 0
    owner = payload.get("owner")
    owner_wxid: str | None = None
    if isinstance(owner, dict):
        owner_external = str(_required(owner, "id", "owner"))
        owner_wxid = _identity(namespace, owner_external)
        upsert_person(
            conn,
            owner_wxid,
            str(owner.get("display_name") or "我"),
            source="structured-import",
        )
        people_by_external[owner_external] = owner_wxid
        people_written += 1

    for index, item in enumerate(people):
        if not isinstance(item, dict):
            raise ValueError(f"people[{index}] 必须是对象")
        external_id = str(_required(item, "id", f"people[{index}]"))
        wxid = _identity(namespace, external_id)
        people_by_external[external_id] = wxid
        upsert_person(
            conn,
            wxid,
            str(item.get("display_name") or external_id),
            source="structured-import",
            remark=item.get("remark") or None,
            nickname=item.get("nickname") or None,
            alias=item.get("alias") or None,
        )
        people_written += 1

    chats_by_external: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, item in enumerate(chats):
        if not isinstance(item, dict):
            raise ValueError(f"chats[{index}] 必须是对象")
        external_id = str(_required(item, "id", f"chats[{index}]"))
        chat_type = str(item.get("type") or "private")
        if chat_type not in {"private", "group", "official_account", "other"}:
            raise ValueError(f"chats[{index}].type 不受支持：{chat_type}")
        peer_id = str(item.get("peer_id") or external_id)
        chat_identity = (
            people_by_external.get(peer_id, _identity(namespace, peer_id))
            if chat_type == "private"
            else f"import:{namespace}:chat:{external_id}"
        )
        chat = {
            "username": chat_identity,
            "chat": str(item.get("name") or external_id),
            "chat_type": chat_type,
            "timestamp": item.get("last_timestamp"),
            "source": "structured-import",
        }
        chat_id = upsert_chat(conn, chat)
        chats_by_external[external_id] = (chat_id, chat)

    messages_written = 0
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            raise ValueError(f"messages[{index}] 必须是对象")
        external_id = str(_required(item, "id", f"messages[{index}]"))
        chat_external = str(_required(item, "chat_id", f"messages[{index}]"))
        if chat_external not in chats_by_external:
            raise ValueError(f"messages[{index}] 引用未知 chat_id：{chat_external}")
        chat_id, chat = chats_by_external[chat_external]
        sender_external = str(item.get("sender_id") or "")
        sender_wxid = people_by_external.get(sender_external, "")
        message_type = str(item.get("type") or "text")
        message = {
            "source_key": f"import:{namespace}:message:{external_id}",
            "timestamp": _timestamp(_required(item, "timestamp", f"messages[{index}]")),
            "type": TYPE_NAMES.get(message_type, message_type),
            "content": str(item.get("content") or ""),
            "sender_username": sender_wxid,
            "sender_contact_display": str(item.get("sender_name") or ""),
            "source": "structured-import",
        }
        if upsert_message(conn, chat_id, chat, message):
            messages_written += 1
        if chat["chat_type"] == "group" and sender_wxid:
            sender_row = conn.execute(
                "SELECT id FROM people WHERE wxid=?", (sender_wxid,)
            ).fetchone()
            if sender_row:
                conn.execute(
                    """
                    INSERT INTO chat_members(chat_id,person_id,raw_json,updated_at)
                    VALUES(?,?,?,?) ON CONFLICT(chat_id,person_id) DO UPDATE SET
                      raw_json=excluded.raw_json,updated_at=excluded.updated_at
                    """,
                    (chat_id, int(sender_row[0]), json_text({"source": "observed-message"}), now_iso()),
                )

    conn.commit()
    roles = refresh_person_roles(conn, self_wxid=owner_wxid)
    conn.execute(
        """
        INSERT INTO meta(key,value,updated_at) VALUES('last_structured_import',?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
        """,
        (namespace, now_iso()),
    )
    conn.commit()
    return {
        "namespace": namespace,
        "people": people_written,
        "chats": len(chats_by_external),
        "messages_seen": len(messages),
        "messages_written": messages_written,
    }


def import_json(conn: sqlite3.Connection, path: Path) -> dict[str, int | str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 无效：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("导入文件根节点必须是对象")
    return import_payload(conn, payload)
