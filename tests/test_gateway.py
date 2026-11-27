"""Real HTTP and owned tiny subprocesses; no production API, database, worker or model is launched."""
from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from storyagent.gateway import Backend, Gateway

FAKE = '''
import json, os, sys, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
with Path(sys.argv[2]).open("a") as log: log.write(str(os.getpid()) + "\\n")
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == "/exit": os._exit(2)
        if self.path == "/api/update":
            allowed = "admin=yes" in self.headers.get("Cookie", "")
            self.send_response(200 if allowed else 403); self.end_headers(); self.wfile.write(b"{}"); return
        if self.path == "/api/events":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            self.wfile.write(b"data: first\\n\\n"); self.wfile.flush()
            end = time.monotonic() + 5
            while not (Path(sys.argv[2]).parent / "release-stream").exists() and time.monotonic() < end:
                time.sleep(0.01)
            self.wfile.write(b"data: second\\n\\n"); return
        body = b"API ready" if not self.path.startswith("/api") else b"authentication required"
        self.send_response(401 if self.path.startswith("/api") else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "session=protected; HttpOnly; SameSite=strict")
        self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        payload = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/api/update/restart":
            allowed = "admin=yes" in self.headers.get("Cookie", "")
            self.send_response(200 if allowed else 403); self.end_headers(); self.wfile.write(b"{}"); self.wfile.flush()
            if allowed:
                def drain():
                    end = time.monotonic() + 5
                    while not (Path(sys.argv[2]).parent / "release-stop").exists() and time.monotonic() < end:
                        time.sleep(0.01)
                    if Path(os.environ["STORY_AGENT_GATEWAY_STOP"]).exists(): os._exit(0)
                threading.Thread(target=drain, daemon=True).start()
            return
        body = json.dumps({"body": payload.decode(), "cookie": self.headers.get("Cookie"),
                           "host": self.headers.get("Host")}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''


@pytest.fixture
def entrance(tmp_path: Path):
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE)
    backend = Backend(tmp_path, tmp_path / "data",
                      command=lambda port: [sys.executable, str(fake), str(port), str(tmp_path / "starts")])
    server = Gateway(("127.0.0.1", 0), backend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        backend.close()
        thread.join(timeout=5)


def request(server: Gateway, path: str, method: str = "GET", headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def start_headers(server: Gateway) -> dict[str, str]:
    return {"Origin": f"http://127.0.0.1:{server.server_port}", "X-StoryAgent-Launch": server.backend.token}


def ready(backend: Backend) -> None:
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if backend.state() == "ready":
            return
        time.sleep(0.02)
    pytest.fail(f"backend did not start: {backend.state()}")


def test_deep_link_survives_backend_exit_and_can_start_it_again(entrance: Gateway) -> None:
    code, headers, body = request(entrance, "/projects/7/workflow")
    assert code == 200 and b"/__launcher/app.js" in body and headers["Cache-Control"] == "no-store"
    code, _, body = request(entrance, "/__launcher/boot")
    boot = json.loads(body)
    assert code == 200 and "gateway.start" in boot["locales"]["zh-CN"]
    assert request(entrance, "/api/projects")[0] == 503
    assert request(entrance, "/__launcher/start", "POST", start_headers(entrance))[0] == 202
    ready(entrance.backend)
    assert request(entrance, "/projects/7/workflow")[2] == b"API ready"
    first = entrance.backend.child
    with pytest.raises(http.client.RemoteDisconnected):
        direct = http.client.HTTPConnection("127.0.0.1", entrance.backend.port, timeout=2)
        try:
            direct.request("GET", "/exit")
            direct.getresponse()
        finally:
            direct.close()
    assert first is not None
    first.wait(timeout=3)
    assert json.loads(request(entrance, "/__launcher/status")[2])["state"] == "failed"
    assert b"/__launcher/app.js" in request(entrance, "/projects/7/workflow")[2]
    assert request(entrance, "/__launcher/start", "POST", start_headers(entrance))[0] == 202
    ready(entrance.backend)
    assert entrance.backend.child is not first


def test_start_is_same_origin_token_protected_and_idempotent(entrance: Gateway) -> None:
    for headers in ({}, {"Origin": "https://evil.example", "X-StoryAgent-Launch": entrance.backend.token},
                    {"Origin": f"http://127.0.0.1:{entrance.server_port}"},
                    {**start_headers(entrance), "Host": "evil.example"}):
        assert request(entrance, "/__launcher/start", "POST", headers)[0] == 403
    assert request(entrance, "/__launcher/start")[0] == 404
    assert request(entrance, "/__launcher/start", "POST", start_headers(entrance), b"run anything")[0] == 400
    assert entrance.backend.child is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda _: request(entrance, "/__launcher/start", "POST",
                                                start_headers(entrance))[0], range(4)))
    assert codes == [202] * 4
    ready(entrance.backend)
    assert len((entrance.backend.root / "starts").read_text().splitlines()) == 1


def test_proxy_keeps_auth_uploads_and_streaming(entrance: Gateway) -> None:
    entrance.backend.start()
    ready(entrance.backend)
    code, headers, body = request(entrance, "/api/private")
    assert code == 401 and body == b"authentication required" and "HttpOnly" in headers["Set-Cookie"]
    payload = "完整原曲".encode() * 50000
    code, _, body = request(entrance, "/api/upload", "POST", {"Cookie": "session=abc"}, payload)
    echoed = json.loads(body)
    assert code == 200 and echoed["body"].encode() == payload and echoed["cookie"] == "session=abc"
    assert echoed["host"] == f"127.0.0.1:{entrance.server_port}"
    conn = http.client.HTTPConnection("127.0.0.1", entrance.server_port, timeout=3)
    try:
        conn.request("GET", "/api/events")
        response = conn.getresponse()
        assert response.readline() == b"data: first\n"  # arrives while the backend is still streaming
        (entrance.backend.root / "release-stream").touch()
        assert response.read().endswith(b"data: second\n\n")
    finally:
        conn.close()


def test_gateway_never_exposes_process_controls_to_the_lan(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        Gateway(("0.0.0.0", 0), Backend(tmp_path, tmp_path))


def test_login_installation_is_idle_and_preserves_absolute_argv(tmp_path: Path, monkeypatch) -> None:
    import plistlib

    from storyagent.gateway import install_login

    def no_process(*args, **kwargs):
        pytest.fail("preparing login startup must not restart any running service")

    monkeypatch.setattr("subprocess.Popen", no_process)
    root, home = tmp_path / "repo with spaces", tmp_path / "my data"
    path = install_login(root, home, "127.0.0.1", 8765, directory=tmp_path / "agents")
    spec = plistlib.loads(path.read_bytes())
    assert spec["ProgramArguments"][0] == str(root / ".venv/bin/python")
    assert spec["ProgramArguments"][-1] == "--no-auto-start"
    assert spec["EnvironmentVariables"]["STORY_AGENT_HOME"] == str(home)
    assert spec["RunAtLoad"] and spec["KeepAlive"] and spec["WorkingDirectory"] == str(root)


def test_exit_uses_admin_authorization_and_drains_before_stopping(entrance: Gateway) -> None:
    entrance.backend.start()
    ready(entrance.backend)
    assert request(entrance, "/__launcher/stop", "POST", start_headers(entrance))[0] == 403
    assert not entrance.backend.stop_file.exists()  # denied users cannot even affect a concurrent restart
    headers = {**start_headers(entrance), "Cookie": "admin=yes"}
    code, _, body = request(entrance, "/__launcher/stop", "POST", headers)
    assert code == 202 and json.loads(body)["state"] == "stopping"
    assert entrance.backend.child is not None and entrance.backend.child.poll() is None
    (entrance.backend.root / "release-stop").touch()
    entrance.backend.child.wait(timeout=3)
    assert entrance.backend.state() == "stopped"
    assert request(entrance, "/__launcher/")[0] == 200  # entrance remains available after the API/worker exit
    assert request(entrance, "/__launcher/start", "POST", start_headers(entrance))[0] == 202
    ready(entrance.backend)


def test_login_button_checks_api_admin_before_writing_configuration(entrance: Gateway, monkeypatch) -> None:
    entrance.backend.start()
    ready(entrance.backend)
    installed = []
    monkeypatch.setattr("storyagent.gateway.sys.platform", "darwin")
    monkeypatch.setattr("storyagent.gateway.install_login", lambda *args: installed.append(args))
    assert request(entrance, "/__launcher/install-login", "POST", start_headers(entrance))[0] == 403
    assert installed == []
    headers = {**start_headers(entrance), "Cookie": "admin=yes"}
    assert request(entrance, "/__launcher/install-login", "POST", headers)[0] == 200
    assert installed == [(entrance.backend.root, entrance.backend.home, "127.0.0.1", entrance.server_port)]
