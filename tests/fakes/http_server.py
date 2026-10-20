"""Tiny in-process HTTP server for adapter tests (no network, no real services).

    with FakeServer() as srv:
        srv.route("GET", "/health", {"ok": True})
        srv.route("POST", "/prompt", lambda req: {"prompt_id": "p1"})
        requests.get(srv.url + "/health")

Routes may return a dict/list (JSON), ``bytes`` (raw), or a ``(status, body)`` tuple.
Every request is recorded in ``srv.requests``.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    body: bytes
    json: Any = None


@dataclass
class FakeServer:
    requests: list[RecordedRequest] = field(default_factory=list)
    _routes: dict[tuple[str, str], Any] = field(default_factory=dict)
    _httpd: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def route(self, method: str, path: str, response: Any) -> None:
        """``response`` may be a value or ``callable(RecordedRequest) -> value``; a list of
        values is consumed one per call (the last one repeats)."""
        self._routes[(method.upper(), path)] = response

    @property
    def url(self) -> str:
        assert self._httpd is not None
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def __enter__(self) -> FakeServer:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                try:
                    parsed_json = json.loads(body) if body else None
                except ValueError:
                    parsed_json = None
                req = RecordedRequest(
                    self.command, parsed.path, parse_qs(parsed.query), body, parsed_json
                )
                outer.requests.append(req)
                resp = outer._routes.get((self.command, parsed.path))
                if resp is None:
                    status, payload = 404, {"error": "no route"}
                else:
                    if isinstance(resp, list):
                        resp = resp.pop(0) if len(resp) > 1 else resp[0]
                    if callable(resp):
                        resp = resp(req)
                    status, payload = resp if isinstance(resp, tuple) else (200, resp)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header(
                    "Content-Type",
                    "application/octet-stream" if isinstance(payload, bytes) else "application/json",
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = _handle  # noqa: N815

            def log_message(self, *args: object) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        assert self._httpd is not None and self._thread is not None
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
