"""Base for adapters that run a local command-line tool (CosyVoice, whisper, h3.c, LongCat, mflux-train ...).

A subclass says *what to run* (``command``) and *what came out* (``collect``); this class owns the process:
start it in its own session with output going to a log file, report progress, cancel it, and turn a bad exit
into an ``AdapterError`` carrying the tail of the log. Processes are children of the worker, so they are not
re-attachable: after a worker crash the job is simply re-submitted (and the abandoned child finishes harmlessly
into a scratch directory that is removed).
"""
from __future__ import annotations

import os
import subprocess
import threading
import uuid
from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from storyagent.domain.errors import ErrorCode

from ..contracts import AdapterBase, AdapterError, ArtifactSpec, JobContext, JobRecord, Progress

LOG_NAME = "tool.log"
TAIL_CHARS = 1200


@dataclass(frozen=True)
class Command:
    argv: list[str]
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)  # added to the worker's environment


class SubprocessAdapter(AdapterBase):
    poll_interval_sec = 2.0

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._lock = threading.Lock()

    # -- to implement -------------------------------------------------------------------------
    @abstractmethod
    def command(self, job: JobRecord, ctx: JobContext) -> Command: ...

    @abstractmethod
    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]: ...

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        """(fraction 0..1, message) from the log so far; default: indeterminate."""
        return 0.0, ""

    # -- contract -----------------------------------------------------------------------------
    @staticmethod
    def log_path(ctx: JobContext) -> Path:
        return ctx.work_dir / LOG_NAME

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        cmd = self.command(job, ctx)
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        log = self.log_path(ctx).open("ab")
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
                cmd.argv, cwd=cmd.cwd, env={**os.environ, **cmd.env}, stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise AdapterError(ErrorCode.MISSING_TOOL, f"cannot start {cmd.argv[0]}", tool=str(cmd.argv[0])) from exc
        except OSError as exc:
            raise AdapterError(ErrorCode.TOOL_FAILED, str(exc), exit_code=-1) from exc
        finally:
            log.close()
        ext = f"proc:{uuid.uuid4().hex}"
        with self._lock:
            self._procs[ext] = proc
        ctx.log("process_started", argv0=str(cmd.argv[0]), pid=proc.pid)
        return ext

    def _tail(self, ctx: JobContext) -> str:
        try:
            return self.log_path(ctx).read_text("utf-8", errors="replace")[-TAIL_CHARS:]
        except OSError:
            return ""

    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress:
        with self._lock:
            proc = self._procs.get(external_id)
        if proc is None:
            raise AdapterError(ErrorCode.WORKER_LOST, "the tool process belongs to a previous worker")
        rc = proc.poll()
        if rc is None:
            frac, msg = self.progress(job, self._tail(ctx))
            return Progress("running", min(frac, 0.99), msg)
        with self._lock:
            self._procs.pop(external_id, None)
        if rc == 0:
            return Progress("succeeded", 1.0, "done")
        tail = self._tail(ctx)
        return Progress("failed", error=AdapterError(
            ErrorCode.TOOL_FAILED, tail[-400:] or f"exit code {rc}", exit_code=rc, detail=tail[-400:]))

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        return self.collect(job, ctx)

    def cancel(self, external_id: str, job: JobRecord, ctx: JobContext) -> None:
        with self._lock:
            proc = self._procs.pop(external_id, None)
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()  # our own child only
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
