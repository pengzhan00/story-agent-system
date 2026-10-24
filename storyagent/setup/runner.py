"""Run one step of the guide, on the operator's explicit say-so.

The guide only prints commands. This is the one place that can run one: a single step, named by id, shown in full first
(the command, where it runs, and for a download its source, file and size) and run only with ``confirmed=True``. The
command is an argv list from the catalog: no shell, nothing is killed, and nothing else is started.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .layout import SetupError
from .probe import Probe
from .report import Report, StepView

Spawn = Callable[..., "subprocess.CompletedProcess[bytes]"]


def find_step(report: Report, step_id: str) -> StepView:
    for comp in report.components:
        for step in comp.steps:
            if step.id == step_id:
                return step
    raise SetupError("unknown_step", step_id)


def child_env(probe: Probe, step_env: Mapping[str, str] | None = None,
              extra_path: Sequence[Path] = ()) -> dict[str, str]:
    """The environment of a step: the probe's PATH plus ``extra_path`` (the folder ``uv tool install`` puts programs in,
    ``[tools.mflux] bin_dir``, may not be on PATH yet)."""
    env = dict(probe.environ)
    env["PATH"] = os.pathsep.join(p for p in (probe.path, *(str(d) for d in extra_path)) if p)
    env.update(step_env or {})
    return env


def run_step(report: Report, step_id: str, *, probe: Probe, service_up: Callable[[str], bool], confirmed: bool,
             extra_path: Sequence[Path] = (), spawn: Spawn | None = None) -> int:
    """Run ``step_id``; returns the command's exit code. Raises ``SetupError`` when the step may not run."""
    step = find_step(report, step_id)
    if step.error:
        raise SetupError(step.error, step.id)
    if not step.runnable or step.kind == "manual":
        raise SetupError("not_runnable", step.id)
    if step.needs_service and not service_up(step.needs_service):
        raise SetupError("service_down", step.needs_service)
    if not confirmed:
        raise SetupError("not_confirmed", step.id)
    env = child_env(probe, extra_path=extra_path)
    argv = list(step.argv)
    exe = argv[0]
    if "/" not in exe:
        found = _which(exe, env["PATH"])
        if found is None:
            raise SetupError("tool_missing", exe)
        argv[0] = found
    elif not Path(exe).is_file():
        raise SetupError("tool_missing", exe)
    run: Sequence[str] = argv
    cwd = Path(step.cwd) if step.cwd else None
    if cwd is not None and not cwd.is_dir():
        raise SetupError("cwd_missing", str(cwd))
    proc = (spawn or subprocess.run)(run, cwd=cwd, env=env, check=False)  # noqa: S603 - argv list, no shell
    return int(proc.returncode)


def _which(name: str, path: str) -> str | None:
    import shutil

    return shutil.which(name, path=path)
