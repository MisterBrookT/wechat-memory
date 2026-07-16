from __future__ import annotations

import sqlite3
from typing import Any

from .db import json_text, now_iso


def _add_role(
    conn: sqlite3.Connection,
    person_id: int,
    role: str,
    source: str,
    confidence: float,
    evidence: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO person_roles(person_id,role,source,confidence,evidence_json,updated_at)
        VALUES(?,?,?,?,?,?)
        """,
        (person_id, role, source, confidence, json_text(evidence), now_iso()),
    )


def refresh_person_roles(conn: sqlite3.Connection, *, self_wxid: str | None = None) -> dict[str, int]:
    """Rebuild deterministic, overlapping roles from the normalized public schema."""
    conn.execute("DELETE FROM person_roles")
    people = conn.execute("SELECT id,wxid FROM people").fetchall()
    private_wxids = {
        str(row[0])
        for row in conn.execute("SELECT wxid FROM chats WHERE chat_type='private'")
    }
    official_wxids = {
        str(row[0])
        for row in conn.execute(
            "SELECT wxid FROM chats WHERE chat_type IN ('official_account','service','official')"
        )
    }
    member_ids = {int(row[0]) for row in conn.execute("SELECT DISTINCT person_id FROM chat_members")}

    for row in people:
        person_id = int(row["id"])
        wxid = str(row["wxid"])
        is_self = bool(self_wxid and wxid == self_wxid)
        is_official = wxid in official_wxids

        if is_self:
            _add_role(conn, person_id, "self", "structured-import", 1.0, {"identity": wxid})
        if is_official:
            _add_role(
                conn,
                person_id,
                "official_or_service",
                "chat-type",
                1.0,
                {"chat_type": "official_account"},
            )
        if wxid in private_wxids and not is_self and not is_official:
            _add_role(
                conn,
                person_id,
                "private_chat_peer",
                "session",
                1.0,
                {"chat_type": "private"},
            )
        if person_id in member_ids:
            _add_role(
                conn,
                person_id,
                "group_member_seen",
                "chat-members",
                1.0,
                {"meaning": "曾在本地群成员记录中观测到"},
            )
        if not conn.execute("SELECT 1 FROM person_roles WHERE person_id=?", (person_id,)).fetchone():
            _add_role(conn, person_id, "unknown", "fallback", 0.5, {})

    conn.commit()
    return role_stats(conn)


def role_stats(conn: sqlite3.Connection) -> dict[str, int]:
    result = {"known_identities": int(conn.execute("SELECT count(*) FROM people").fetchone()[0])}
    result.update(
        {
            str(row["role"]): int(row["count"])
            for row in conn.execute(
                "SELECT role,count(DISTINCT person_id) AS count FROM person_roles GROUP BY role"
            )
        }
    )
    return result


def person_roles(conn: sqlite3.Connection, person_id: int) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT role FROM person_roles WHERE person_id=? ORDER BY role", (person_id,)
        )
    ]
