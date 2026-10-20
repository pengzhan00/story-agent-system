from __future__ import annotations

import requests

from .fakes.http_server import FakeServer


def test_routes_records_and_sequences() -> None:
    with FakeServer() as srv:
        srv.route("GET", "/health", {"ok": True})
        srv.route("POST", "/prompt", lambda req: {"echo": req.json})
        srv.route("GET", "/history/p1", [{}, {"p1": {"status": "done"}}])
        srv.route("GET", "/view", b"\x00\x01bin")
        srv.route("GET", "/boom", (500, {"error": "x"}))

        assert requests.get(srv.url + "/health", timeout=5).json() == {"ok": True}
        assert requests.post(srv.url + "/prompt", json={"a": 1}, timeout=5).json() == {"echo": {"a": 1}}
        assert requests.get(srv.url + "/history/p1", timeout=5).json() == {}
        assert requests.get(srv.url + "/history/p1", timeout=5).json()["p1"]["status"] == "done"
        assert requests.get(srv.url + "/view?filename=a.mp4", timeout=5).content == b"\x00\x01bin"
        assert requests.get(srv.url + "/boom", timeout=5).status_code == 500
        assert requests.get(srv.url + "/missing", timeout=5).status_code == 404
        assert srv.requests[-1].path == "/missing"
        assert srv.requests[4].query == {"filename": ["a.mp4"]}
