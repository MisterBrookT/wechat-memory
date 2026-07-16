from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from .classify import refresh_person_roles, role_stats
from .db import analysis_db_path, database, db_path, readonly_database
from .importer import import_json
from .profiles import build_profile, find_person, person_view, top_people
from .query import answer, evidence, retrieve_hybrid
from .semantic import build_semantic_index, semantic_status
from .ui import serve


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_doctor(_: argparse.Namespace) -> int:
    codex_path = shutil.which("codex")
    qmd_path = shutil.which("qmd")
    with database() as conn:
        fts5 = bool(conn.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0])
    emit(
        {
            "ok": fts5,
            "required": {"sqlite_fts5": fts5},
            "optional": {
                "codex": {"path": codex_path, "available": bool(codex_path)},
                "qmd": {"path": qmd_path, "available": bool(qmd_path)},
            },
            "sqlite_fts5": fts5,
            "database": str(db_path()),
        }
    )
    return 0 if fts5 else 1


def cmd_import_json(args: argparse.Namespace) -> int:
    with database() as conn:
        result = import_json(conn, args.path.expanduser())
    emit(result)
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    with database() as conn:
        result = {
            "database": str(db_path()),
            "analysis_database": str(analysis_db_path()),
            "identities": role_stats(conn),
            "chats": conn.execute("SELECT count(*) FROM chats").fetchone()[0],
            "messages": conn.execute("SELECT count(*) FROM messages").fetchone()[0],
            "profiles": conn.execute("SELECT count(*) FROM analysis.profile_summaries").fetchone()[0],
            "facts": conn.execute("SELECT count(*) FROM analysis.profile_facts WHERE status='active'").fetchone()[0],
            "message_range": dict(
                conn.execute("SELECT min(ts) AS first_ts,max(ts) AS last_ts FROM messages").fetchone()
            ),
            "last_import": (
                dict(conn.execute("SELECT value,updated_at FROM meta WHERE key='last_structured_import'").fetchone())
                if conn.execute("SELECT 1 FROM meta WHERE key='last_structured_import'").fetchone()
                else None
            ),
            "semantic": semantic_status(),
        }
    emit(result)
    return 0


def cmd_classify(_: argparse.Namespace) -> int:
    with database() as conn:
        self_row = conn.execute(
            "SELECT p.wxid FROM people p JOIN person_roles r ON r.person_id=p.id "
            "WHERE r.role='self' LIMIT 1"
        ).fetchone()
        emit({"identities": refresh_person_roles(conn, self_wxid=self_row[0] if self_row else None)})
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    outputs = []
    with database() as conn:
        if args.person:
            person = find_person(conn, args.person)
            if person is None:
                emit({"error": "PERSON_NOT_FOUND", "query": args.person})
                return 1
            people = [person]
        else:
            people = top_people(conn, 100_000 if args.all else args.top)
        for index, person in enumerate(people, start=1):
            print(f"[{index}/{len(people)}] profile {person['display_name']}", file=sys.stderr)
            try:
                outputs.append(build_profile(conn, person, message_limit=args.message_limit))
            except Exception as exc:
                outputs.append({"person": person["display_name"], "error": str(exc)})
    emit({"profiles": outputs})
    return 0 if all("error" not in item for item in outputs) else 2


def cmd_person(args: argparse.Namespace) -> int:
    with database() as conn:
        person = find_person(conn, args.query)
        if person is None:
            emit({"error": "PERSON_NOT_FOUND", "query": args.query})
            return 1
        emit(person_view(conn, person))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    with database() as conn:
        emit(answer(conn, args.question, limit=args.limit, mode=args.mode))
    return 0


def cmd_retrieve(args: argparse.Namespace) -> int:
    """Return traceable evidence without starting a nested Codex session."""
    with database() as conn:
        emit(retrieve_hybrid(conn, args.question, limit=args.limit, mode=args.mode))
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    with readonly_database() as conn:
        result = build_semantic_index(conn, embed=not args.no_embed)
    emit(result)
    return 0 if result["ok"] else 2


def cmd_evidence(args: argparse.Namespace) -> int:
    with database() as conn:
        item = evidence(conn, args.message_id)
    if item is None:
        emit({"error": "EVIDENCE_NOT_FOUND", "message_id": args.message_id})
        return 1
    emit(item)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    serve(args.host, args.port)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="wechat-memory")
    commands = root.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

    structured = commands.add_parser(
        "import-json",
        help="导入用户已依法取得的结构化 JSON；不执行提取或解密",
    )
    structured.add_argument("path", type=Path)
    structured.set_defaults(func=cmd_import_json)

    stats = commands.add_parser("stats")
    stats.set_defaults(func=cmd_stats)

    classify = commands.add_parser("classify")
    classify.set_defaults(func=cmd_classify)

    profile = commands.add_parser("profile")
    profile.add_argument("--person")
    profile.add_argument("--top", type=int, default=10)
    profile.add_argument("--all", action="store_true", help="画像全部有私聊证据的人物")
    profile.add_argument("--message-limit", type=int, default=600)
    profile.set_defaults(func=cmd_profile)

    person = commands.add_parser("person")
    person.add_argument("query")
    person.set_defaults(func=cmd_person)

    query = commands.add_parser("query")
    query.add_argument("question")
    query.add_argument("--limit", type=int, default=80)
    query.add_argument("--mode", choices=("auto", "exact", "semantic", "hybrid"), default="auto")
    query.set_defaults(func=cmd_query)

    retrieval = commands.add_parser(
        "retrieve",
        help="只检索消息和画像事实；不启动 Codex，由当前 Agent 综合",
    )
    retrieval.add_argument("question")
    retrieval.add_argument("--limit", type=int, default=80)
    retrieval.add_argument(
        "--mode",
        choices=("auto", "exact", "semantic", "hybrid"),
        default="auto",
    )
    retrieval.set_defaults(func=cmd_retrieve)

    index = commands.add_parser("index", help="导出安全检索文本并更新独立 QMD 向量索引")
    index.add_argument("--no-embed", action="store_true", help="只更新文档索引，不生成向量")
    index.set_defaults(func=cmd_index)

    ev = commands.add_parser("evidence")
    ev.add_argument("message_id", type=int)
    ev.set_defaults(func=cmd_evidence)

    ui = commands.add_parser("serve")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.set_defaults(func=cmd_serve)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except (sqlite3.Error, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        emit({"error": type(exc).__name__, "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
