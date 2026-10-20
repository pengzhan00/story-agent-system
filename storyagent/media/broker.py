"""Scheduling policy: which queued job may start now. Pure functions, no I/O.

Rules (in order): at most one heavy job at a time; a cap on concurrent light jobs; a memory budget
(a job that alone exceeds the budget still runs when nothing else does, so it can't deadlock);
and, when ``llm_unload_policy = ask``, heavy jobs wait while any LLM is resident.
Blocked jobs do not hold up later ones (no head-of-line blocking).
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType

from storyagent.config import Settings
from storyagent.domain.errors import WaitReason

from .contracts import JobRecord

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Gate:
    allowed: bool
    reason: WaitReason | None = None


def can_start(
    job: JobRecord,
    running: Sequence[JobRecord],
    settings: Settings,
    *,
    resident_llms: Sequence[str] = (),
) -> Gate:
    if job.heavy:
        if any(r.heavy for r in running):
            return Gate(False, WaitReason.HEAVY_BUSY)
    else:
        light = sum(1 for r in running if not r.heavy)
        if light >= int(settings.runtime.get("max_light_jobs", 2)):
            return Gate(False, WaitReason.LIGHT_LIMIT)
    budget = float(settings.runtime.get("memory_budget_gib", 0))
    if budget and running and sum(r.est_memory_gib for r in running) + job.est_memory_gib > budget:
        return Gate(False, WaitReason.MEMORY_BUDGET)
    if job.heavy and settings.llm_unload_policy == "ask" and resident_llms:
        return Gate(False, WaitReason.LLM_RESIDENT)
    return Gate(True)


def select_next(
    queued: Sequence[JobRecord],
    running: Sequence[JobRecord],
    settings: Settings,
    *,
    resident_llms: Sequence[str] = (),
) -> tuple[JobRecord | None, dict[str, WaitReason]]:
    """First queued job (already in priority order) that may start, plus why each earlier one waits."""
    waiting: dict[str, WaitReason] = {}
    for job in queued:
        gate = can_start(job, running, settings, resident_llms=resident_llms)
        if gate.allowed:
            return job, waiting
        assert gate.reason is not None
        waiting[job.id] = gate.reason
    return None, waiting


class CaffeinateGuard:
    """Keep macOS awake while this process lives (``caffeinate -i -w <pid>``). No-op elsewhere or if disabled."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._proc: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> CaffeinateGuard:
        import os

        exe = shutil.which("caffeinate") if self.enabled and platform.system() == "Darwin" else None
        if exe:
            try:
                self._proc = subprocess.Popen(  # noqa: S603 - fixed argv
                    [exe, "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except OSError:
                log.warning("caffeinate could not be started; the machine may sleep during long jobs")
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None

    @property
    def active(self) -> bool:
        return self._proc is not None
