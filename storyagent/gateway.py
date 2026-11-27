"""Loopback web entrance that survives an API/worker failure.

Only this checkout's supervisor can be started, on an explicit same-origin request. The API remains responsible
for its own authentication; proxying never replaces it. No model service or media job is started by the gateway.
"""
from __future__ import annotations

import argparse
import hmac
import http.client
import json
import os
import plistlib
import secrets
import signal
import socket
import subprocess
import sys
import threading
import tomllib
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ASSETS = Path(__file__).with_name("gateway_web")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade"}
LOGIN_LABEL = "com.storyagent.gateway"


def install_login(root: Path, home: Path, host: str, port: int, *, directory: Path | None = None) -> Path:
    """Prepare login startup without loading/stopping any running service. launchd reads this at next login."""
    directory = directory or Path.home() / "Library/LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True)
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = {"STORY_AGENT_HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")}
    if os.environ.get("STORY_AGENT_CONFIG_DIR"):
        env["STORY_AGENT_CONFIG_DIR"] = str(Path(os.environ["STORY_AGENT_CONFIG_DIR"]).expanduser().resolve())
    spec = {
        "Label": LOGIN_LABEL,
        "ProgramArguments": [str(root / ".venv/bin/python"), "-m", "storyagent.gateway", "--host", host,
                             "--port", str(port), "--no-auto-start"],
        "WorkingDirectory": str(root), "EnvironmentVariables": env,
        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 15,
        "StandardOutPath": str(logs / "gateway.log"), "StandardErrorPath": str(logs / "gateway.log"),
    }
    path = directory / f"{LOGIN_LABEL}.plist"
    temporary = path.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(spec))
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def _message(key: str, **params: Any) -> str:
    language = os.environ.get("STORY_AGENT_LOCALE", "zh-CN")
    if language not in {"en", "zh-CN"}:
        language = "en"
    table = json.loads((ASSETS.parent / "locales" / f"{language}.json").read_text())
    return str(table[key]).format(**params)


class Backend:
    def __init__(self, root: Path, home: Path, *, command: Callable[[int], Sequence[str]] | None = None) -> None:
        self.root, self.home = root, home
        self.command = command or (lambda port: ["bash", str(root / "scripts/start.sh"), "--backend-only",
                                                "--host", "127.0.0.1", "--port", str(port)])
        self.child: subprocess.Popen[bytes] | None = None
        self.port = 0
        self.attempted = False
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.stopping = False
        self.stop_file = home / "run" / f"gateway-stop-{self.token[:12]}"

    def start(self) -> None:
        with self.lock:
            if self.child is not None and self.child.poll() is None:
                return
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                self.port = sock.getsockname()[1]
            self.attempted = True
            self.stopping = False
            self.stop_file.unlink(missing_ok=True)
            logs = self.home / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / "backend-launch.log").open("ab") as log:
                self.child = subprocess.Popen(
                    self.command(self.port), cwd=self.root, stdout=log, stderr=log, start_new_session=True,
                    env={**os.environ, "STORY_AGENT_HOME": str(self.home),
                         "STORY_AGENT_GATEWAY_STOP": str(self.stop_file)})

    def request_stop(self, headers: dict[str, str]) -> int:
        """Reuse the API's admin check and wait-for-work restart handshake, then exit instead of restarting."""
        with self.lock:
            if self.child is None or self.child.poll() is not None:
                return 200
            if self.stopping:
                return 202
            authorized = self.authorize(headers)
            if authorized >= 300:
                return authorized
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
            try:
                # The API authenticates the browser cookie/token and verifies admin rights. Never bypass it.
                forwarded = {k: v for k, v in headers.items()
                             if k.lower() in {"cookie", "authorization", "origin", "host"}}
                # Do not touch the supervisor's exit marker until the existing admin boundary approved this user.
                self.stop_file.parent.mkdir(parents=True, exist_ok=True)
                self.stop_file.touch(mode=0o600)
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
                forwarded["Content-Type"] = "application/json"
                conn.request("POST", "/api/update/restart", b'{"interrupt":false}', forwarded)
                response = conn.getresponse()
                if response.status >= 300:
                    self.stop_file.unlink(missing_ok=True)
                    return response.status
                self.stopping = True
                return 202
            except (OSError, http.client.HTTPException):
                self.stop_file.unlink(missing_ok=True)
                return 502
            finally:
                conn.close()

    def authorize(self, headers: dict[str, str]) -> int:
        if not self.port:
            return 503
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request("GET", "/api/update", headers={k: v for k, v in headers.items()
                         if k.lower() in {"cookie", "authorization", "origin", "host"}})
            return conn.getresponse().status
        except (OSError, http.client.HTTPException):
            return 502
        finally:
            conn.close()

    def cancel_stop(self) -> None:
        with self.lock:
            self.stopping = False
            self.stop_file.unlink(missing_ok=True)

    def state(self) -> str:
        with self.lock:
            if self.stopping and not self.stop_file.exists():
                self.stopping = False
            if self.child is None or self.child.poll() is not None:
                return "failed" if self.attempted and not self.stopping else "stopped"
            if self.stopping:
                return "stopping"
            port = self.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        try:
            conn.request("GET", "/")
            response = conn.getresponse()
            return "ready" if response.status < 500 else "starting"
        except (OSError, http.client.HTTPException):
            return "starting"
        finally:
            conn.close()

    def close(self) -> None:
        with self.lock:
            child = self.child
            if child is None or child.poll() is not None:
                return
            # Only the new session we created, never a process discovered by its port or name.
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


class Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], backend: Backend) -> None:
        if address[0] not in {"127.0.0.1", "localhost"}:
            raise ValueError("the startup gateway must listen on loopback; use --backend-only for a LAN API")
        self.backend = backend
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: Gateway

    def log_message(self, format: str, *args: Any) -> None:
        pass  # backend log and state describe failures; never log cookies or startup tokens

    def _local(self) -> bool:
        port = self.server.server_port
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        if port == 80:
            hosts |= {"localhost", "127.0.0.1"}
        return self.client_address[0] == "127.0.0.1" and self.headers.get("Host", "") in hosts

    def _send(self, code: int, data: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, code: int, data: dict[str, Any]) -> None:
        self._send(code, json.dumps(data).encode(), "application/json")

    def _control(self) -> None:
        path = self.path.split("?", 1)[0]
        if self.command == "POST" and path in {"/__launcher/start", "/__launcher/stop", "/__launcher/install-login"}:
            origin = self.headers.get("Origin", "")
            expected = f"http://{self.headers.get('Host', '')}"
            token = self.headers.get("X-StoryAgent-Launch", "")
            if origin != expected or not hmac.compare_digest(token, self.server.backend.token):
                self._json(403, {"error": "forbidden"})
            elif self.headers.get("Content-Length", "0") != "0" or self.headers.get("Transfer-Encoding"):
                self._json(400, {"error": "unexpected_body"})
            else:
                try:
                    if path.endswith("/install-login"):
                        code = self.server.backend.authorize(dict(self.headers.items()))
                        if code >= 300:
                            self._json(code, {"error": "forbidden"})
                        elif sys.platform != "darwin":
                            self._json(400, {"error": "unsupported"})
                        else:
                            install_login(self.server.backend.root, self.server.backend.home,
                                          "127.0.0.1", self.server.server_port)
                            self._json(200, {"installed": True})
                    elif path.endswith("/stop"):
                        code = self.server.backend.request_stop(dict(self.headers.items()))
                        self._json(code, {"state": self.server.backend.state()})
                    else:
                        self.server.backend.start()
                        self._json(202, {"state": self.server.backend.state()})
                except OSError:
                    self._json(500, {"error": "start_failed"})
            return
        if self.command not in {"GET", "HEAD"}:
            self._json(405, {"error": "method_not_allowed"})
        elif path == "/__launcher/":
            self._send(200, (ASSETS / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/__launcher/status":
            self._json(200, {"state": self.server.backend.state()})
        elif path == "/__launcher/boot":
            locales = {}
            for name in ("en", "zh-CN"):
                table = json.loads((ASSETS.parent / "locales" / f"{name}.json").read_text())
                locales[name] = {k: v for k, v in table.items() if k.startswith("gateway.")}
            self._json(200, {"token": self.server.backend.token, "locales": locales,
                             "login_supported": sys.platform == "darwin",
                             "login_installed": (Path.home() / "Library/LaunchAgents" /
                                                 f"{LOGIN_LABEL}.plist").exists()})
        elif path in {"/__launcher/app.js", "/__launcher/style.css"}:
            name = "app.js" if path.endswith(".js") else "style.css"
            self._send(200, (ASSETS / name).read_bytes(),
                       "text/javascript" if name.endswith(".js") else "text/css")
        else:
            self._json(404, {"error": "not_found"})

    def _handle(self) -> None:
        if not self._local() or not self.path.startswith("/") or self.path.startswith("//"):
            self._json(403, {"error": "forbidden"})
        elif self.path.startswith("/__launcher/"):
            self._control()
        else:
            self._proxy()

    def _proxy(self) -> None:
        backend = self.server.backend
        conn = http.client.HTTPConnection("127.0.0.1", backend.port, timeout=35)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP | {"x-storyagent-launch"}}
        started = False
        try:
            if not backend.port or backend.child is None or backend.child.poll() is not None:
                raise ConnectionRefusedError
            if self.headers.get("Transfer-Encoding"):
                self._json(411, {"error": "content_length_required"})
                return
            remaining = int(self.headers.get("Content-Length", "0"))
            if remaining < 0:
                raise ValueError
            conn.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            for key, value in headers.items():
                conn.putheader(key, value)
            conn.endheaders()
            while remaining:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    return
                conn.send(chunk)
                remaining -= len(chunk)
            response = conn.getresponse()
            if self.command == "DELETE" and self.path.split("?", 1)[0] == "/api/update/restart" \
                    and 200 <= response.status < 300:
                backend.cancel_stop()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP:
                    self.send_header(key, value)
            self.end_headers()
            started = True
            if self.command != "HEAD":
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()  # SSE must reach the browser before the stream ends
        except ValueError:
            if not started:
                self._json(400, {"error": "bad_request"})
        except (OSError, http.client.HTTPException):
            if not started:
                if self.command in {"GET", "HEAD"} and not self.path.startswith("/api/"):
                    self._send(200, (ASSETS / "index.html").read_bytes(), "text/html; charset=utf-8")
                else:
                    self._json(503, {"error": "backend_unavailable"})
        finally:
            conn.close()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _handle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-auto-start", action="store_true")
    parser.add_argument("--install-login", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    home = Path(os.environ.get("STORY_AGENT_HOME", str(root / "data"))).expanduser().resolve()
    config = Path(os.environ.get("STORY_AGENT_CONFIG_DIR", str(home / "config"))).expanduser()
    try:
        # A broken model/LLM configuration must not take down the startup page itself.
        api = tomllib.loads((config / "services.toml").read_text()).get("api", {})
    except (OSError, ValueError):
        api = {}
    host = args.host or api.get("host", "127.0.0.1")
    port = args.port if args.port is not None else int(api.get("port", 8765))
    if args.install_login:
        if sys.platform != "darwin":
            parser.error("login installation is supported on macOS only")
        if host not in {"127.0.0.1", "localhost"}:
            parser.error("the startup gateway must listen on loopback")
        print(_message("gateway.cli.installed", path=install_login(root, home, host, port)), flush=True)
        return
    backend = Backend(root, home)
    server = Gateway((host, port), backend)  # bind before launching anything; no duplicate supervisor
    def terminate(*_: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        if not args.no_auto_start:
            backend.start()
        print(_message("gateway.cli.entrance", url=f"http://{host}:{server.server_port}"), flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        backend.close()


if __name__ == "__main__":
    main()
