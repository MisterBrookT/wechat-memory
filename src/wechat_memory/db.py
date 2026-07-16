from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DEFAULT_DB = Path.home() / "Library" / "Application Support" / "wechat-memory" / "crm.sqlite"
DEFAULT_ANALYSIS_DB = DEFAULT_DB.with_name("analysis.sqlite")


def db_path() -> Path:
    return Path(os.environ.get("WECHAT_MEMORY_DB", DEFAULT_DB)).expanduser()


def analysis_db_path(core_path: Path | None = None) -> Path:
    configured = os.environ.get("WECHAT_MEMORY_ANALYSIS_DB")
    if configured:
        return Path(configured).expanduser()
    core = core_path or db_path()
    if core == DEFAULT_DB:
        return DEFAULT_ANALYSIS_DB
    return core.with_name(f"{core.stem}.analysis{core.suffix}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def owner_name(conn: sqlite3.Connection | None = None) -> str:
    configured = os.environ.get("WECHAT_MEMORY_OWNER_NAME", "").strip()
    if configured:
        return configured
    if conn is not None:
        row = conn.execute(
            """
            SELECT p.display_name FROM people p
            JOIN person_roles r ON r.person_id=p.id AND r.role='self'
            ORDER BY r.confidence DESC LIMIT 1
            """
        ).fetchone()
        if row and str(row[0]).strip():
            return str(row[0]).strip()
    return "我"


CORE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_records (
  id INTEGER PRIMARY KEY,
  source_kind TEXT NOT NULL,
  source_key TEXT NOT NULL UNIQUE,
  observed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_records_kind ON raw_records(source_kind);

CREATE TABLE IF NOT EXISTS raw_record_versions (
  id INTEGER PRIMARY KEY,
  source_key TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(source_key, payload_hash)
);

CREATE INDEX IF NOT EXISTS idx_raw_versions_source ON raw_record_versions(source_key, id);

CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  wxid TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  remark TEXT,
  nickname TEXT,
  alias TEXT,
  avatar_url TEXT,
  source TEXT NOT NULL DEFAULT 'structured-import',
  raw_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_people_display ON people(display_name);
CREATE INDEX IF NOT EXISTS idx_people_remark ON people(remark);

CREATE TABLE IF NOT EXISTS person_roles (
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN (
    'private_chat_peer','group_member_seen','official_or_service','self','unknown'
  )),
  source TEXT NOT NULL,
  confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
  evidence_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(person_id, role, source)
);

CREATE INDEX IF NOT EXISTS idx_person_roles_role ON person_roles(role, person_id);

CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY,
  wxid TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  chat_type TEXT NOT NULL,
  last_ts INTEGER,
  raw_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chats_type ON chats(chat_type);

CREATE TABLE IF NOT EXISTS chat_members (
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  group_nickname TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(chat_id, person_id)
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  source_key TEXT NOT NULL UNIQUE,
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  sender_person_id INTEGER REFERENCES people(id),
  sender_name TEXT,
  local_id INTEGER,
  ts INTEGER NOT NULL,
  message_type TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  display_content TEXT NOT NULL DEFAULT '',
  raw_json TEXT NOT NULL,
  raw_record_id INTEGER REFERENCES raw_records(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_sender_ts ON messages(sender_person_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  message_id UNINDEXED,
  content,
  sender_name,
  chat_name,
  tokenize='unicode61 remove_diacritics 2'
);
"""


ANALYSIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis.meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis.profile_runs (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL,
  model TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  error TEXT
);

CREATE TABLE IF NOT EXISTS analysis.profile_summaries (
  person_id INTEGER PRIMARY KEY,
  summary TEXT NOT NULL,
  model TEXT NOT NULL,
  source_run_id INTEGER,
  generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis.profile_facts (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL,
  category TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
  evidence_message_id INTEGER NOT NULL,
  first_seen_ts INTEGER,
  last_seen_ts INTEGER,
  status TEXT NOT NULL DEFAULT 'active',
  user_corrected INTEGER NOT NULL DEFAULT 0 CHECK(user_corrected IN (0,1)),
  source_run_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(person_id, category, value, evidence_message_id)
);

CREATE TABLE IF NOT EXISTS analysis.fact_evidence (
  fact_id INTEGER NOT NULL REFERENCES profile_facts(id) ON DELETE CASCADE,
  evidence_kind TEXT NOT NULL,
  evidence_id INTEGER NOT NULL,
  support TEXT NOT NULL DEFAULT 'supports',
  evidence_payload_hash TEXT,
  quote_text TEXT,
  PRIMARY KEY(fact_id, evidence_kind, evidence_id)
);

CREATE TABLE IF NOT EXISTS analysis.profile_summary_evidence (
  person_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  evidence_payload_hash TEXT NOT NULL,
  quote_text TEXT NOT NULL,
  PRIMARY KEY(person_id, message_id)
);

CREATE INDEX IF NOT EXISTS analysis.idx_profile_facts_person
ON profile_facts(person_id, category);

CREATE VIRTUAL TABLE IF NOT EXISTS analysis.profile_facts_fts USING fts5(
  fact_id UNINDEXED,
  person_name,
  category,
  value,
  tokenize='unicode61 remove_diacritics 2'
);
"""


LEGACY_ANALYSIS_TABLES = (
    "profile_runs",
    "profile_summaries",
    "profile_facts",
    "fact_evidence",
    "profile_summary_evidence",
)


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30)
    _secure_database_files(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _attach_analysis(conn: sqlite3.Connection, path: Path, *, readonly: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = f"file:{path}?mode=ro" if readonly else str(path)
    conn.execute("ATTACH DATABASE ? AS analysis", (target,))
    if not readonly:
        _secure_database_files(path)


def _secure_database_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            os.chmod(candidate, 0o600)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _legacy_tables(conn: sqlite3.Connection) -> set[str]:
    names = {row[0] for row in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
    return set(LEGACY_ANALYSIS_TABLES) & names


def _migrate_legacy_analysis(conn: sqlite3.Connection) -> bool:
    legacy = _legacy_tables(conn)
    if not legacy:
        return False
    required = set(LEGACY_ANALYSIS_TABLES)
    if legacy != required:
        raise RuntimeError(f"旧画像表不完整，停止迁移：{sorted(legacy)}")
    order = (
        "profile_runs",
        "profile_summaries",
        "profile_facts",
        "fact_evidence",
        "profile_summary_evidence",
    )
    for table in order:
        columns = [row[1] for row in conn.execute(f"PRAGMA main.table_info({table})")]
        primary = [
            row[1]
            for row in sorted(
                conn.execute(f"PRAGMA main.table_info({table})"), key=lambda item: item[5]
            )
            if row[5]
        ]
        quoted = ",".join(f'"{name}"' for name in columns)
        conn.execute(
            f"INSERT OR IGNORE INTO analysis.{table}({quoted}) SELECT {quoted} FROM main.{table}"
        )
        join = " AND ".join(f'a."{name}" IS s."{name}"' for name in primary)
        differs = " OR ".join(f'a."{name}" IS NOT s."{name}"' for name in columns)
        mismatch = int(
            conn.execute(
                f"SELECT count(*) FROM main.{table} s "
                f"LEFT JOIN analysis.{table} a ON {join} WHERE {differs}"
            ).fetchone()[0]
        )
        if mismatch:
            raise RuntimeError(f"画像迁移存在 {mismatch} 条主键冲突或内容不一致：{table}")
    conn.commit()
    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS main.profile_facts_fts")
        for table in reversed(order):
            conn.execute(f"DROP TABLE main.{table}")
        conn.execute(
            "INSERT INTO analysis.meta(key,value,updated_at) VALUES('legacy_migrated','1',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (now_iso(),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(CORE_SCHEMA)
    _ensure_column(conn, "messages", "raw_record_id", "INTEGER REFERENCES raw_records(id)")
    _ensure_column(conn, "messages", "display_content", "TEXT NOT NULL DEFAULT ''")
    conn.executescript(ANALYSIS_SCHEMA)
    analysis_current = conn.execute(
        "SELECT value FROM analysis.meta WHERE key='schema_version'"
    ).fetchone()
    legacy_migrated = _migrate_legacy_analysis(conn)
    current = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if current is None or int(current[0]) < 6:
        from .message_content import readable_content

        for row in conn.execute("SELECT id,content,message_type FROM messages"):
            conn.execute(
                "UPDATE messages SET display_content=? WHERE id=?",
                (readable_content(row["content"], row["message_type"]), row["id"]),
            )
        conn.execute("DELETE FROM messages_fts")
        conn.execute(
            """
            INSERT INTO messages_fts(rowid,message_id,content,sender_name,chat_name)
            SELECT m.id,m.id,m.display_content,coalesce(m.sender_name,''),c.name
            FROM messages m JOIN chats c ON c.id=m.chat_id
            """
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_record_versions(source_key,payload_hash,observed_at,payload_json)
        SELECT source_key,payload_hash,observed_at,payload_json FROM raw_records
        """
    )
    if analysis_current is None or legacy_migrated:
        conn.execute("DELETE FROM analysis.profile_facts_fts")
        conn.execute(
            """
            INSERT INTO analysis.profile_facts_fts(rowid,fact_id,person_name,category,value)
            SELECT f.id,f.id,p.display_name,f.category,f.value
            FROM analysis.profile_facts f JOIN people p ON p.id=f.person_id
            WHERE f.status='active'
            """
        )
    conn.execute(
        "INSERT INTO meta(key,value,updated_at) VALUES('schema_version','6',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
        (now_iso(),),
    )
    conn.execute(
        "INSERT INTO analysis.meta(key,value,updated_at) VALUES('schema_version','1',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
        (now_iso(),),
    )
    conn.commit()


@contextmanager
def database(
    path: Path | None = None,
    analysis_path: Path | None = None,
) -> Iterator[sqlite3.Connection]:
    target = path or db_path()
    conn = connect(target)
    attached = analysis_path or analysis_db_path(target)
    try:
        _attach_analysis(conn, attached)
        initialize(conn)
        _secure_database_files(target)
        _secure_database_files(attached)
        yield conn
    finally:
        conn.close()
        _secure_database_files(target)
        _secure_database_files(attached)


@contextmanager
def readonly_database(
    path: Path | None = None,
    analysis_path: Path | None = None,
) -> Iterator[sqlite3.Connection]:
    target = path or db_path()
    uri = f"file:{target}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        _attach_analysis(conn, analysis_path or analysis_db_path(target), readonly=True)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        yield conn
    finally:
        conn.close()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
