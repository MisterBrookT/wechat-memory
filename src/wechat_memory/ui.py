from __future__ import annotations

import ipaddress
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import readonly_database
from .query import evidence
from .views import interaction_graph, overview, person_detail


WEB_ROOT = Path(__file__).with_name("web")


class MemoryHandler(BaseHTTPRequestHandler):
    server_version = "WeChatMemory/0.2"

    def log_message(self, format: str, *args: object) -> None:
        # Do not put private search terms or response bodies into access logs.
        print(f"ui {self.client_address[0]} {args[0] if args else ''}")

    def _headers(self, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _json(self, value: object, status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8")
        self.wfile.write(payload)

    def _trusted_request(self) -> bool:
        host = self.headers.get("Host", "")
        try:
            hostname = urlparse(f"//{host}").hostname or ""
            trusted_host = hostname == "localhost" or ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            trusted_host = False
        if trusted_host:
            return True
        self._json({"error": "FORBIDDEN"}, HTTPStatus.FORBIDDEN)
        return False

    def do_GET(self) -> None:  # noqa: N802
        if not self._trusted_request():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/overview":
                with readonly_database() as conn:
                    self._json(overview(conn))
                return
            if path == "/api/graph":
                params = parse_qs(parsed.query)
                with readonly_database() as conn:
                    self._json(
                        interaction_graph(
                            conn,
                            days=int(params.get("days", ["90"])[0]),
                            people_limit=int(params.get("people", ["80"])[0]),
                            group_limit=int(params.get("groups", ["12"])[0]),
                        )
                    )
                return
            if path.startswith("/api/people/"):
                person_id = int(path.rsplit("/", 1)[1])
                with readonly_database() as conn:
                    result = person_detail(conn, person_id)
                self._json(result or {"error": "PERSON_NOT_FOUND"}, HTTPStatus.OK if result else HTTPStatus.NOT_FOUND)
                return
            if path.startswith("/api/evidence/"):
                message_id = int(path.rsplit("/", 1)[1])
                with readonly_database() as conn:
                    result = evidence(conn, message_id)
                self._json(result or {"error": "EVIDENCE_NOT_FOUND"}, HTTPStatus.OK if result else HTTPStatus.NOT_FOUND)
                return
            self._static(path)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": "BAD_REQUEST", "message": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": type(exc).__name__, "message": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        candidate = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT.resolve():
            self._json({"error": "NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self._json({"error": "NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            return
        payload = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self._headers(HTTPStatus.OK, content_type)
        self.wfile.write(payload)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError("为保护本地微信资料，UI 只允许监听 loopback 地址")
    server = ThreadingHTTPServer((host, port), MemoryHandler)
    print(f"WeChat Memory: http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
