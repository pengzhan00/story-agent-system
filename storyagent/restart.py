"""Hot restart: how the server, the worker and ``scripts/start.sh`` agree on a restart without any of them signalling
another process.

* ``RESTART_EXIT_CODE`` (75, EX_TEMPFAIL): a worker or server that ends with it is asking the supervisor to start both
  again. ``scripts/start.sh`` is that supervisor; every other exit code stops everything, as before. The number is
  written in the script too (``tests/test_update.py`` keeps the two equal).
* ``<home>/update/supervisor.json``: written by ``start.sh`` (``storyagent update marker``) with its pid. A process is
  *supervised* when that pid is its parent: only then is a restart offered, because only then something starts it again.
* ``restart.json`` (the server's request) and ``restart-worker.json`` (the worker's answer) are small files in the same
  folder. The worker stops claiming work, finishes what it is doing (``wait``) or hands it back at once (``now``), says
  ``stopped`` and exits 75; the server sees that, lets the browser notice, and exits 75 too. Nothing here ends another
  process: each one leaves by itself.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from storyagent.config import Settings

log = logging.getLogger(__name__)

RESTART_EXIT_CODE = 75
RestartMode = Literal["wait", "now"]
MODES: tuple[RestartMode, ...] = ("wait", "now")
SERVER_GRACE_SEC = 4.0          # after the worker has stopped the server stays up this long, so the page can notice
WORKER_HARD_EXIT_SEC = 20.0     # an interrupt-now restart: the worker leaves after this even if a thread still runs


def update_dir(settings: Settings) -> Path:
    return settings.home / "update"


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# -- the supervisor ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Supervisor:
    pid: int
    root: str
    started_at: float


def supervisor_path(settings: Settings) -> Path:
    return update_dir(settings) / "supervisor.json"


def write_supervisor(settings: Settings, pid: int, root: Path, *, now: float | None = None) -> Supervisor:
    sup = Supervisor(pid=pid, root=str(root), started_at=time.time() if now is None else now)
    _write(supervisor_path(settings), asdict(sup))
    return sup


def clear_supervisor(settings: Settings, pid: int | None = None) -> None:
    """Remove the marker (only if it is this supervisor's, when ``pid`` is given)."""
    path = supervisor_path(settings)
    data = _read(path)
    if data is not None and pid is not None and data.get("pid") != pid:
        return
    path.unlink(missing_ok=True)


def read_supervisor(settings: Settings) -> Supervisor | None:
    data = _read(supervisor_path(settings))
    try:
        if not data:
            return None
        return Supervisor(int(data["pid"]), str(data.get("root", "")), float(data.get("started_at", 0)))
    except (KeyError, TypeError, ValueError):
        return None


def supervised(settings: Settings, parent_pid: int | None = None, root: Path | None = None) -> bool:
    """True when the supervisor in the marker started this process (it is our parent) for this checkout."""
    sup = read_supervisor(settings)
    if sup is None:
        return False
    if root is not None and sup.root and Path(sup.root).resolve() != root.resolve():
        return False
    return sup.pid == (os.getppid() if parent_pid is None else parent_pid)


# -- the request (server -> worker) and the answer (worker -> server) -------------------------------------
@dataclass(frozen=True)
class RestartRequest:
    id: str
    mode: RestartMode
    at: float
    by: str = ""


@dataclass(frozen=True)
class WorkerAnswer:
    id: str
    state: Literal["draining", "stopped"]
    running: int
    at: float


def _request_path(settings: Settings) -> Path:
    return update_dir(settings) / "restart.json"


def _answer_path(settings: Settings) -> Path:
    return update_dir(settings) / "restart-worker.json"


def write_request(settings: Settings, request: RestartRequest) -> None:
    _answer_path(settings).unlink(missing_ok=True)
    _write(_request_path(settings), asdict(request))


def read_request(settings: Settings) -> RestartRequest | None:
    data = _read(_request_path(settings))
    try:
        if not data or data.get("mode") not in MODES:
            return None
        return RestartRequest(str(data["id"]), data["mode"], float(data["at"]), str(data.get("by", "")))
    except (KeyError, TypeError, ValueError):
        return None


def clear_request(settings: Settings) -> None:
    _request_path(settings).unlink(missing_ok=True)
    _answer_path(settings).unlink(missing_ok=True)


def write_answer(settings: Settings, answer: WorkerAnswer) -> None:
    _write(_answer_path(settings), asdict(answer))


def read_answer(settings: Settings) -> WorkerAnswer | None:
    data = _read(_answer_path(settings))
    try:
        if not data or data.get("state") not in ("draining", "stopped"):
            return None
        return WorkerAnswer(str(data["id"]), data["state"], int(data.get("running", 0)), float(data.get("at", 0)))
    except (KeyError, TypeError, ValueError):
        return None


def phase_of(request: RestartRequest | None, answer: WorkerAnswer | None) -> str:
    """``none`` | ``requested`` (the worker has not answered yet) | ``draining`` | ``restarting``."""
    if request is None:
        return "none"
    if answer is None or answer.id != request.id:
        return "requested"
    return "restarting" if answer.state == "stopped" else "draining"


# -- the worker side ------------------------------------------------------------------------------------
class Pausable(Protocol):
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def busy(self) -> int: ...


class WorkerGate:
    """Called about once a second by the worker process: reads the request, stops/resumes claiming, answers.

    ``poll()`` is True when the worker should leave now (to exit with ``RESTART_EXIT_CODE``). A request older than the
    worker itself is a leftover of a restart that already happened and is ignored."""

    def __init__(self, settings: Settings, worker: Pausable, *, clock: Callable[[], float] = time.time) -> None:
        self.settings, self.worker, self.clock = settings, worker, clock
        self.started = clock()
        self._paused = False
        self.leaving = False               # True once poll() has said the worker should go
        self.mode: RestartMode = "wait"

    def poll(self) -> bool:
        request = read_request(self.settings)
        if request is None or request.at < self.started:
            if self._paused:
                self.worker.resume()
                self._paused = False
                _answer_path(self.settings).unlink(missing_ok=True)
                log.info("restart cancelled: claiming work again")
            return False
        if not self._paused:
            self.worker.pause()
            self._paused = True
            log.info("restart requested (%s): no new work is claimed", request.mode)
        running = self.worker.busy()
        self.mode = request.mode
        if request.mode == "wait" and running:
            write_answer(self.settings, WorkerAnswer(request.id, "draining", running, self.clock()))
            return False
        write_answer(self.settings, WorkerAnswer(request.id, "stopped", running, self.clock()))
        self.leaving = True
        return True


def exit_now(code: int = RESTART_EXIT_CODE) -> None:
    """Leave at once; threads still running are abandoned (their leases expire and the work is requeued)."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def watch_worker(
    gate: WorkerGate, stop: threading.Event, *, interval: float = 1.0,
    hard_exit: Callable[[], None] = exit_now, hard_exit_sec: float = WORKER_HARD_EXIT_SEC,
) -> threading.Thread:
    """Poll the gate in a thread; when it says leave, set ``stop`` (the worker loop ends and shuts down) and arm a
    timer that ends the process anyway. The caller exits with ``RESTART_EXIT_CODE`` after the loop returns."""
    def loop() -> None:
        while not stop.is_set():
            try:
                if gate.poll():
                    stop.set()
                    timer = threading.Timer(hard_exit_sec, hard_exit)
                    timer.daemon = True
                    timer.start()
                    return
            except Exception:
                log.exception("restart check failed")
            stop.wait(interval)

    t = threading.Thread(target=loop, daemon=True, name="restart-watch")
    t.start()
    return t


# -- the server side ------------------------------------------------------------------------------------
def server_should_leave(settings: Settings, request: RestartRequest | None = None) -> bool:
    """True once the worker has said it stopped for the current request."""
    request = request or read_request(settings)
    answer = read_answer(settings)
    return phase_of(request, answer) == "restarting"
