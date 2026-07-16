import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_memory.db import database, readonly_database
from wechat_memory.importer import import_payload
from wechat_memory.query import evidence, retrieve, retrieve_hybrid
from wechat_memory.semantic import export_search_documents
from wechat_memory.store import upsert_chat, upsert_message
from wechat_memory.views import interaction_graph


DEMO = {
    "schema_version": 1,
    "namespace": "demo",
    "owner": {"id": "me", "display_name": "Me"},
    "people": [
        {"id": "alice", "display_name": "Alice"},
        {"id": "bob", "display_name": "Bob"},
    ],
    "chats": [
        {"id": "alice-chat", "name": "Alice", "type": "private", "peer_id": "alice"},
        {"id": "team", "name": "Agent Builders", "type": "group"},
    ],
    "messages": [
        {
            "id": "m1",
            "chat_id": "alice-chat",
            "sender_id": "alice",
            "sender_name": "Alice",
            "timestamp": "2026-07-16T10:00:00+08:00",
            "type": "text",
            "content": "I am building an AI agent product.",
        },
        {
            "id": "m2",
            "chat_id": "team",
            "sender_id": "bob",
            "sender_name": "Bob",
            "timestamp": "2026-07-16T11:00:00+08:00",
            "type": "text",
            "content": "We are preparing a seed round.",
        },
    ],
}


class CoreTest(unittest.TestCase):
    def test_core_and_analysis_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "crm.sqlite"
            with database(path) as conn:
                core = {row[0] for row in conn.execute("SELECT name FROM main.sqlite_master")}
                analysis = {row[0] for row in conn.execute("SELECT name FROM analysis.sqlite_master")}
                self.assertNotIn("profile_summaries", core)
                self.assertIn("profile_summaries", analysis)
            with readonly_database(path) as conn:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute(
                        "INSERT INTO people(wxid,display_name,created_at,updated_at) "
                        "VALUES('x','x','x','x')"
                    )

    def test_structured_import_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with database(Path(tmp) / "crm.sqlite") as conn:
                first = import_payload(conn, DEMO)
                second = import_payload(conn, DEMO)
                self.assertEqual(first["messages_written"], 2)
                self.assertEqual(second["messages_written"], 0)
                self.assertEqual(conn.execute("SELECT count(*) FROM messages").fetchone()[0], 2)
                owner_roles = {
                    row[0]
                    for row in conn.execute(
                        "SELECT r.role FROM person_roles r JOIN people p ON p.id=r.person_id "
                        "WHERE p.display_name='Me'"
                    )
                }
                self.assertIn("self", owner_roles)

    def test_message_update_preserves_raw_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with database(Path(tmp) / "crm.sqlite") as conn:
                chat = {"username": "chat-1", "chat": "Alice", "chat_type": "private"}
                chat_id = upsert_chat(conn, chat)
                message = {
                    "source_key": "import:demo:message:1",
                    "timestamp": 10,
                    "type": "text",
                    "content": "first",
                }
                upsert_message(conn, chat_id, chat, message)
                upsert_message(conn, chat_id, chat, {**message, "content": "updated"})
                conn.commit()
                self.assertEqual(conn.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
                self.assertEqual(
                    conn.execute("SELECT count(*) FROM raw_record_versions").fetchone()[0], 2
                )

    def test_retrieval_does_not_require_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with database(Path(tmp) / "crm.sqlite") as conn:
                import_payload(conn, DEMO)
                result = retrieve(conn, "AI agent")
                self.assertEqual(result["messages"][0]["sender_name"], "Alice")
                self.assertEqual(result["facts"], [])
                item = evidence(conn, result["messages"][0]["id"])
                self.assertEqual(item["chat_name"], "Alice")
                self.assertTrue(item["current_payload_hash"])

    def test_hybrid_retrieval_can_use_vector_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with database(Path(tmp) / "crm.sqlite") as conn:
                import_payload(conn, DEMO)
                message_id = int(
                    conn.execute("SELECT id FROM messages WHERE sender_name='Bob'").fetchone()[0]
                )

                def semantic_search(_question: str, *, limit: int):
                    self.assertGreaterEqual(limit, 40)
                    return {
                        "available": True,
                        "warning": "",
                        "hits": [{"message_id": message_id, "score": 0.9, "rank": 1}],
                    }

                result = retrieve_hybrid(
                    conn, "Who is fundraising?", mode="semantic", semantic_search=semantic_search
                )
                self.assertEqual(result["messages"][0]["id"], message_id)
                self.assertIn("qmd-vector", result["messages"][0]["retrieval_backends"])

    def test_auto_retrieval_degrades_when_vector_index_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with database(Path(tmp) / "crm.sqlite") as conn:
                import_payload(conn, DEMO)
                with patch("wechat_memory.query.semantic_index_current", return_value=False):
                    result = retrieve_hybrid(conn, "AI agent", mode="auto")
                self.assertEqual(result["retrieval"]["backends"], ["sql-fts"])
                self.assertTrue(result["retrieval"]["degraded"])

    def test_search_documents_are_traceable_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            documents = Path(tmp) / "documents"
            with database(Path(tmp) / "crm.sqlite") as conn:
                import_payload(conn, DEMO)
                result = export_search_documents(conn, documents)
            files = list(documents.glob("*.md"))
            self.assertGreater(result["documents"], 0)
            self.assertTrue(any("message_id:" in item.read_text(encoding="utf-8") for item in files))
            self.assertEqual(stat.S_IMODE(documents.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in files))

    def test_graph_is_observed_interaction_not_claimed_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with database(Path(tmp) / "crm.sqlite") as conn:
                import_payload(conn, DEMO)
                graph = interaction_graph(conn)
            self.assertGreaterEqual(len(graph["nodes"]), 2)
            self.assertTrue(
                all(
                    edge["kind"] in {"private", "group_context", "group_message"}
                    for edge in graph["edges"]
                )
            )


if __name__ == "__main__":
    unittest.main()
