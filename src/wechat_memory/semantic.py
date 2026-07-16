from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, now_iso


COLLECTION = "wechat-memory"
INDEX_NAME = "wechat-memory"
EMBED_MODEL = "hf:ggml-org/embeddinggemma-300M-GGUF/embeddinggemma-300M-Q8_0.gguf"
GENERATE_MODEL = "hf:tobil/qmd-query-expansion-1.7B-gguf/qmd-query-expansion-1.7B-q4_k_m.gguf"
RERANK_MODEL = "hf:ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/qwen3-reranker-0.6b-q8_0.gguf"
MESSAGE_ID_RE = re.compile(r"message_id:(\d+)")
VECTOR_WINDOW_MESSAGES = 8
VECTOR_WINDOW_CHARS = 2600
VECTOR_SEGMENT_CHARS = 2100
VECTOR_OVERLAP_MESSAGES = 1
VECTOR_HIGHLIGHT_CHARS = 80
NON_SEMANTIC_TYPES = {"图片", "表情", "语音", "视频", "通话", "系统"}
PLACEHOLDER_CONTENT = {
    "[图片]",
    "[表情]",
    "[语音]",
    "[视频]",
    "[通话]",
    "[系统]",
    "[位置]",
}


def search_docs_path() -> Path:
    return Path(
        os.environ.get("WECHAT_MEMORY_SEARCH_DOCS", DEFAULT_DB.parent / "search-docs")
    ).expanduser()


def qmd_config_dir() -> Path:
    return Path(
        os.environ.get("WECHAT_MEMORY_QMD_CONFIG", DEFAULT_DB.parent / "qmd")
    ).expanduser()


def qmd_cache_home() -> Path:
    configured = os.environ.get("WECHAT_MEMORY_QMD_CACHE_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Library/Caches/wechat-memory"


def qmd_index_path() -> Path:
    return qmd_cache_home() / "qmd" / f"{INDEX_NAME}.sqlite"


def qmd_environment() -> dict[str, str]:
    config = qmd_config_dir()
    cache = qmd_cache_home()
    config.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    os.chmod(config, 0o700)
    env = os.environ.copy()
    env["QMD_CONFIG_DIR"] = str(config)
    env["XDG_CACHE_HOME"] = str(cache)
    return env


def _secure_qmd_index() -> None:
    index = qmd_index_path()
    for candidate in (index, Path(f"{index}-wal"), Path(f"{index}-shm")):
        if candidate.exists():
            os.chmod(candidate, 0o600)


def _atomic_write(path: Path, content: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == content:
            return False
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    return True


def _single_line(value: Any, *, limit: int = 4000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…[截断]"


def _semantic_content(row: Any) -> str | None:
    """Return high-signal readable text for vector search; exact search keeps everything."""
    message_type = _single_line(row["message_type"], limit=50)
    content = _single_line(row["display_content"], limit=12000)
    if not content or message_type in NON_SEMANTIC_TYPES or content in PLACEHOLDER_CONTENT:
        return None
    content = re.sub(r"(?:https?://|mp://)\S+", " ", content)
    content = re.sub(r"\s+", " ", content).strip(" ·|-")
    signal = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", content)
    # Short acknowledgements and URL-only feeds add noise. SQLite/FTS still retains them.
    if len(signal) < 6:
        return None
    return content


def _segments(content: str) -> list[str]:
    if len(content) <= VECTOR_SEGMENT_CHARS:
        return [content]
    return [
        content[start : start + VECTOR_SEGMENT_CHARS]
        for start in range(0, len(content), VECTOR_SEGMENT_CHARS)
    ]


def _message_units(row: Any) -> list[str]:
    content = _semantic_content(row)
    if content is None:
        return []
    timestamp = datetime.fromtimestamp(int(row["ts"]), UTC).isoformat(
        timespec="seconds"
    )
    sender = _single_line(row["sender_name"] or "未知发送者", limit=200)
    segments = _segments(content)
    suffix = len(segments) > 1
    return [
        (
            f"- [message_id:{int(row['id'])}] {timestamp} | {sender}"
            f"{' | 片段 ' + str(index) + '/' + str(len(segments)) if suffix else ''}"
            f" | {segment}"
        )
        for index, segment in enumerate(segments, start=1)
    ]


def _windows(units: list[str]) -> list[list[str]]:
    result: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for unit in units:
        unit_chars = len(unit) + 1
        if current and (
            len(current) >= VECTOR_WINDOW_MESSAGES
            or current_chars + unit_chars > VECTOR_WINDOW_CHARS
        ):
            result.append(current)
            overlap = current[-VECTOR_OVERLAP_MESSAGES:]
            if sum(len(item) + 1 for item in overlap) + unit_chars <= VECTOR_WINDOW_CHARS:
                current = overlap
                current_chars = sum(len(item) + 1 for item in current)
            else:
                current = []
                current_chars = 0
        current.append(unit)
        current_chars += unit_chars
    if current:
        result.append(current)
    return result


def export_search_documents(conn, root: Path | None = None) -> dict[str, Any]:
    """Export small, traceable semantic windows; SQLite remains source of truth."""
    target = (root or search_docs_path()).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o700)
    rows = conn.execute(
        """
        SELECT m.id,m.ts,m.message_type,m.sender_name,m.display_content,m.updated_at,c.id AS chat_id,
               c.name AS chat_name,c.chat_type,c.updated_at AS chat_updated_at
        FROM messages m JOIN chats c ON c.id=m.chat_id
        WHERE trim(m.display_content)<>''
        ORDER BY c.id,m.ts,m.id
        """
    ).fetchall()
    expected: set[Path] = set()
    updated = 0
    unchanged = 0
    indexed_message_ids: set[int] = set()
    content_digest = hashlib.sha256()
    active_key: tuple[int, str] | None = None
    active_rows: list[Any] = []

    def write_group(messages: list[Any]) -> None:
        nonlocal updated, unchanged
        if not messages:
            return
        first = messages[0]
        chat_id = int(first["chat_id"])
        month = datetime.fromtimestamp(int(first["ts"]), UTC).strftime("%Y-%m")
        chat_name = _single_line(first["chat_name"], limit=500)
        chat_type = _single_line(first["chat_type"], limit=50)
        units: list[str] = []
        for message in messages:
            message_units = _message_units(message)
            if message_units:
                indexed_message_ids.add(int(message["id"]))
                units.extend(message_units)
                semantic_content = _semantic_content(message) or ""
                if len(semantic_content) >= VECTOR_HIGHLIGHT_CHARS:
                    for segment_number, unit in enumerate(message_units, start=1):
                        path = target / (
                            f"message-{int(message['id']):08d}-s{segment_number:03d}.md"
                        )
                        expected.add(path)
                        lines = [
                            "---",
                            f"message_id: {int(message['id'])}",
                            f"chat_id: {chat_id}",
                            f"chat_type: {json.dumps(chat_type, ensure_ascii=False)}",
                            f"chat_name: {json.dumps(chat_name, ensure_ascii=False)}",
                            "source: wechat-memory",
                            "---",
                            "",
                            f"# {chat_name} · 重点消息",
                            "",
                            unit,
                        ]
                        content = "\n".join(lines) + "\n"
                        content_digest.update(path.name.encode())
                        content_digest.update(hashlib.sha256(content.encode()).digest())
                        if _atomic_write(path, content):
                            updated += 1
                        else:
                            unchanged += 1
        for window_number, window in enumerate(_windows(units), start=1):
            path = target / (
                f"chat-{chat_id:06d}-{month.replace('-', '')}-w{window_number:05d}.md"
            )
            expected.add(path)
            lines = [
                "---",
                f"chat_id: {chat_id}",
                f"chat_type: {json.dumps(chat_type, ensure_ascii=False)}",
                f"chat_name: {json.dumps(chat_name, ensure_ascii=False)}",
                f"month: {json.dumps(month)}",
                f"window: {window_number}",
                "source: wechat-memory",
                "---",
                "",
                f"# {chat_name} · {month} · 对话片段 {window_number}",
                "",
                *window,
            ]
            content = "\n".join(lines) + "\n"
            content_digest.update(path.name.encode())
            content_digest.update(hashlib.sha256(content.encode()).digest())
            if _atomic_write(path, content):
                updated += 1
            else:
                unchanged += 1

    for row in rows:
        month = datetime.fromtimestamp(int(row["ts"]), UTC).strftime("%Y-%m")
        key = (int(row["chat_id"]), month)
        if active_key is not None and key != active_key:
            write_group(active_rows)
            active_rows = []
        active_key = key
        active_rows.append(row)
    write_group(active_rows)

    removed = 0
    for pattern in ("chat-*.md", "message-*.md"):
        for stale in target.glob(pattern):
            if stale not in expected:
                stale.unlink()
                removed += 1
    manifest = {
        "schema_version": 2,
        "generated_at": now_iso(),
        "source_messages": len(rows),
        "indexed_messages": len(indexed_message_ids),
        "documents": len(expected),
        "latest_message_id": max((int(row["id"]) for row in rows), default=None),
        "latest_ts": max((int(row["ts"]) for row in rows), default=None),
        "latest_updated_at": max((str(row["updated_at"]) for row in rows), default=None),
        "latest_chat_updated_at": max(
            (str(row["chat_updated_at"]) for row in rows), default=None
        ),
        "content_sha256": content_digest.hexdigest(),
    }
    _atomic_write(
        target / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return {
        **manifest,
        "path": str(target),
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
    }


def write_qmd_config(root: Path | None = None) -> Path:
    documents = (root or search_docs_path()).expanduser().resolve()
    path = qmd_config_dir() / f"{INDEX_NAME}.yml"
    content = "\n".join(
        [
            'global_context: "个人微信本地消息。每条记录含 message_id、时间、发送者、会话和安全可读正文；用于人物、项目、兴趣和关系上下文查询。"',
            "",
            "collections:",
            f"  {COLLECTION}:",
            f"    path: {json.dumps(str(documents), ensure_ascii=False)}",
            '    pattern: "**/*.md"',
            "    ignore:",
            '      - "**/.*"',
            "    context:",
            '      "/": "微信原消息派生检索文档；结论必须回到 message_id 对应的 SQLite 原消息核验。"',
            "",
            "models:",
            f"  embed: {EMBED_MODEL}",
            f"  generate: {GENERATE_MODEL}",
            f"  rerank: {RERANK_MODEL}",
            "",
        ]
    )
    _atomic_write(path, content)
    legacy = qmd_config_dir() / "index.yml"
    if legacy.is_file():
        legacy.unlink()
    return path


def _run_qmd(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    old_umask = os.umask(0o077)
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=qmd_environment(),
            start_new_session=True,
        )
    finally:
        os.umask(old_umask)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except KeyboardInterrupt:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)
    finally:
        _secure_qmd_index()
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def build_semantic_index(conn, *, embed: bool = True, binary: str = "qmd") -> dict[str, Any]:
    exported = export_search_documents(conn)
    config = write_qmd_config()
    ready_path = qmd_config_dir() / "ready.json"
    try:
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        ready = {}
    if ready.get("content_sha256") != exported["content_sha256"] and ready_path.exists():
        ready_path.unlink()
    executable = shutil.which(binary)
    if executable is None:
        return {
            "ok": False,
            "stage": "qmd",
            "error": "QMD_NOT_FOUND",
            "export": exported,
            "config": str(config),
        }
    try:
        update = _run_qmd([executable, "--index", INDEX_NAME, "update"], timeout=300)
        if update.returncode != 0:
            return {
                "ok": False,
                "stage": "update",
                "returncode": update.returncode,
                "error": (update.stderr or update.stdout).strip(),
                "export": exported,
                "config": str(config),
            }
        operation = "update"
        if embed:
            vector = _run_qmd(
                [executable, "--index", INDEX_NAME, "embed", "-c", COLLECTION],
                timeout=3600,
            )
            operation = "update+embed"
            if vector.returncode != 0:
                return {
                    "ok": False,
                    "stage": "embed",
                    "returncode": vector.returncode,
                    "error": (vector.stderr or vector.stdout).strip(),
                    "export": exported,
                    "config": str(config),
                }
            _atomic_write(
                ready_path,
                json.dumps(
                    {
                        "schema_version": 1,
                        "completed_at": now_iso(),
                        "content_sha256": exported["content_sha256"],
                        "documents": exported["documents"],
                        "source_messages": exported["source_messages"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "stage": "timeout",
            "returncode": 124,
            "error": f"QMD 超时：{exc.timeout} 秒",
            "export": exported,
            "config": str(config),
        }
    return {
        "ok": True,
        "operation": operation,
        "export": exported,
        "config": str(config),
        "index": str(qmd_index_path()),
    }


def semantic_status() -> dict[str, Any]:
    manifest_path = search_docs_path() / "manifest.json"
    ready_path = qmd_config_dir() / "ready.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = None
    try:
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        ready = None
    index = qmd_index_path()
    return {
        "ready": bool(
            manifest
            and ready
            and ready.get("content_sha256") == manifest.get("content_sha256")
            and index.is_file()
            and index.stat().st_size > 4096
        ),
        "documents": manifest,
        "completed": ready,
        "search_docs": str(search_docs_path()),
        "config": str(qmd_config_dir() / f"{INDEX_NAME}.yml"),
        "index": str(index),
        "index_bytes": index.stat().st_size if index.is_file() else 0,
        "qmd": shutil.which("qmd"),
    }


def semantic_index_current(conn) -> bool:
    status = semantic_status()
    documents = status.get("documents")
    if not status["ready"] or not isinstance(documents, dict):
        return False
    row = conn.execute(
        """
        SELECT count(*) AS messages,max(m.id) AS latest_message_id,max(m.ts) AS latest_ts,
               max(m.updated_at) AS latest_updated_at,
               max(c.updated_at) AS latest_chat_updated_at
        FROM messages m JOIN chats c ON c.id=m.chat_id
        WHERE trim(m.display_content)<>''
        """
    ).fetchone()
    return bool(
        int(row["messages"]) == int(documents.get("source_messages") or -1)
        and row["latest_message_id"] == documents.get("latest_message_id")
        and row["latest_ts"] == documents.get("latest_ts")
        and row["latest_updated_at"] == documents.get("latest_updated_at")
        and row["latest_chat_updated_at"] == documents.get("latest_chat_updated_at")
    )


def _json_payload(stdout: str) -> Any:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        for index in reversed(
            [position for position, char in enumerate(stdout) if char in "[{"]
        ):
            try:
                return json.loads(stdout[index:])
            except json.JSONDecodeError:
                continue
    return None


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("results", "hits", "documents", "matches", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payload] if any(key in payload for key in ("path", "file", "uri")) else []


def _field(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if item.get(name) is not None:
            return item[name]
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        for name in names:
            if metadata.get(name) is not None:
                return metadata[name]
    return None


def _message_ids_from_item(item: dict[str, Any], documents: Path) -> list[int]:
    raw_path = _field(item, "path", "file", "filename", "uri", "document")
    lines: list[str] = []
    if isinstance(raw_path, str):
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = documents / candidate
        try:
            resolved = candidate.expanduser().resolve(strict=True)
            resolved.relative_to(documents.resolve(strict=True))
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, ValueError):
            lines = []
    # Generated windows stay below one QMD chunk, so the whole safe file is the hit.
    if lines:
        weighted: list[tuple[int, int]] = []
        for line in lines:
            weighted.extend(
                (int(value), len(line)) for value in MESSAGE_ID_RE.findall(line)
            )
        found = [
            message_id
            for message_id, _weight in sorted(weighted, key=lambda item: -item[1])
        ]
        if found:
            return list(dict.fromkeys(found))
    snippet = str(_field(item, "snippet", "content", "text", "context") or "")
    found = [int(value) for value in MESSAGE_ID_RE.findall(snippet)]
    if found:
        return list(dict.fromkeys(found))
    if not lines:
        return []
    raw_line = _field(item, "line_start", "line", "line_number", "start_line")
    try:
        line = max(1, int(raw_line or 1))
    except (TypeError, ValueError):
        line = 1
    context = "\n".join(lines[max(0, line - 12) : min(len(lines), line + 12)])
    return [int(value) for value in MESSAGE_ID_RE.findall(context)]


def search_semantic(
    question: str,
    *,
    limit: int = 40,
    binary: str = "qmd",
) -> dict[str, Any]:
    status = semantic_status()
    executable = shutil.which(binary)
    if not status["ready"] or executable is None:
        return {
            "available": False,
            "hits": [],
            "warning": "微信向量索引未就绪；已使用 SQL/FTS。",
        }
    args = [
        executable,
        "--index",
        INDEX_NAME,
        "query",
        f"vec: {question}",
        "--format",
        "json",
        "-n",
        str(max(5, min(int(limit), 80))),
        "--full-path",
        "--line-numbers",
        "-c",
        COLLECTION,
    ]
    try:
        result = _run_qmd(args, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "hits": [], "warning": f"QMD 查询失败：{exc}"}
    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip().splitlines()
        return {
            "available": False,
            "hits": [],
            "warning": f"QMD 查询失败：{reason[-1] if reason else '未知错误'}",
        }
    documents_hits: list[dict[str, Any]] = []
    documents = search_docs_path()
    for rank, item in enumerate(_items(_json_payload(result.stdout)), start=1):
        raw_score = _field(item, "score", "similarity", "rank")
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = 1 / (60 + rank)
        path = str(_field(item, "path", "file", "filename", "uri", "document") or "")
        documents_hits.append(
            {
                "rank": rank,
                "score": score,
                "document": path,
                "message_ids": _message_ids_from_item(item, documents),
            }
        )
    # One representative per semantic document first; only then add neighboring messages.
    # This prevents a few 8-message windows from consuming the entire result budget.
    hits: dict[int, dict[str, Any]] = {}
    max_width = max((len(item["message_ids"]) for item in documents_hits), default=0)
    for offset in range(max_width):
        for document_hit in documents_hits:
            message_ids = document_hit["message_ids"]
            if offset >= len(message_ids):
                continue
            message_id = int(message_ids[offset])
            rank = int(document_hit["rank"])
            score = float(document_hit["score"])
            previous = hits.get(message_id)
            if previous is None or score > previous["score"]:
                hits[message_id] = {
                    "message_id": message_id,
                    "score": score,
                    "rank": rank,
                    "document": document_hit["document"],
                }
            if len(hits) >= max(1, min(int(limit), 200)):
                break
        if len(hits) >= max(1, min(int(limit), 200)):
            break
    ordered = list(hits.values())
    return {
        "available": True,
        "hits": ordered[: max(1, min(int(limit), 200))],
        "warning": "",
    }
