"""New-machine install guide: detect what the pipeline needs, say what is missing and how to install it, write the
machine's configuration, verify. See ``docs/INSTALL.md``. Nothing is downloaded or started unless the operator asks."""
from .layout import Layout, SetupError
from .probe import MachineInfo, Probe, RunResult
from .report import Report, build_report

__all__ = ["Layout", "MachineInfo", "Probe", "Report", "RunResult", "SetupError", "build_report"]
