"""What the guide may look at on the machine: programs on PATH, the output of a read-only command, a service answer.

Everything goes through a ``Probe`` so the guide can be tested against a made-up machine (an empty home, a PATH with a
few stub programs, canned command output) and so the detection code has one place that touches the real one. A probe
only reads: it never installs, starts, stops or downloads anything.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

HttpGet = Callable[..., Any]


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str = ""


@dataclass(frozen=True)
class MachineInfo:
    arch: str
    system: str
    macos: str = ""            # "14.5" ... (macOS only)
    memory_gib: float = 0.0


def _real_run(argv: Sequence[str]) -> RunResult | None:
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True, timeout=20, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None
    return RunResult(proc.returncode, proc.stdout)


def _real_machine() -> MachineInfo:
    memory = 0.0
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5,  # noqa: S603, S607
                             check=False).stdout.strip()
        memory = int(out) / 2**30 if out.isdigit() else 0.0
    except (OSError, subprocess.SubprocessError):
        memory = 0.0
    return MachineInfo(arch=platform.machine(), system=platform.system(), macos=platform.mac_ver()[0],
                       memory_gib=round(memory, 1))


@dataclass
class Probe:
    """``path`` is the PATH searched for programs; ``environ`` the environment variables the guide may read."""

    path: str = field(default_factory=lambda: os.environ.get("PATH", ""))
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    run: Callable[[Sequence[str]], RunResult | None] = _real_run
    machine: Callable[[], MachineInfo] = _real_machine
    http_get: HttpGet = requests.get
    free_gib: Callable[[Path], float] = lambda p: shutil.disk_usage(p).free / 2**30

    def which(self, name: str) -> Path | None:
        found = shutil.which(name, path=self.path)
        return Path(found) if found else None

    def free_at(self, path: Path) -> float:
        """Free disk space (GiB) where ``path`` is or would be: the nearest folder that exists."""
        probe_dir = next((p for p in (path, *path.parents) if p.exists()), Path("."))
        try:
            return float(self.free_gib(probe_dir))
        except OSError:
            return 0.0
