# Contributing

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

Use synthetic fixtures only. Never commit personal chat exports, databases, media, credentials, identifiers, generated search documents, indexes, or model caches.

## Accepted scope

- structured import adapters for data already lawfully accessible to the user;
- evidence storage and migrations;
- deterministic classification;
- local retrieval, profiles, and visualization;
- privacy, security, and deletion improvements.

## Rejected scope

No key extraction, decryption, process-memory scanning, reverse engineering, Hook/injection, client modification, access-control bypass, proprietary encrypted database parsing, or instructions enabling those activities.

Every derived fact must remain traceable to a source message ID.
