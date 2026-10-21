"""Start the background services the pipeline needs, on the operator's explicit request.

``[launch.<service>]`` in ``services.toml`` says how (argv, cwd, env). Starting is detached (own session, output in
``<home>/logs/launch_<name>.log``) and recorded in ``<home>/run/launch_<name>.json``; the server and the worker never
depend on it. This module never stops, restarts or kills anything: a service that is already answering is left alone,
and one that is not startable here (no ``[launch]`` entry) is only reported.

State of a service: ``running`` (health probe answers) | ``starting`` (launched, within its timeout) |
``failed`` (launched, no answer in time or the process died) | ``stopped`` | ``disabled`` | ``probe_only``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
