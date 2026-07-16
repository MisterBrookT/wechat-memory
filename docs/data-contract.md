# Structured import contract

`wechat-memory import-json FILE` accepts schema version 1. The canonical schema is [`schemas/import.schema.json`](../schemas/import.schema.json).

## Minimal example

```json
{
  "schema_version": 1,
  "namespace": "my-export",
  "owner": {"id": "me", "display_name": "Me"},
  "people": [
    {"id": "alice", "display_name": "Alice"}
  ],
  "chats": [
    {"id": "alice-chat", "name": "Alice", "type": "private", "peer_id": "alice"}
  ],
  "messages": [
    {
      "id": "m-1",
      "chat_id": "alice-chat",
      "sender_id": "alice",
      "sender_name": "Alice",
      "timestamp": "2026-07-16T10:00:00+08:00",
      "type": "text",
      "content": "I am building an AI agent product."
    }
  ]
}
```

## Semantics

- `namespace` isolates IDs from separate sources. Reusing a namespace and message ID updates the same logical record.
- `owner` is optional. When present, it receives the `self` role.
- `people[].id`, `chats[].id`, and `messages[].id` must be stable within a namespace.
- A private chat should set `peer_id` to the corresponding person ID.
- `timestamp` accepts Unix seconds or ISO 8601.
- Message types include `text`, `image`, `voice`, `video`, `file`, `link`, `location`, and `system`; unknown strings are preserved.
- Empty-media placeholders remain evidence but are not treated as semantic text.

## Adapter rule

Adapters must produce this contract from data the user is already authorized to access. Do not contribute adapters that extract keys, bypass access controls, scan process memory, modify a client, Hook/inject code, or parse proprietary encrypted database layouts.
