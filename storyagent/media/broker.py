"""Scheduling policy: which queued job may start now. Pure functions, no I/O.

Rules (in order): the machine as a whole must have the job's memory free plus a headroom (the operator's other
apps count; ``available_gib`` comes from the caller); at most ``max_concurrent_jobs`` local jobs in all (1 = a
linear queue, 0 = no overall cap); at most one heavy job at a time; a cap on concurrent light jobs; a memory budget
(a job that alone exceeds the budget still runs when nothing else does, so it can't deadlock);
and, when ``llm_unload_policy = ask``, heavy jobs wait while any LLM is resident.
Blocked jobs do not hold up later ones (no head-of-line blocking).
"""
from __future__ import annotations

import logging
import platform
import re
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


_VM_PAGE = re.compile(r"page size of (\d+) bytes")
_VM_COUNT = re.compile(r"^Pages (free|inactive|speculative|purgeable):\s+(\d+)\.", re.MULTILINE)


def parse_vm_stat(text: str) -> float | None:
    """GiB the system could give a new job right now (free + inactive + speculative + purgeable pages), from the
    output of macOS ``vm_stat``; ``None`` when it cannot be read."""
    size = _VM_PAGE.search(text)
    counts = {k: int(v) for k, v in _VM_COUNT.findall(text)}
    if size is None or len(counts) < 4:
        return None
    return sum(counts.values()) * int(size.group(1)) / 2**30


def system_available_gib() -> float | None:
    """Memory the whole machine has left (other apps included); ``None`` off macOS or when ``vm_stat`` fails."""
    if platform.system() != "Darwin" or shutil.which("vm_stat") is None:
        return None
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5, check=False).stdout  # noqa: S603, S607
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_vm_stat(out)


_SWAP = re.compile(r"total = ([\d.]+)M\s+used = ([\d.]+)M")


def parse_swapusage(text: str) -> tuple[float, float] | None:
    """``(total GiB, used GiB)`` of the swap from macOS ``sysctl -n vm.swapusage``; ``None`` when unreadable."""
    found = _SWAP.search(text)
    return (float(found.group(1)) / 1024, float(found.group(2)) / 1024) if found else None


def _sysctl(name: str) -> str:
    if shutil.which("sysctl") is None:
        return ""
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5,  # noqa: S603, S607
                              check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def memory_snapshot() -> dict[str, float | None]:
    """Total, available and swap memory of the machine in GiB (macOS; every value ``None`` where it cannot be read)."""
    total = _sysctl("hw.memsize").strip()
    swap = parse_swapusage(_sysctl("vm.swapusage"))
    return {"total_gib": int(total) / 2**30 if total.isdigit() else None, "free_gib": system_available_gib(),
            "swap_total_gib": swap[0] if swap else None, "swap_used_gib": swap[1] if swap else None}


def _remote(job: JobRecord, settings: Settings) -> bool:
    model = settings.models.get(job.model_ref)
    return bool(model and model.remote)


def can_start(
    job: JobRecord,
    running: Sequence[JobRecord],
    settings: Settings,
    *,
    resident_llms: Sequence[str] = (),
    available_gib: float | None = None,
) -> Gate:
    if _remote(job, settings):
        # a cloud job uses no local memory or GPU: it runs next to anything, up to its own limit
        if sum(1 for r in running if _remote(r, settings)) >= int(settings.runtime.get("max_remote_jobs", 4)):
            return Gate(False, WaitReason.LIGHT_LIMIT)
        return Gate(True)
    headroom = float(settings.runtime.get("memory_headroom_gib", 8))
    if available_gib is not None and job.est_memory_gib and job.est_memory_gib + headroom > available_gib:
        return Gate(False, WaitReason.SYSTEM_MEMORY)       # other apps hold the memory it needs: wait, do not swap
    cap = int(settings.runtime.get("max_concurrent_jobs", 0))
    if cap and sum(1 for r in running if not _remote(r, settings)) >= cap:       # a strictly linear queue (cap 1)
        return Gate(False, WaitReason.QUEUE_LIMIT)
    if job.heavy:
        if any(r.heavy for r in running):
            return Gate(False, WaitReason.HEAVY_BUSY)
    else:
        light = sum(1 for r in running if not r.heavy and not _remote(r, settings))
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
    available_gib: float | None = None,
) -> tuple[JobRecord | None, dict[str, WaitReason]]:
    """First queued job (already in priority order) that may start, plus why each earlier one waits."""
    waiting: dict[str, WaitReason] = {}
    for job in queued:
        gate = can_start(job, running, settings, resident_llms=resident_llms, available_gib=available_gib)
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
