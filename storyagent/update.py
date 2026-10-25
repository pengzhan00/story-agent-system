"""Update this checkout from its remote, step by step, and ask for a hot restart.

These are the deploy steps, and the single source of them: ``scripts/deploy.sh`` just runs ``storyagent update run``.
They run as argv lists (never through a shell), each line of output going to ``<home>/logs/update-<id>.log``:

    fetch -> compare -> merge (fast-forward only) -> install -> web_install -> web_build -> migrate -> i18n

The first step that fails stops the run: nothing restarts and the old code keeps running (a failure after ``merge``
leaves the checkout on the new commit; the run records the commit it started from). Only when every step passed, and
``scripts/start.sh`` supervises the processes, a restart is requested (``storyagent.restart``): the worker finishes its
running work (or hands it back at once, on an explicit choice), then worker and server leave with exit code 75 and the
script starts them again.

Guards: ``[update] enabled`` (``auto`` = the production checkout: the main work tree, on the update branch), a clean
work tree, and one update at a time. Who may call it (admins) is decided by the API. All commands go through a
``Runner``, so tests use a made-up one.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import storyagent
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.restart import (
    MODES,
    RestartMode,
    RestartRequest,
    clear_request,
    phase_of,
    read_answer,
    read_request,
    supervised,
    update_dir,
    write_request,
)
from storyagent.util import new_id

log = logging.getLogger(__name__)

STEP_IDS = ("fetch", "compare", "merge", "install", "web_install", "web_build", "migrate", "i18n")
RUN_STATES = ("running", "succeeded", "current", "failed", "interrupted")
INSPECT_TTL_SEC = 5.0
LOG_TAIL_LINES = 80
DETAIL_LINES = 6
COMMIT_LIST_LIMIT = 100
_SEP = "\x1f"


class UpdateError(Exception):
    """A refused or failed update request; ``code`` is the API error code, ``status`` its HTTP status."""

    def __init__(self, code: str, status: int = 409, detail: str = "") -> None:
        super().__init__(code)
        self.code, self.status, self.detail = code, status, detail


# -- running commands ------------------------------------------------------------------------------------
class Runner(Protocol):
    def run(self, argv: Sequence[str], cwd: Path, on_line: Callable[[str], None], timeout: float) -> int: ...


class SubprocessRunner:
    """Runs a command without a shell, streaming its output (stdout and stderr together) line by line."""

    env: Mapping[str, str] = {"GIT_TERMINAL_PROMPT": "0", "CI": "1"}      # never wait for a password / a question

    def run(self, argv: Sequence[str], cwd: Path, on_line: Callable[[str], None], timeout: float) -> int:
        try:
            proc = subprocess.Popen(
                list(argv), cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1, env={**os.environ, **self.env})
        except OSError as exc:
            on_line(str(exc))
            return 127
        timed_out = threading.Event()

        def expire() -> None:
            timed_out.set()
            proc.terminate()                    # our own child; nothing else is ever signalled

        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                on_line(line.rstrip("\n"))
            code = proc.wait()
        finally:
            timer.cancel()
        if timed_out.is_set():
            on_line(f"timeout after {timeout:.0f}s")
            return 124
        return code


# -- data -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Commit:
    sha: str
    short: str
    subject: str


@dataclass
class StepState:
    id: str
    status: str = "pending"                 # pending | running | ok | failed | skipped
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    note: str = ""                          # i18n key of a short explanation ("" = none)
    detail: str = ""                        # the last lines of the tool's output when it failed


@dataclass
class RunState:
    id: str
    status: str
    by: str
    started_at: float
    boot_id: str
    steps: list[StepState]
    finished_at: float | None = None
    before: Commit | None = None
    after: Commit | None = None
    behind: list[Commit] = field(default_factory=list)
    failed_step: str = ""
    log_file: str = ""
    restart: str = "none"                   # none | requested | unsupervised | declined
    restart_mode: str = ""


@dataclass(frozen=True)
class Availability:
    enabled: bool
    reason: str = ""                        # "" | off | not_git | linked_worktree | branch | no_remote


@dataclass(frozen=True)
class Inspection:
    available: Availability
    current: Commit | None
    branch: str
    clean: bool
    dirty: list[str]
    behind: list[Commit]
    behind_count: int
    ahead_count: int
    remote_ref: str
    remote_known: bool
    tools_missing: list[str]
    checked_at: float | None               # the last successful fetch


@dataclass(frozen=True)
class RunningWork:
    jobs: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    queued: int

    @property
    def count(self) -> int:
        return len(self.jobs) + len(self.runs)


@dataclass(frozen=True)
class RestartInfo:
    supervised: bool
    phase: str                              # none | requested | draining | restarting
    mode: str
    requested_at: float | None
    running: int


@dataclass(frozen=True)
class Snapshot:
    inspection: Inspection
    run: RunState | None
    log_tail: list[str]
    restart: RestartInfo
    boot_id: str
    started_at: float


def repo_root() -> Path:
    return Path(storyagent.__file__).resolve().parents[1]


def running_work(db: Database) -> RunningWork:
    """What a restart would interrupt: media jobs and workflow runs being worked on, and how many wait."""
    conn = db.conn
    jobs = [
        {"id": r["id"], "kind": r["kind"], "model": r["model_ref"], "heavy": bool(r["heavy"]),
         "project_id": r["project_id"], "progress": float(r["progress"] or 0), "started_at": r["started_at"]}
        for r in conn.execute(
            "SELECT id, kind, model_ref, heavy, project_id, progress, started_at FROM media_jobs "
            "WHERE status='running' ORDER BY started_at")
    ]
    runs = [
        {"id": r["id"], "project_id": r["project_id"], "started_at": r["started_at"]}
        for r in conn.execute("SELECT id, project_id, started_at FROM workflow_runs WHERE status='running'")
    ]
    queued = conn.execute("SELECT COUNT(*) FROM media_jobs WHERE status='queued'").fetchone()[0]
    queued += conn.execute("SELECT COUNT(*) FROM workflow_runs WHERE status='queued'").fetchone()[0]
    return RunningWork(jobs, runs, int(queued))


def _commit(text: str) -> Commit | None:
    parts = text.strip().split(_SEP)
    return Commit(parts[0], parts[1], parts[2] if len(parts) > 2 else "") if len(parts) >= 2 and parts[0] else None


class Updater:
    """One per server process. Thread-safe: the run happens in a background thread, status calls read files."""

    def __init__(
        self, settings: Settings, *, root: Path | None = None, runner: Runner | None = None,
        python: str | None = None, which: Callable[[str], str | None] = shutil.which,
        clock: Callable[[], float] = time.time, parent_pid: int | None = None,
        audit: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.settings, self.cfg = settings, settings.update
        self.root = (root or repo_root()).resolve()
        self.runner: Runner = runner or SubprocessRunner()
        self.python = python or sys.executable
        self.which, self.clock, self.parent_pid = which, clock, parent_pid
        self.audit = audit or (lambda _action, _by, _detail: None)
        self.boot_id = new_id()[:12]
        self.started_at = clock()
        self._lock = threading.Lock()                    # held for the whole run: one update at a time
        self._thread: threading.Thread | None = None
        self._cache: tuple[float, Inspection] | None = None
        self._run: RunState | None = self._load_run()
        if self._run is not None and self._run.status == "running" and self._run.boot_id != self.boot_id:
            self._run.status, self._run.finished_at = "interrupted", self.clock()      # the server died in the middle
            self._save_run()

    # -- files ----------------------------------------------------------------------------------------------
    @property
    def _state_path(self) -> Path:
        return update_dir(self.settings) / "state.json"

    def _load_run(self) -> RunState | None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8")).get("run")
        except (OSError, ValueError, AttributeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return RunState(
                id=data["id"], status=data["status"], by=data.get("by", ""), started_at=data["started_at"],
                boot_id=data.get("boot_id", ""), steps=[StepState(**s) for s in data.get("steps", [])],
                finished_at=data.get("finished_at"),
                before=Commit(**data["before"]) if data.get("before") else None,
                after=Commit(**data["after"]) if data.get("after") else None,
                behind=[Commit(**c) for c in data.get("behind", [])], failed_step=data.get("failed_step", ""),
                log_file=data.get("log_file", ""), restart=data.get("restart", "none"),
                restart_mode=data.get("restart_mode", ""))
        except (KeyError, TypeError):
            return None

    def _save_run(self) -> None:
        if self._run is None:
            return
        path = self._state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
        tmp.write_text(json.dumps({"run": asdict(self._run)}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # -- git ------------------------------------------------------------------------------------------------
    def _capture(self, argv: Sequence[str], timeout: float = 120.0) -> tuple[int, list[str]]:
        lines: list[str] = []
        code = self.runner.run(argv, self.root, lines.append, min(self.cfg.step_timeout_sec, timeout))
        return code, lines

    def _git(self, *args: str) -> tuple[int, list[str]]:
        return self._capture(["git", *args])

    @property
    def remote_ref(self) -> str:
        return f"{self.cfg.remote}/{self.cfg.branch}"

    def availability(self) -> Availability:
        if self.cfg.enabled == "off":
            return Availability(False, "off")
        code, out = self._git("rev-parse", "--git-dir", "--git-common-dir")
        if code != 0 or len(out) < 2:
            return Availability(False, "not_git")
        if self.cfg.enabled == "on":
            return Availability(True)
        git_dir, common = (self.root / out[0]).resolve(), (self.root / out[1]).resolve()
        if git_dir != common:
            return Availability(False, "linked_worktree")       # a development work tree, never the production one
        code, out = self._git("rev-parse", "--abbrev-ref", "HEAD")
        if code != 0 or not out or out[0].strip() != self.cfg.branch:
            return Availability(False, "branch")
        code, _ = self._git("remote", "get-url", self.cfg.remote)
        if code != 0:
            return Availability(False, "no_remote")
        return Availability(True)

    def inspect(self, *, force: bool = False) -> Inspection:
        """The checkout as it is (no network): current commit, work tree state, commits the remote ref is ahead by."""
        now = self.clock()
        if not force and self._cache is not None and now - self._cache[0] < INSPECT_TTL_SEC:
            return self._cache[1]
        available = self.availability()
        current: Commit | None = None
        branch, clean, dirty = "", True, []
        behind: list[Commit] = []
        behind_count = ahead_count = 0
        known = False
        if available.reason != "not_git":
            code, out = self._git("log", "-1", f"--format=%H{_SEP}%h{_SEP}%s")
            current = _commit(out[0]) if code == 0 and out else None
            code, out = self._git("rev-parse", "--abbrev-ref", "HEAD")
            branch = out[0].strip() if code == 0 and out else ""
            code, out = self._git("status", "--porcelain")
            dirty = [x for x in out if x.strip()] if code == 0 else []
            clean = not dirty
            ref = self.remote_ref
            code, _ = self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
            known = code == 0
            if known:
                code, out = self._git(
                    "log", "-n", str(COMMIT_LIST_LIMIT), f"--format=%H{_SEP}%h{_SEP}%s", f"HEAD..{ref}")
                behind = [c for c in (_commit(x) for x in out) if c] if code == 0 else []
                code, out = self._git("rev-list", "--count", f"HEAD..{ref}")
                behind_count = int(out[0]) if code == 0 and out and out[0].strip().isdigit() else len(behind)
                code, out = self._git("rev-list", "--count", f"{ref}..HEAD")
                ahead_count = int(out[0]) if code == 0 and out and out[0].strip().isdigit() else 0
        result = Inspection(
            available=available, current=current, branch=branch, clean=clean, dirty=dirty[:20], behind=behind,
            behind_count=behind_count, ahead_count=ahead_count, remote_ref=self.remote_ref, remote_known=known,
            tools_missing=self.missing_tools(), checked_at=self._last_fetch())
        self._cache = (now, result)
        return result

    def missing_tools(self) -> list[str]:
        needed = ["git", "uv"] + (["pnpm"] if (self.root / "web" / "package.json").is_file() else [])
        return [tool for tool in needed if self.which(tool) is None]

    def _last_fetch(self) -> float | None:
        try:
            value = json.loads((update_dir(self.settings) / "fetched.json").read_text(encoding="utf-8")).get("at")
        except (OSError, ValueError, AttributeError):
            return None
        return float(value) if isinstance(value, (int, float)) else None

    def _note_fetch(self) -> None:
        path = update_dir(self.settings) / "fetched.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": self.clock()}), encoding="utf-8")

    def check(self) -> Inspection:
        """``git fetch`` and then compare. Refused while an update runs (the fetch would race its own)."""
        if not self._lock.acquire(blocking=False):
            raise UpdateError("update_busy")
        try:
            available = self.availability()
            if not available.enabled:
                raise UpdateError("update_disabled", 403, available.reason)
            code, out = self._capture(["git", "fetch", self.cfg.remote], self.cfg.step_timeout_sec)
            if code != 0:
                raise UpdateError("update_fetch_failed", 502, "\n".join(out[-DETAIL_LINES:]))
            self._note_fetch()
            return self.inspect(force=True)
        finally:
            self._lock.release()

    # -- the run --------------------------------------------------------------------------------------------
    def snapshot(self) -> Snapshot:
        return Snapshot(self.inspect(), self._run, self._tail(), self.restart_info(), self.boot_id, self.started_at)

    @property
    def last_run(self) -> RunState | None:
        return self._run

    def busy(self) -> bool:
        return self._lock.locked()

    def start(self, *, by: str = "", restart: bool = True, mode: RestartMode = "wait") -> RunState:
        """Validate and begin an update in a background thread. Raises ``UpdateError`` when it must not run."""
        if mode not in MODES:
            raise UpdateError("invalid", 422, mode)
        if not self._lock.acquire(blocking=False):
            raise UpdateError("update_busy")
        try:
            info = self.inspect(force=True)
            if not info.available.enabled:
                raise UpdateError("update_disabled", 403, info.available.reason)
            if not info.clean:
                raise UpdateError("update_dirty", 409, "\n".join(info.dirty[:DETAIL_LINES]))
            if info.tools_missing:
                raise UpdateError("update_tool_missing", 424, ", ".join(info.tools_missing))
            run_id = self._new_run_id()
            log_path = self.settings.logs_dir / f"update-{run_id}.log"
            steps = [StepState(s) for s in STEP_IDS]
            run = RunState(
                id=run_id, status="running", by=by, started_at=self.clock(), boot_id=self.boot_id, steps=steps,
                before=info.current, log_file=str(log_path), restart_mode=mode if restart else "")
            self._run = run
            self._save_run()
        except BaseException:
            self._lock.release()
            raise
        self.audit("update.start", by, {
            "run": run.id, "from": run.before.short if run.before else "", "restart": restart, "mode": mode})
        self._thread = threading.Thread(
            target=self._execute, args=(run, restart, mode, by), daemon=True, name="update-run")
        self._thread.start()
        return run

    def _new_run_id(self) -> str:
        return time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock())) + "-" + new_id()[:4]

    def wait(self, timeout: float | None = None) -> None:
        """Test/CLI helper: block until the background run is over."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _tail(self) -> list[str]:
        run = self._run
        if run is None or not run.log_file:
            return []
        try:
            lines = Path(run.log_file).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return lines[-LOG_TAIL_LINES:]

    def _execute(self, run: RunState, restart: bool, mode: RestartMode, by: str) -> None:
        try:
            self._run_to_end(run, restart, mode, by)
        finally:
            self._lock.release()

    def _run_to_end(self, run: RunState, restart: bool, mode: RestartMode, by: str) -> None:
        log_path = Path(run.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("a", encoding="utf-8", buffering=1) as logfile:
                def out(line: str) -> None:
                    logfile.write(line + "\n")

                self._steps(run, out)
        except Exception as exc:                          # noqa: BLE001 - the run must always end in a state
            log.exception("update run crashed")
            run.status, run.failed_step = "failed", run.failed_step or next(
                (s.id for s in run.steps if s.status == "running"), "")
            for s in run.steps:
                if s.status == "running":
                    s.status, s.finished_at, s.detail = "failed", self.clock(), str(exc)
        run.finished_at = self.clock()
        if run.status == "succeeded" and restart:
            try:
                self._write_restart(mode, by)
                run.restart = "requested"
            except UpdateError as exc:
                run.restart = "unsupervised" if exc.code == "update_unsupervised" else "declined"
        self._cache = None
        self._save_run()
        self._prune_logs()
        self.audit("update.finish", by, {
            "run": run.id, "status": run.status, "failed_step": run.failed_step, "restart": run.restart,
            "from": run.before.short if run.before else "", "to": run.after.short if run.after else ""})

    def _steps(self, run: RunState, out: Callable[[str], None]) -> None:
        commands = self._commands()
        for step in run.steps:
            step.status, step.started_at = "running", self.clock()
            self._save_run()
            out(f"== {step.id}")
            if step.id in ("web_install", "web_build") and not (self.root / "web" / "package.json").is_file():
                self._finish_step(step, "skipped", note="update.note.no_web")
                continue
            if step.id == "compare":
                ok = self._compare(run, step, out)
            else:
                argv, cwd = commands[step.id]
                out("$ " + " ".join(argv))
                tail: list[str] = []

                def keep(line: str, tail: list[str] = tail) -> None:
                    out(line)
                    tail.append(line)

                code = self.runner.run(argv, cwd, keep, self.cfg.step_timeout_sec)
                step.exit_code = code
                ok = code == 0
                if ok:
                    self._finish_step(step, "ok")
                else:
                    self._finish_step(step, "failed", detail="\n".join([x for x in tail if x.strip()][-DETAIL_LINES:]))
                if ok and step.id == "merge":
                    _, head = self._git("log", "-1", f"--format=%H{_SEP}%h{_SEP}%s")
                    run.after = _commit(head[0]) if head else None
            if not ok:
                run.status, run.failed_step = "failed", step.id
                for rest in run.steps:
                    if rest.status == "pending":
                        rest.status = "skipped"
                self._save_run()
                out(f"== {step.id} failed - stopping here; nothing is restarted")
                return
            if run.status == "current":
                for rest in run.steps:
                    if rest.status == "pending":
                        rest.status = "skipped"
                self._save_run()
                return
            self._save_run()
        run.status = "succeeded"

    def _finish_step(self, step: StepState, status: str, *, note: str = "", detail: str = "") -> None:
        step.status, step.finished_at, step.note, step.detail = status, self.clock(), note, detail

    def _compare(self, run: RunState, step: StepState, out: Callable[[str], None]) -> bool:
        info = self.inspect(force=True)
        run.before = info.current
        run.behind = info.behind
        out(f"{info.remote_ref}: {info.behind_count} behind, {info.ahead_count} ahead")
        for c in info.behind:
            out(f"  {c.short} {c.subject}")
        if not info.remote_known:
            self._finish_step(step, "failed", note="update.note.no_remote_ref")
            return False
        if info.behind_count == 0:                      # unpushed local commits do not matter: nothing to take over
            self._finish_step(step, "ok", note="update.note.up_to_date")
            run.status = "current"
            run.after = info.current
            return True
        if info.ahead_count:
            self._finish_step(step, "failed", note="update.note.diverged")
            return False
        self._finish_step(step, "ok")
        return True

    def _commands(self) -> dict[str, tuple[list[str], Path]]:
        web = self.root / "web"
        py = self.python
        return {
            "fetch": (["git", "fetch", self.cfg.remote], self.root),
            "merge": (["git", "merge", "--ff-only", self.remote_ref], self.root),
            "install": (["uv", "pip", "install", "--python", py, "-e", "."], self.root),
            "web_install": (["pnpm", "install", "--frozen-lockfile"], web),
            "web_build": (["pnpm", "build"], web),
            "migrate": ([py, "-m", "storyagent", "db", "migrate"], self.root),
            "i18n": ([py, "-m", "storyagent", "i18n", "check"], self.root),
        }

    def _prune_logs(self) -> None:
        logs = sorted(self.settings.logs_dir.glob("update-*.log"))
        for old in logs[: max(len(logs) - self.cfg.keep_logs, 0)]:
            old.unlink(missing_ok=True)

    # -- restart --------------------------------------------------------------------------------------------
    def is_supervised(self) -> bool:
        return supervised(self.settings, self.parent_pid, self.root)

    def restart_info(self) -> RestartInfo:
        request, answer = read_request(self.settings), read_answer(self.settings)
        phase = phase_of(request, answer)
        return RestartInfo(
            supervised=self.is_supervised(), phase=phase, mode=request.mode if request else "",
            requested_at=request.at if request else None,
            running=answer.running if answer and request and answer.id == request.id else 0)

    def request_restart(self, mode: RestartMode = "wait", *, by: str = "") -> RestartRequest:
        """Ask the worker and the server to restart (see ``storyagent.restart``). Needs a supervisor."""
        if self._lock.locked():
            raise UpdateError("update_busy")
        return self._write_restart(mode, by)

    def _write_restart(self, mode: RestartMode, by: str) -> RestartRequest:
        if mode not in MODES:
            raise UpdateError("invalid", 422, mode)
        if not self.is_supervised():
            raise UpdateError("update_unsupervised", 409)
        request = RestartRequest(id=new_id()[:12], mode=mode, at=self.clock(), by=by)
        write_request(self.settings, request)
        self.audit("update.restart", by, {"mode": mode, "request": request.id})
        return request

    def cancel_restart(self, *, by: str = "") -> None:
        request = read_request(self.settings)
        if request is None:
            raise UpdateError("update_no_restart", 409)
        answer = read_answer(self.settings)
        if phase_of(request, answer) == "restarting":
            raise UpdateError("update_restarting", 409)           # the worker has already stopped
        clear_request(self.settings)
        self.audit("update.restart_cancel", by, {"request": request.id})

    def forget_restart(self) -> None:
        """A freshly started server: the request that brought it here is done."""
        clear_request(self.settings)
