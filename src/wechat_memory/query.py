from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .agent import run_codex
from .db import owner_name
from .semantic import search_semantic, semantic_index_current


SCHEMA = Path(__file__).with_name("schemas") / "query.schema.json"


def _tokens(question: str) -> list[str]:
    ascii_words = re.findall(r"[A-Za-z0-9_+.-]{2,}", question)
    chinese: list[str] = []
    stop = ["有哪些人", "哪些人", "有没有", "有哪些", "最近", "什么", "哪些", "怎么", "如何", "这个", "那个", "有人", "关于", "信息", "谁在", "谁", "提到过", "提到", "聊过", "在聊", "喜欢", "研究", "正在", "在做"]
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", question):
        cleaned = phrase
        for word in stop:
            cleaned = cleaned.replace(word, "")
        if len(cleaned) >= 2:
            chinese.append(cleaned)
    return list(dict.fromkeys([word for word in [*ascii_words, *chinese] if len(word) >= 2]))


def retrieve(conn: sqlite3.Connection, question: str, limit: int = 80) -> dict[str, list[dict[str, Any]]]:
    limit = max(1, min(int(limit), 200))
    tokens = _tokens(question)
    message_rows: list[sqlite3.Row] = []
    fact_rows: list[sqlite3.Row] = []
    named_people = conn.execute(
        """
        SELECT id,wxid,display_name FROM people
        WHERE length(display_name)>=2 AND instr(?,display_name)>0
        ORDER BY length(display_name) DESC LIMIT 5
        """,
        (question,),
    ).fetchall()
    if named_people:
        placeholders = ",".join("?" for _ in named_people)
        message_rows = conn.execute(
            f"""
            SELECT m.id,m.ts,m.message_type,m.display_content AS content,m.sender_name,c.name AS chat_name,c.chat_type,
                   p.id AS person_id,p.display_name AS person_name,0 AS rank
            FROM messages m JOIN chats c ON c.id=m.chat_id
            JOIN people p ON p.wxid=c.wxid AND c.chat_type='private'
            WHERE p.id IN ({placeholders}) ORDER BY m.ts DESC LIMIT ?
            """,
            (*[row["id"] for row in named_people], limit),
        ).fetchall()
        fact_rows = conn.execute(
            f"""
            SELECT f.id,f.category,f.value,f.confidence,f.evidence_message_id,
                   fe.evidence_payload_hash,fe.quote_text AS evidence_quote,
                   p.id AS person_id,p.display_name AS person_name,0 AS rank
            FROM analysis.profile_facts f JOIN people p ON p.id=f.person_id
            LEFT JOIN analysis.fact_evidence fe ON fe.fact_id=f.id AND fe.evidence_kind='message'
            WHERE f.status='active' AND p.id IN ({placeholders})
            ORDER BY f.confidence DESC LIMIT ?
            """,
            (*[row["id"] for row in named_people], limit),
        ).fetchall()
    if tokens:
        expression = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        try:
            fts_rows = conn.execute(
                """
                SELECT m.id,m.ts,m.message_type,m.display_content AS content,m.sender_name,c.name AS chat_name,c.chat_type,
                       p.id AS person_id,p.display_name AS person_name,bm25(messages_fts) AS rank
                FROM messages_fts
                JOIN messages m ON m.id=messages_fts.message_id
                JOIN chats c ON c.id=m.chat_id
                LEFT JOIN people p ON p.wxid=c.wxid AND c.chat_type='private'
                WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?
                """,
                (expression, limit),
            ).fetchall()
            if message_rows:
                seen = {row["id"] for row in message_rows}
                message_rows = [*message_rows, *[row for row in fts_rows if row["id"] not in seen]][:limit]
            else:
                message_rows = fts_rows
            fts_facts = conn.execute(
                """
                SELECT f.id,f.category,f.value,f.confidence,f.evidence_message_id,
                       fe.evidence_payload_hash,fe.quote_text AS evidence_quote,
                       p.id AS person_id,p.display_name AS person_name,bm25(profile_facts_fts) AS rank
                FROM analysis.profile_facts_fts
                JOIN analysis.profile_facts f ON f.id=profile_facts_fts.fact_id
                JOIN people p ON p.id=f.person_id
                LEFT JOIN analysis.fact_evidence fe ON fe.fact_id=f.id AND fe.evidence_kind='message'
                WHERE profile_facts_fts MATCH ? AND f.status='active' ORDER BY rank LIMIT ?
                """,
                (expression, limit),
            ).fetchall()
            fact_seen = {row["id"] for row in fact_rows}
            fact_rows = [*fact_rows, *[row for row in fts_facts if row["id"] not in fact_seen]][:limit]
        except sqlite3.OperationalError:
            pass
    if tokens or not message_rows:
        needles = tokens or [question]
        clauses = " OR ".join("m.display_content LIKE ?" for _ in needles)
        like_rows = conn.execute(
            f"""
            SELECT m.id,m.ts,m.message_type,m.display_content AS content,m.sender_name,c.name AS chat_name,c.chat_type,
                   p.id AS person_id,p.display_name AS person_name,0 AS rank
            FROM messages m JOIN chats c ON c.id=m.chat_id
            LEFT JOIN people p ON p.wxid=c.wxid AND c.chat_type='private'
            WHERE {clauses} ORDER BY m.ts DESC LIMIT ?
            """,
            (*[f"%{token}%" for token in needles], limit),
        ).fetchall()
        seen = {row["id"] for row in message_rows}
        message_rows = [*message_rows, *[row for row in like_rows if row["id"] not in seen]][:limit]
        fact_clauses = " OR ".join("f.value LIKE ?" for _ in needles)
        like_facts = conn.execute(
            f"""
            SELECT f.id,f.category,f.value,f.confidence,f.evidence_message_id,
                   fe.evidence_payload_hash,fe.quote_text AS evidence_quote,
                   p.id AS person_id,p.display_name AS person_name,0 AS rank
            FROM analysis.profile_facts f JOIN people p ON p.id=f.person_id
            LEFT JOIN analysis.fact_evidence fe ON fe.fact_id=f.id AND fe.evidence_kind='message'
            WHERE f.status='active' AND ({fact_clauses})
            ORDER BY f.confidence DESC LIMIT ?
            """,
            (*[f"%{token}%" for token in needles], limit),
        ).fetchall()
        fact_seen = {row["id"] for row in fact_rows}
        fact_rows = [*fact_rows, *[row for row in like_facts if row["id"] not in fact_seen]][:limit]
    fact_message_ids = {int(row["evidence_message_id"]) for row in fact_rows}
    present_message_ids = {int(row["id"]) for row in message_rows}
    missing_ids = sorted(fact_message_ids - present_message_ids)
    if missing_ids:
        placeholders = ",".join("?" for _ in missing_ids)
        evidence_rows = conn.execute(
            f"""
            SELECT m.id,m.ts,m.message_type,m.display_content AS content,m.sender_name,c.name AS chat_name,c.chat_type,
                   p.id AS person_id,p.display_name AS person_name,0 AS rank
            FROM messages m JOIN chats c ON c.id=m.chat_id
            LEFT JOIN people p ON p.wxid=c.wxid AND c.chat_type='private'
            WHERE m.id IN ({placeholders})
            """,
            missing_ids,
        ).fetchall()
        message_rows = [*message_rows, *evidence_rows]
    return {"messages": [dict(row) for row in message_rows], "facts": [dict(row) for row in fact_rows]}


def _semantic_messages(
    conn: sqlite3.Connection,
    hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    message_ids = [int(hit["message_id"]) for hit in hits]
    if not message_ids:
        return []
    placeholders = ",".join("?" for _ in message_ids)
    rows = conn.execute(
        f"""
        SELECT m.id,m.ts,m.message_type,m.display_content AS content,m.sender_name,
               c.name AS chat_name,c.chat_type,p.id AS person_id,p.display_name AS person_name
        FROM messages m JOIN chats c ON c.id=m.chat_id
        LEFT JOIN people p ON p.wxid=c.wxid AND c.chat_type='private'
        WHERE m.id IN ({placeholders})
        """,
        message_ids,
    ).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    result: list[dict[str, Any]] = []
    for hit in hits:
        message_id = int(hit["message_id"])
        if message_id not in by_id:
            continue
        item = by_id[message_id]
        item["semantic_score"] = float(hit.get("score") or 0)
        item["vector_rank"] = int(hit.get("rank") or len(result) + 1)
        result.append(item)
    return result


def _merge_messages(
    lexical: list[dict[str, Any]],
    semantic: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    scores: dict[int, float] = {}
    backends: dict[int, set[str]] = {}
    for backend, rows in (("sql-fts", lexical), ("qmd-vector", semantic)):
        for rank, row in enumerate(rows, start=1):
            message_id = int(row["id"])
            if message_id not in merged:
                merged[message_id] = dict(row)
            else:
                merged[message_id].update(
                    {key: value for key, value in row.items() if value is not None}
                )
            scores[message_id] = scores.get(message_id, 0) + 1 / (60 + rank)
            backends.setdefault(message_id, set()).add(backend)
    ordered = sorted(
        merged,
        key=lambda message_id: (-scores[message_id], -int(merged[message_id]["ts"])),
    )[: max(1, min(int(limit), 200))]
    result: list[dict[str, Any]] = []
    for message_id in ordered:
        item = merged[message_id]
        item["retrieval_backends"] = sorted(backends[message_id])
        item["fusion_score"] = scores[message_id]
        result.append(item)
    return result


def retrieve_hybrid(
    conn: sqlite3.Connection,
    question: str,
    limit: int = 80,
    *,
    mode: str = "auto",
    semantic_search=search_semantic,
) -> dict[str, Any]:
    if mode not in {"auto", "exact", "semantic", "hybrid"}:
        raise ValueError(f"未知查询模式：{mode}")
    limit = max(1, min(int(limit), 200))
    lexical = retrieve(conn, question, limit) if mode != "semantic" else {"messages": [], "facts": []}
    semantic_response = {"available": False, "hits": [], "warning": ""}
    if mode in {"auto", "semantic", "hybrid"}:
        if semantic_search is search_semantic and not semantic_index_current(conn):
            semantic_response = {
                "available": False,
                "hits": [],
                "warning": "微信向量索引未完成或已过期；运行 wechat-memory index 后恢复。",
            }
        else:
            semantic_response = semantic_search(question, limit=max(limit, 40))
    if mode == "semantic" and not semantic_response.get("available"):
        lexical = retrieve(conn, question, limit)
    semantic_rows = _semantic_messages(conn, list(semantic_response.get("hits") or []))
    messages = _merge_messages(lexical["messages"], semantic_rows, limit)
    present_ids = {int(row["id"]) for row in messages}
    lexical_by_id = {int(row["id"]): row for row in lexical["messages"]}
    for fact in lexical["facts"]:
        evidence_id = int(fact["evidence_message_id"])
        if evidence_id in present_ids or evidence_id not in lexical_by_id:
            continue
        evidence_row = dict(lexical_by_id[evidence_id])
        evidence_row["retrieval_backends"] = ["profile-evidence"]
        evidence_row["fusion_score"] = 0.0
        messages.append(evidence_row)
        present_ids.add(evidence_id)
    backends: list[str] = []
    if lexical["messages"] or lexical["facts"]:
        backends.append("sql-fts")
    if semantic_rows:
        backends.append("qmd-vector")
    warning = str(semantic_response.get("warning") or "")
    return {
        "messages": messages,
        "facts": lexical["facts"],
        "retrieval": {
            "requested_mode": mode,
            "backends": backends,
            "degraded": bool(warning),
            "warning": warning,
            "messages": len(messages),
            "facts": len(lexical["facts"]),
        },
    }


def answer(
    conn: sqlite3.Connection,
    question: str,
    *,
    limit: int = 80,
    mode: str = "auto",
) -> dict[str, Any]:
    evidence = retrieve_hybrid(conn, question, limit, mode=mode)
    owner = owner_name(conn)
    prompt = f"""你在回答资料所有者对个人微信人物记忆库的查询。

问题：{question}

规则：
1. 只根据 evidence 回答；不知道就明确说不知道。
2. 原消息是主要证据；画像事实只是可选的高层派生信息。没有画像也必须正常回答。
3. 重点回答人物是谁、做什么、聊过什么、哪些人匹配条件。
4. 每个重要判断引用 message_id。不得捏造 ID。
5. sender_name={owner} 表示资料所有者本人发言；绝不能把资料所有者说的话归因给聊天对象。人物观点只依据该人物作为 sender_name 的消息。
6. 语音、图片、表情若没有转写文本，只能说明存在该类型消息，不能推断其内容。
7. people 只列真正相关人物；群聊中身份不明确时用 chat_name/sender_name 并说明。
8. 不给跟进建议、不做关系评分。

evidence：
{json.dumps(evidence, ensure_ascii=False)}
"""
    result = run_codex(prompt, SCHEMA)
    valid_ids = {row["id"] for row in evidence["messages"]}
    result["evidence_message_ids"] = [
        int(item) for item in result.get("evidence_message_ids", []) if int(item) in valid_ids
    ]
    result["retrieval"] = evidence["retrieval"]
    return result


def evidence(conn: sqlite3.Connection, message_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT m.id,m.ts,m.message_type,m.display_content AS content,m.sender_name,c.name AS chat_name,c.chat_type,
               r.payload_hash AS current_payload_hash
        FROM messages m JOIN chats c ON c.id=m.chat_id
        LEFT JOIN raw_records r ON r.id=m.raw_record_id WHERE m.id=?
        """,
        (message_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["fact_snapshots"] = [
        dict(item)
        for item in conn.execute(
            """
            SELECT f.id AS fact_id,p.display_name AS person_name,f.value,
                   fe.evidence_payload_hash,fe.quote_text,
                   (fe.evidence_payload_hash=r.payload_hash) AS matches_current_payload
            FROM analysis.fact_evidence fe
            JOIN analysis.profile_facts f ON f.id=fe.fact_id
            JOIN people p ON p.id=f.person_id
            JOIN messages m ON m.id=fe.evidence_id
            LEFT JOIN raw_records r ON r.id=m.raw_record_id
            WHERE fe.evidence_kind='message' AND fe.evidence_id=?
            """,
            (message_id,),
        )
    ]
    result["summary_snapshots"] = [
        dict(item)
        for item in conn.execute(
            """
            SELECT p.display_name AS person_name,se.evidence_payload_hash,se.quote_text,
                   (se.evidence_payload_hash=r.payload_hash) AS matches_current_payload
            FROM analysis.profile_summary_evidence se
            JOIN people p ON p.id=se.person_id
            JOIN messages m ON m.id=se.message_id
            LEFT JOIN raw_records r ON r.id=m.raw_record_id
            WHERE se.message_id=?
            """,
            (message_id,),
        )
    ]
    return result
