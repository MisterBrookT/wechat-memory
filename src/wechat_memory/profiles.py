from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from .agent import run_codex
from .classify import person_roles
from .db import now_iso, owner_name


SCHEMA = Path(__file__).with_name("schemas") / "profile.schema.json"


def find_person(conn: sqlite3.Connection, query: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM people
        WHERE wxid=? OR display_name=? OR remark=? OR nickname=? OR alias=?
        ORDER BY CASE WHEN display_name=? THEN 0 ELSE 1 END, updated_at DESC LIMIT 1
        """,
        (query, query, query, query, query, query),
    ).fetchone()


def top_people(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.*,count(m.id) AS message_count,max(m.ts) AS last_ts
        FROM people p
        JOIN chats c ON c.wxid=p.wxid AND c.chat_type='private'
        JOIN messages m ON m.chat_id=c.id
        WHERE m.display_content<>'' AND EXISTS (
          SELECT 1 FROM person_roles pr WHERE pr.person_id=p.id
          AND pr.role='private_chat_peer'
        )
        GROUP BY p.id
        ORDER BY message_count DESC,last_ts DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _evidence_rows(conn: sqlite3.Connection, person_id: int, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT m.id,m.ts,m.message_type,m.display_content AS content,c.name AS chat_name,m.sender_name
        FROM messages m
        JOIN chats c ON c.id=m.chat_id
        JOIN people p ON p.id=?
        WHERE (c.chat_type='private' AND c.wxid=p.wxid) OR m.sender_person_id=p.id
        ORDER BY m.ts DESC LIMIT ?
        """,
        (person_id, limit),
    ).fetchall()


def build_profile(
    conn: sqlite3.Connection,
    person: sqlite3.Row,
    *,
    message_limit: int = 600,
    model: str | None = None,
) -> dict[str, Any]:
    selected = model or os.environ.get("WECHAT_MEMORY_MODEL", "gpt-5.3-codex-spark")
    run_id = conn.execute(
        "INSERT INTO analysis.profile_runs(person_id,model,started_at,status) VALUES(?,?,?,?)",
        (person["id"], selected, now_iso(), "running"),
    ).lastrowid
    conn.commit()
    rows = _evidence_rows(conn, int(person["id"]), message_limit)
    evidence = [
        {
            "message_id": row["id"],
            "time": row["ts"],
            "type": row["message_type"],
            "chat": row["chat_name"],
            "sender": row["sender_name"],
            "content": row["content"][:2000],
        }
        for row in reversed(rows)
        if row["content"] and row["message_type"] == "文本"
    ]
    while len(json.dumps(evidence, ensure_ascii=False)) > 60_000 and len(evidence) > 40:
        evidence = evidence[len(evidence) // 10 :]
    owner = owner_name(conn)
    prompt = f"""你在构建个人微信人物记忆库。只根据证据生成画像，不猜测。

资料所有者：{owner}
目标人物：{person['display_name']}
微信字段：{json.dumps({k: person[k] for k in ('remark','nickname','alias')}, ensure_ascii=False)}

要求：
1. summary 100-250 字，主语必须是目标人物“{person['display_name']}”，不能把目标人物写成资料所有者“{owner}”；并用 summary_evidence_message_ids 列出支撑摘要的 3-10 条真实消息 ID。
2. facts 拆成最小事实。category 只能用 identity/company/role/location/project/expertise/interest/opinion/status/relationship/event。
3. 每条事实只引用一条真实 message_id；value 中的引号原话必须逐字存在于这条消息，不能把相邻消息拼成同一事实；证据不足则不写。
4. sender={owner} 表示资料所有者发言；sender={person['display_name']} 表示目标人物发言。人物事实优先依据目标人物自己的发言；若依据资料所有者发言，value 必须明确归因。
5. confidence 0-1。区分目标人物明确陈述、资料所有者陈述、Agent 推断；推断置信度不得高于 0.7。
6. 不输出行动建议、提醒、关系评分。

证据 JSON：
{json.dumps(evidence, ensure_ascii=False)}
"""
    try:
        if not evidence:
            raise ValueError("没有可用于画像的文本证据")
        result = run_codex(prompt, SCHEMA, model=selected)
        valid_ids = {int(item["message_id"]): int(item["time"]) for item in evidence}
        summary = str(result["summary"]).strip()
        content_by_id = {int(item["message_id"]): str(item["content"]) for item in evidence}
        summary_evidence_ids = list(
            dict.fromkeys(
                int(item)
                for item in result["summary_evidence_message_ids"]
                if int(item) in valid_ids
            )
        )
        if not summary_evidence_ids:
            raise ValueError("画像摘要没有有效的原消息证据")
        prepared_facts: list[tuple[str, str, float, int, int]] = []
        for fact in result.get("facts", []):
            message_id = int(fact["evidence_message_id"])
            if message_id not in valid_ids:
                continue
            value = str(fact["value"]).strip()
            quoted = [item.strip() for item in re.findall(r"[“\"]([^”\"]+)[”\"]", value)]
            if any(len(item) >= 2 and item not in content_by_id[message_id] for item in quoted):
                continue
            prepared_facts.append(
                (
                    str(fact["category"]),
                    value,
                    float(fact["confidence"]),
                    message_id,
                    valid_ids[message_id],
                )
            )

        facts_written = 0
        conn.execute("SAVEPOINT profile_replace")
        try:
            old_ids = [row[0] for row in conn.execute("SELECT id FROM analysis.profile_facts WHERE person_id=? AND user_corrected=0", (person["id"],))]
            for old_id in old_ids:
                conn.execute("DELETE FROM analysis.profile_facts_fts WHERE rowid=?", (old_id,))
            conn.execute("DELETE FROM analysis.profile_facts WHERE person_id=? AND user_corrected=0", (person["id"],))
            conn.execute(
                """
                INSERT INTO analysis.profile_summaries(person_id,summary,model,source_run_id,generated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(person_id) DO UPDATE SET
                  summary=excluded.summary,model=excluded.model,source_run_id=excluded.source_run_id,
                  generated_at=excluded.generated_at
                """,
                (person["id"], summary, selected, run_id, now_iso()),
            )
            conn.execute("DELETE FROM analysis.profile_summary_evidence WHERE person_id=?", (person["id"],))
            for message_id in summary_evidence_ids:
                snapshot = conn.execute(
                    """
                    SELECT r.payload_hash,m.display_content AS content FROM messages m
                    JOIN raw_records r ON r.id=m.raw_record_id WHERE m.id=?
                    """,
                    (message_id,),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO analysis.profile_summary_evidence(person_id,message_id,evidence_payload_hash,quote_text)
                    VALUES(?,?,?,?)
                    """,
                    (person["id"], message_id, snapshot["payload_hash"], snapshot["content"][:2000]),
                )
            for category, value, confidence, message_id, message_ts in prepared_facts:
                stamp = now_iso()
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO analysis.profile_facts(
                      person_id,category,value,confidence,evidence_message_id,first_seen_ts,last_seen_ts,
                      source_run_id,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        person["id"], category, value, confidence, message_id,
                        message_ts, message_ts, run_id, stamp, stamp,
                    ),
                )
                fact_id = cursor.lastrowid
                if cursor.rowcount and fact_id:
                    conn.execute(
                        "INSERT INTO analysis.profile_facts_fts(rowid,fact_id,person_name,category,value) VALUES(?,?,?,?,?)",
                        (fact_id, fact_id, person["display_name"], category, value),
                    )
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO analysis.fact_evidence(
                          fact_id,evidence_kind,evidence_id,evidence_payload_hash,quote_text
                        )
                        SELECT ?, 'message', m.id, r.payload_hash, substr(m.display_content,1,2000)
                        FROM messages m JOIN raw_records r ON r.id=m.raw_record_id WHERE m.id=?
                        """,
                        (fact_id, message_id),
                    )
                    facts_written += 1
            conn.execute("RELEASE SAVEPOINT profile_replace")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT profile_replace")
            conn.execute("RELEASE SAVEPOINT profile_replace")
            raise
        conn.execute(
            "UPDATE analysis.profile_runs SET finished_at=?,status='ok' WHERE id=?", (now_iso(), run_id)
        )
        conn.commit()
        return {
            "person_id": person["id"],
            "person": person["display_name"],
            "summary": summary,
            "summary_evidence_message_ids": summary_evidence_ids,
            "facts_written": facts_written,
            "evidence_messages": len(evidence),
            "model": selected,
        }
    except Exception as exc:
        conn.execute(
            "UPDATE analysis.profile_runs SET finished_at=?,status='failed',error=? WHERE id=?",
            (now_iso(), str(exc)[:4000], run_id),
        )
        conn.commit()
        raise


def person_view(conn: sqlite3.Connection, person: sqlite3.Row) -> dict[str, Any]:
    summary = conn.execute(
        "SELECT summary,model,generated_at FROM analysis.profile_summaries WHERE person_id=?", (person["id"],)
    ).fetchone()
    summary_evidence = [
        dict(row)
        for row in conn.execute(
            """
            SELECT message_id,evidence_payload_hash,quote_text
            FROM analysis.profile_summary_evidence WHERE person_id=? ORDER BY message_id
            """,
            (person["id"],),
        )
    ]
    facts = conn.execute(
        """
        SELECT f.id,f.category,f.value,f.confidence,f.evidence_message_id,m.ts,
               fe.evidence_payload_hash,fe.quote_text
        FROM analysis.profile_facts f JOIN messages m ON m.id=f.evidence_message_id
        LEFT JOIN analysis.fact_evidence fe ON fe.fact_id=f.id AND fe.evidence_kind='message'
        WHERE f.person_id=? AND f.status='active'
        ORDER BY f.category,f.confidence DESC
        """,
        (person["id"],),
    ).fetchall()
    return {
        "person": {
            **{key: person[key] for key in ("id", "wxid", "display_name", "remark", "nickname", "alias")},
            "roles": person_roles(conn, int(person["id"])),
        },
        "profile": (
            {
                **dict(summary),
                "evidence_message_ids": [item["message_id"] for item in summary_evidence],
                "evidence": summary_evidence,
            }
            if summary
            else None
        ),
        "facts": [dict(row) for row in facts],
    }
