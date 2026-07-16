# Security policy

## Supported code

Security fixes target the latest `main` branch.

## Report a vulnerability

Use GitHub private vulnerability reporting for this repository. Do not include real chat databases, message exports, credentials, keys, access tokens, personal identifiers, or screenshots containing private conversations. Build a synthetic reproduction.

## Hard boundary

Reports and feature requests about key extraction, decryption, process-memory scanning, reverse engineering, Hook/injection, client modification, access-control bypass, or proprietary encrypted database parsing are out of scope and will be closed.

## Local security model

- Databases and search artifacts are local files.
- Generated semantic documents and indexes use owner-only permissions where supported.
- UI defaults to `127.0.0.1` and is read-only.
- No telemetry is implemented.
- QMD runs locally.
- `codex` model configuration controls whether selected evidence stays local or reaches a remote provider.
