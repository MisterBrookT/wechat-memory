# Privacy

WeChat Memory has no telemetry, analytics, account system, or hosted backend.

## Local data

Default storage:

```text
~/Library/Application Support/wechat-memory/
~/Library/Caches/wechat-memory/
```

These files can contain private conversations and derived inferences. Back them up only to locations you trust. Delete both directories to remove local project data and rebuildable indexes.

## Models

SQLite and QMD processing are local. Profile generation and `wechat-memory query` call the installed `codex` CLI. Whether message evidence is sent to a remote model depends on that CLI's model and provider configuration. Review it before processing sensitive content.

## Other people

Exports contain information about correspondents and group members. Operators—not this project—control collection, purpose, retention, sharing, deletion, and model-provider choices.
