"""Start the background services the pipeline needs, on the operator's explicit request.

``[launch.<service>]`` in ``services.toml`` says how (argv, cwd, env). Starting is detached (own session, output in
``<home>/logs/launch_<name>.log``) and recorded in ``<home>/run/launch_<name>.json``; the server and the worker never
depend on it. A service that is already answering is left alone, and one that is not startable here (no ``[launch]``
entry) is only reported. Stopping exists for one case: the operator clicks "stop" (or runs ``ops stop NAME``) on a
service whose ``[launch.<name>]`` says ``stop = "port"``; then the process of the operator's own user that listens on
that service's port gets SIGTERM (SIGKILL only with ``force``). Nothing else is ever stopped or killed.

State of a service: ``running`` (health probe answers) | ``starting`` (launched, within its timeout) |
``failed`` (launched, no answer in time or the process died) | ``stopped`` | ``disabled`` | ``probe_only``.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from storyagent.config import Settings
from storyagent.util import now_iso

HttpGet = Callable[..., Any]
_children: dict[str, subprocess.Popen[bytes]] = {}   # keeps the Popen objects of this process, so exits get reaped


class LaunchError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class ServiceState:
    name: str
    url: str
    state: str
    startable: bool
    bulk: bool
    stoppable: bool = False
    detail: str = ""
    hint_key: str = ""
    pid: int | None = None
    elapsed_sec: float | None = None
    timeout_sec: float | None = None
    log: str = ""


def _expand(raw: str) -> Path:
    return Path(raw).expanduser()


class Launcher:
    def __init__(self, settings: Settings, *, http_get: HttpGet = requests.get) -> None:
        self.settings = settings
        self.http_get = http_get

    # -- files ---------------------------------------------------------------------------------
    def _record_path(self, name: str) -> Path:
        return self.settings.home / "run" / f"launch_{name}.json"

    def _log_path(self, name: str) -> Path:
        return self.settings.logs_dir / f"launch_{name}.log"

    def _record(self, name: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._record_path(name).read_text("utf-8"))  # type: ignore[no-any-return]
        except (OSError, ValueError):
            return None

    # -- probing -------------------------------------------------------------------------------
    def _answering(self, name: str) -> tuple[bool, str]:
        svc = self.settings.service(name)
        try:
            r = self.http_get(svc.health_url, timeout=3)
        except requests.RequestException as exc:
            return False, type(exc).__name__
        return r.status_code < 400, f"HTTP {r.status_code}"

    @staticmethod
    def _alive(name: str, pid: int | None) -> bool:
        child = _children.get(name)
        if child is not None and child.pid == pid:
            return child.poll() is None
        if not pid:
            return False
        try:
            out = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True,
                                 timeout=5, check=False).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return False
        return bool(out) and not out.startswith("Z")

    # -- state ---------------------------------------------------------------------------------
    def status(self, name: str) -> ServiceState:
        svc = self.settings.service(name)
        spec = self.settings.launch.get(name)
        base: dict[str, Any] = {"name": name, "url": svc.url, "startable": spec is not None,
                "bulk": bool(spec and spec["bulk"]), "hint_key": spec["hint_key"] if spec else "",
                "stoppable": bool(spec and spec["stop"] == "port"),
                "log": str(self._log_path(name)) if spec else ""}
        if not svc.enabled:
            return ServiceState(state="disabled", **base)
        ok, detail = self._answering(name)
        if ok:
            return ServiceState(state="probe_only" if svc.probe_only else "running", detail=detail, **base)
        rec = self._record(name) if spec else None
        if not spec or rec is None:
            return ServiceState(state="stopped", detail=detail, **base)
        elapsed = max(time.time() - float(rec.get("started_epoch", 0)), 0.0)
        timeout = float(spec["timeout_sec"])
        pid = rec.get("pid")
        alive = spec["launcher"] or self._alive(name, pid)
        state = "starting" if alive and elapsed < timeout else "failed"
        return ServiceState(state=state, detail=detail, pid=pid, elapsed_sec=round(elapsed, 1),
                            timeout_sec=timeout, **base)

    def statuses(self) -> list[ServiceState]:
        return [self.status(n) for n in self.settings.services]

    # -- start ---------------------------------------------------------------------------------
    def _resolve_argv(self, spec: Mapping[str, Any], cwd: Path) -> list[str]:
        first = spec["argv"][0]
        exe: str | None
        if "/" in first or first.startswith("~"):
            path = _expand(first)
            exe = str(path if path.is_absolute() else cwd / path)
            if not Path(exe).is_file():
                exe = None
        else:
            exe = shutil.which(first)
        if exe is None:
            raise LaunchError("launch_missing", first)
        return [exe, *[str(_expand(a)) if a.startswith("~") else a for a in spec["argv"][1:]]]

    def start(self, name: str) -> ServiceState:
        """Start ``name`` unless it answers already or is being started. Raises ``LaunchError``."""
        if name not in self.settings.services:
            raise LaunchError("launch_unknown", name)
        spec = self.settings.launch.get(name)
        if spec is None:
            raise LaunchError("launch_not_startable", name)
        current = self.status(name)
        if current.state in ("running", "starting", "probe_only", "disabled"):
            return current
        cwd = _expand(spec["cwd"]) if spec["cwd"] else self.settings.home
        if not cwd.is_dir():
            raise LaunchError("launch_missing", str(cwd))
        argv = self._resolve_argv(spec, cwd)
        env = {**os.environ, **spec["env"]}
        self._log_path(name).parent.mkdir(parents=True, exist_ok=True)
        with self._log_path(name).open("ab") as log:
            log.write(f"\n--- {now_iso()} start {name}: {' '.join(argv)}\n".encode())
            log.flush()
            try:
                child = subprocess.Popen(  # noqa: S603 - argv list from the operator's own config, no shell
                    argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
            except OSError as exc:
                raise LaunchError("launch_failed", str(exc)) from exc
        _children[name] = child
        rec = {"pid": child.pid, "started_at": now_iso(), "started_epoch": time.time(), "argv": argv}
        self._record_path(name).parent.mkdir(parents=True, exist_ok=True)
        self._record_path(name).write_text(json.dumps(rec), "utf-8")
        return self.status(name)

    def start_all(self) -> list[tuple[str, str, str]]:
        """Start every ``bulk`` service that is not up. Returns ``(name, outcome, detail)``; one failure does not
        stop the others."""
        results: list[tuple[str, str, str]] = []
        for name, spec in self.settings.launch.items():
            if not spec["bulk"]:
                continue
            try:
                before = self.status(name).state
                after = self.start(name).state
                results.append((name, "started" if before in ("stopped", "failed") else "already", after))
            except LaunchError as exc:
                results.append((name, exc.code, exc.detail))
        return results


    # -- stop ----------------------------------------------------------------------------------
    def listeners(self, name: str) -> list[tuple[int, str]]:
        """``(pid, command)`` of the processes listening on the port of service ``name`` (lsof, no shell)."""
        port = urlparse(self.settings.service(name).url).port
        if port is None or shutil.which("lsof") is None:
            return []
        try:
            out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"], capture_output=True,
                                 text=True, timeout=10, check=False).stdout  # noqa: S603, S607
        except (OSError, subprocess.SubprocessError):
            return []
        found: list[tuple[int, str]] = []
        pid = 0
        for line in out.splitlines():
            if line.startswith("p") and line[1:].isdigit():
                pid = int(line[1:])
            elif line.startswith("c") and pid and all(pid != p for p, _ in found):
                found.append((pid, line[1:]))
        return found

    @staticmethod
    def _owned(pid: int) -> bool:
        """True when the process belongs to this user (another user's process is never signalled). ``ps`` is not
        available everywhere - a restricted sandbox denies /bin/ps - and ``lsof`` reports the uid as well, so it is
        the fallback."""
        try:
            out = subprocess.run(["ps", "-p", str(pid), "-o", "uid="], capture_output=True, text=True, timeout=5,
                                 check=False).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out.isdigit():
            return int(out) == os.getuid()
        if shutil.which("lsof") is None:
            return False
        try:
            lines = subprocess.run(["lsof", "-nP", "-p", str(pid), "-F", "u"], capture_output=True, text=True,
                                   timeout=5, check=False).stdout.splitlines()  # noqa: S603, S607
        except (OSError, subprocess.SubprocessError):
            return False
        uid = next((line[1:] for line in lines if line.startswith("u")), "")
        return uid.isdigit() and int(uid) == os.getuid()

    @staticmethod
    def _signal(pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)       # the one place a process is signalled: operator-requested stop of a listed service
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            raise LaunchError("launch_foreign", str(pid)) from exc

    def stop(self, name: str, *, force: bool = False, wait_sec: float = 15.0) -> ServiceState:
        """End the operator's own process that listens on the service's port (needs ``stop = "port"``). SIGTERM, or
        SIGKILL with ``force``; raises ``launch_stop_timeout`` when it is still there after ``wait_sec``."""
        if name not in self.settings.services:
            raise LaunchError("launch_unknown", name)
        spec = self.settings.launch.get(name)
        if spec is None or spec["stop"] != "port":
            raise LaunchError("launch_not_stoppable", name)
        found = self.listeners(name)
        if not found:
            raise LaunchError("launch_not_running", name)
        foreign = [pid for pid, _ in found if not self._owned(pid)]
        if foreign:
            raise LaunchError("launch_foreign", ", ".join(str(p) for p in foreign))
        for pid, _ in found:
            self._signal(pid, signal.SIGKILL if force else signal.SIGTERM)
        deadline = time.monotonic() + wait_sec
        while time.monotonic() < deadline:
            if not self.listeners(name):
                break
            time.sleep(0.5)
        else:
            raise LaunchError("launch_stop_timeout", ", ".join(f"{p} {c}" for p, c in self.listeners(name)))
        self._record_path(name).unlink(missing_ok=True)
        _children.pop(name, None)
        return self.status(name)
