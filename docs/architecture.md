# Architecture

WeChat Memory separates evidence, deterministic structure, rebuildable analysis, and retrieval.

## Six layers

1. **Structured import boundary**
   - Input: schema-versioned JSON supplied by the user.
   - No extraction, decryption, reverse engineering, process access, or proprietary database parsing.
   - Imports are namespaced and idempotent.

2. **Evidence store**
   - `raw_records` holds the latest payload for each stable source key.
   - `raw_record_versions` preserves every distinct payload hash.
   - A message update never silently rewrites historical evidence.

3. **Structured facts**
   - `people`, `chats`, `messages`, `chat_members`, and overlapping `person_roles`.
   - `messages_fts` provides SQLite FTS5 retrieval.
   - `known_identities` is not a friend count. Group members, system accounts, private peers, and contacts overlap.

4. **Optional profiles**
   - Stored in separate `analysis.sqlite`.
   - Every summary and fact references one or more message IDs and evidence snapshots.
   - Profiles can be deleted and rebuilt without changing source data.

5. **Retrieval**
   - Exact person routing, FTS5 BM25, SQL `LIKE`, optional QMD vectors.
   - Reciprocal-rank fusion merges lexical and semantic candidates.
   - Final evidence is read back from SQLite by `message_id`.
   - Missing or stale vectors degrade to SQL/FTS automatically.

6. **Use surfaces**
   - `retrieve`: JSON evidence for an existing Agent session.
   - `query`: optional standalone Codex answer.
   - `serve`: local observed-interaction graph.

## Trust boundaries

```text
user-provided JSON
  │
  ▼
crm.sqlite ──────────────── source of truth
  ├── FTS5
  ├── safe search documents ── QMD named index
  └── selected evidence ─────── optional Codex analysis
                                  │
                                  ▼
                              analysis.sqlite
```

- Raw XML-like payloads are converted to safe display text before search, profiles, or UI use.
- XML tokens, attachment keys, and CDN credentials are not copied into semantic search documents.
- Generated search files and indexes use owner-only permissions where supported.
- The HTTP UI defaults to loopback and is read-only.

## Query without profiles

Profiles are a high-level materialized view, not a gate. A query can retrieve raw messages, then let the current Agent synthesize an answer. Profiles mainly provide compressed long-term context, stable categories, and a future basis for cross-person relations.
