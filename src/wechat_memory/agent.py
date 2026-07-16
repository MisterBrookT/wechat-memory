from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class AgentError(RuntimeError):
    pass


def run_codex(prompt: str, schema: Path, *, model: str | None = None, timeout: int = 600) -> dict[str, Any]:
    selected = model or os.environ.get("WECHAT_MEMORY_MODEL", "gpt-5.3-codex-spark")
    with tempfile.TemporaryDirectory(prefix="wechat-memory-agent-") as work:
        output = Path(work) / "result.json"
        command = [
            "codex",
            "exec",
            "-",
            "--model",
            selected,
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(output),
            "--color",
            "never",
        ]
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=work,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            raise AgentError(error[-4000:])
        if not output.exists():
            raise AgentError("Codex 未生成结构化结果")
        try:
            return json.loads(output.read_text())
        except json.JSONDecodeError as exc:
            raise AgentError(f"Codex 结果不是 JSON: {exc}") from exc

