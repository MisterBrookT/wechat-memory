from __future__ import annotations

import sqlite3
from typing import Any

from .classify import role_stats
from .db import owner_name
from .profiles import person_view


def overview(conn: sqlite3.Connection) -> dict[str, Any]:
    last_import = conn.execute(
        "SELECT value AS namespace,updated_at FROM meta WHERE key='last_structured_import'"
    ).fetchone()
    message_range = conn.execute(
        "SELECT min(ts) AS first_ts,max(ts) AS last_ts FROM messages"
    ).fetchone()
    return {
        "identities": role_stats(conn),
        "chats": int(conn.execute("SELECT count(*) FROM chats").fetchone()[0]),
        "messages": int(conn.execute("SELECT count(*) FROM messages").fetchone()[0]),
        "profiles": int(
            conn.execute("SELECT count(*) FROM analysis.profile_summaries").fetchone()[0]
        ),
        "facts": int(
            conn.execute(
                "SELECT count(*) FROM analysis.profile_facts WHERE status='active'"
            ).fetchone()[0]
        ),
        "message_range": dict(message_range),
        "last_import": dict(last_import) if last_import else None,
    }


def interaction_graph(
    conn: sqlite3.Connection,
    *,
    days: int = 90,
    people_limit: int = 80,
    group_limit: int = 12,
) -> dict[str, Any]:
    """Build an observed-interaction graph, not an inferred closeness graph."""
    days = max(0, min(int(days), 3650))
    people_limit = max(10, min(int(people_limit), 160))
    group_limit = max(0, min(int(group_limit), 30))
    latest_ts = int(conn.execute("SELECT coalesce(max(ts),0) FROM messages").fetchone()[0])
    cutoff = max(0, latest_ts - days * 86400) if days else 0

    self_row = conn.execute(
        """
        SELECT p.id,p.display_name FROM people p
        JOIN person_roles r ON r.person_id=p.id AND r.role='self'
        ORDER BY r.confidence DESC LIMIT 1
        """
    ).fetchone()
    self_person_id = int(self_row["id"]) if self_row else None
    self_name = str(self_row["display_name"]) if self_row else owner_name(conn)
    nodes: list[dict[str, Any]] = [
        {
            "id": "self",
            "kind": "self",
            "person_id": self_person_id,
            "label": self_name,
            "message_count": 0,
            "last_ts": latest_ts or None,
            "roles": ["self"],
        }
    ]
    edges: list[dict[str, Any]] = []
    person_node_ids: set[int] = set()

    private_rows = conn.execute(
        """
        WITH roles AS (
          SELECT person_id,group_concat(DISTINCT role) AS roles
          FROM person_roles GROUP BY person_id
        )
        SELECT p.id,p.display_name,p.remark,p.nickname,
               coalesce(r.roles,'') AS roles,count(m.id) AS message_count,max(m.ts) AS last_ts,
               ps.generated_at AS profile_generated_at
        FROM people p
        JOIN chats c ON c.wxid=p.wxid AND c.chat_type='private'
        JOIN messages m ON m.chat_id=c.id
        LEFT JOIN roles r ON r.person_id=p.id
        LEFT JOIN analysis.profile_summaries ps ON ps.person_id=p.id
        WHERE m.ts>=?
        GROUP BY p.id
        ORDER BY message_count DESC,last_ts DESC
        LIMIT ?
        """,
        (cutoff, people_limit),
    ).fetchall()
    for row in private_rows:
        person_id = int(row["id"])
        person_node_ids.add(person_id)
        count = int(row["message_count"])
        nodes.append(
            {
                "id": f"person:{person_id}",
                "kind": "person",
                "person_id": person_id,
                "label": str(row["display_name"]),
                "remark": row["remark"],
                "nickname": row["nickname"],
                "roles": str(row["roles"] or "").split(",") if row["roles"] else [],
                "message_count": count,
                "last_ts": row["last_ts"],
                "profile_ready": bool(row["profile_generated_at"]),
            }
        )
        edges.append(
            {
                "id": f"private:{person_id}",
                "source": "self",
                "target": f"person:{person_id}",
                "kind": "private",
                "weight": count,
            }
        )

    group_rows: list[sqlite3.Row] = []
    if group_limit:
        group_rows = conn.execute(
            """
            SELECT c.id,c.name,count(m.id) AS message_count,max(m.ts) AS last_ts
            FROM chats c JOIN messages m ON m.chat_id=c.id
            WHERE c.chat_type='group' AND m.ts>=?
            GROUP BY c.id ORDER BY message_count DESC,last_ts DESC LIMIT ?
            """,
            (cutoff, group_limit),
        ).fetchall()
    for row in group_rows:
        group_id = int(row["id"])
        count = int(row["message_count"])
        nodes.append(
            {
                "id": f"group:{group_id}",
                "kind": "group",
                "chat_id": group_id,
                "label": str(row["name"]),
                "message_count": count,
                "last_ts": row["last_ts"],
                "roles": [],
            }
        )
        edges.append(
            {
                "id": f"group-self:{group_id}",
                "source": "self",
                "target": f"group:{group_id}",
                "kind": "group_context",
                "weight": count,
            }
        )

    if group_rows:
        group_ids = [int(row["id"]) for row in group_rows]
        placeholders = ",".join("?" for _ in group_ids)
        group_people = conn.execute(
            f"""
            WITH roles AS (
              SELECT person_id,group_concat(DISTINCT role) AS roles
              FROM person_roles GROUP BY person_id
            )
            SELECT c.id AS group_id,p.id,p.display_name,p.remark,p.nickname,
                   coalesce(r.roles,'') AS roles,count(m.id) AS message_count,max(m.ts) AS last_ts,
                   ps.generated_at AS profile_generated_at
            FROM messages m
            JOIN chats c ON c.id=m.chat_id
            JOIN people p ON p.id=m.sender_person_id
            LEFT JOIN roles r ON r.person_id=p.id
            LEFT JOIN analysis.profile_summaries ps ON ps.person_id=p.id
            WHERE c.id IN ({placeholders}) AND m.ts>=?
              AND instr(',' || coalesce(r.roles,'') || ',',',self,')=0
            GROUP BY c.id,p.id
            ORDER BY message_count DESC,last_ts DESC
            LIMIT ?
            """,
            (*group_ids, cutoff, max(20, people_limit)),
        ).fetchall()
        for row in group_people:
            person_id = int(row["id"])
            if person_id not in person_node_ids:
                person_node_ids.add(person_id)
                nodes.append(
                    {
                        "id": f"person:{person_id}",
                        "kind": "person",
                        "person_id": person_id,
                        "label": str(row["display_name"]),
                        "remark": row["remark"],
                        "nickname": row["nickname"],
                        "roles": str(row["roles"] or "").split(",") if row["roles"] else [],
                        "message_count": int(row["message_count"]),
                        "last_ts": row["last_ts"],
                        "profile_ready": bool(row["profile_generated_at"]),
                    }
                )
            edges.append(
                {
                    "id": f"group-person:{row['group_id']}:{person_id}",
                    "source": f"group:{int(row['group_id'])}",
                    "target": f"person:{person_id}",
                    "kind": "group_message",
                    "weight": int(row["message_count"]),
                }
            )

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "days": days,
            "cutoff": cutoff or None,
            "latest_ts": latest_ts or None,
            "people": sum(node["kind"] == "person" for node in nodes),
            "groups": len(group_rows),
            "edges": len(edges),
            "meaning": "线表示本地可见的私聊或群内发言，不是亲密度或真实社交关系推断。",
        },
    }


def list_people(
    conn: sqlite3.Connection,
    *,
    role: str = "private_chat_peer",
    query: str = "",
    limit: int = 80,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if role != "all":
        clauses.append("pr.role=?")
        params.append(role)
    if query.strip():
        needle = f"%{query.strip()}%"
        clauses.append(
            "(p.display_name LIKE ? OR p.remark LIKE ? OR p.nickname LIKE ? OR p.alias LIKE ?)"
        )
        params.extend([needle, needle, needle, needle])
    params.extend([max(1, min(limit, 200)), max(0, offset)])
    rows = conn.execute(
        f"""
        WITH person_messages AS (
          SELECT p.id AS person_id,m.id AS message_id,m.ts
          FROM people p JOIN chats c ON c.wxid=p.wxid AND c.chat_type='private'
          JOIN messages m ON m.chat_id=c.id
          UNION
          SELECT m.sender_person_id,m.id,m.ts FROM messages m WHERE m.sender_person_id IS NOT NULL
        ), message_stats AS (
          SELECT person_id,count(*) AS message_count,max(ts) AS last_ts
          FROM person_messages GROUP BY person_id
        )
        SELECT p.id,p.display_name,p.remark,p.nickname,p.alias,p.avatar_url,
               group_concat(DISTINCT all_roles.role) AS roles,
               ps.generated_at AS profile_generated_at,
               coalesce(ms.message_count,0) AS message_count,ms.last_ts
        FROM people p
        JOIN person_roles pr ON pr.person_id=p.id
        LEFT JOIN person_roles all_roles ON all_roles.person_id=p.id
        LEFT JOIN analysis.profile_summaries ps ON ps.person_id=p.id
        LEFT JOIN message_stats ms ON ms.person_id=p.id
        WHERE {' AND '.join(clauses) if clauses else '1=1'}
        GROUP BY p.id
        ORDER BY (last_ts IS NULL),last_ts DESC,p.display_name
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()
    return [
        {
            **dict(row),
            "roles": str(row["roles"] or "").split(",") if row["roles"] else [],
        }
        for row in rows
    ]


def person_detail(conn: sqlite3.Connection, person_id: int) -> dict[str, Any] | None:
    person = conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
    if person is None:
        return None
    result = person_view(conn, person)
    result["timeline"] = timeline(conn, person_id, limit=80)
    return result


def timeline(conn: sqlite3.Connection, person_id: int, *, limit: int = 80) -> list[dict[str, Any]]:
    person = conn.execute("SELECT wxid FROM people WHERE id=?", (person_id,)).fetchone()
    if person is None:
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT m.id AS message_id,m.ts,m.message_type,m.display_content AS content,m.sender_name,
                   c.name AS chat_name,c.chat_type
            FROM messages m JOIN chats c ON c.id=m.chat_id
            WHERE (c.chat_type='private' AND c.wxid=?) OR m.sender_person_id=?
            ORDER BY m.ts DESC LIMIT ?
            """,
            (person["wxid"], person_id, max(1, min(limit, 300))),
        )
    ]
