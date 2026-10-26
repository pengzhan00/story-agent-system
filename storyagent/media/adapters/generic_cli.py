"""Run any command-line tool as a model: ``provider = "cli"`` and a ``[models.X.connector]`` table.

    [models."my-tts"]
    kind = "audio.tts"
    provider = "cli"
    [models."my-tts".connector]
    argv = ["~/tools/mytts/bin/mytts", "--text", "{text}", ["--voice", "{ref}"], ["--seed", "{seed}"], "--out", "{out}"]
    cwd = "~/tools/mytts"                 # optional
    env = { HF_HUB_OFFLINE = "1" }       # optional, added to the worker's environment
    stdin = "{text}"                     # optional: written to the tool's standard input, then closed
    ext = "wav"                          # what the tool writes (default: wav for audio, mp4 for video, png)
    output = "{out}"                     # the file to take; a glob (``*.wav``) is searched in the work folder
    progress_regex = "(\\d+)%"           # optional: the last match of group 1 in the log is the percent

The placeholders are those of ``connect/template.py``; ``{out}`` is a path in the job's work folder and ``{work}``
the folder. A nested list is an optional group, dropped when a value in it is missing. The tool runs in its own
session with its output in a log, is cancelled by the job's cancel, and a bad exit shows the tail of the log.
"""
from __future__ import annotations

import glob
import re
import shutil
from pathlib import Path
from typing import Any

from storyagent.config import ConfigError, Settings
from storyagent.connect.spec import GENERIC_KINDS, connector_models, connector_of
from storyagent.connect.template import Missing, job_values, render_argv, render_string
from storyagent.domain.errors import ErrorCode

from ..contracts import (
    AdapterError,
    ArtifactSpec,
    Capabilities,
    HealthReport,
    HealthState,
    JobContext,
    JobRecord,
    ValidationIssue,
)
from ._subprocess import Command, SubprocessAdapter

DEFAULT_EXT = {"audio": "wav", "video": "mp4", "image": "png"}


class CliAdapter(SubprocessAdapter):
    provider = "cli"

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    def capabilities(self) -> Capabilities:
        return Capabilities(kinds=GENERIC_KINDS, heavy=False, supports_cancel=True, supports_reattach=False,
                            poll_interval_sec=self.poll_interval_sec)

    # -- reading the connector ----------------------------------------------------------------
    def _spec(self, job: JobRecord, ctx: JobContext) -> dict[str, Any]:
        try:
            return dict(connector_of(ctx.settings, job.model_ref, self.provider))
        except ConfigError as exc:
            raise AdapterError(ErrorCode.INVALID_PARAMS, str(exc), detail=str(exc)) from exc

    @staticmethod
    def _ext(job: JobRecord, spec: dict[str, Any]) -> str:
        return str(spec.get("ext") or DEFAULT_EXT.get(job.kind.value.split(".")[0], "bin")).lstrip(".")

    def _values(self, job: JobRecord, ctx: JobContext, spec: dict[str, Any]) -> dict[str, Any]:
        out = ctx.work_dir / f"output.{self._ext(job, spec)}"
        return job_values(job.inputs, job.params, job.seed, ctx.settings.home,
                          {"out": str(out), "work": str(ctx.work_dir)})

    def _argv(self, job: JobRecord, ctx: JobContext, spec: dict[str, Any]) -> list[str]:
        raw = spec.get("argv")
        if not isinstance(raw, list) or not raw:
            raise AdapterError(ErrorCode.INVALID_PARAMS, "connector.argv must be a non-empty list",
                               detail="connector.argv")
        try:
            argv = render_argv(raw, self._values(job, ctx, spec))
        except Missing as exc:
            raise AdapterError(ErrorCode.INVALID_INPUT, f"the command needs a value for {{{exc.name}}}",
                               detail=exc.name) from exc
        argv[0] = str(Path(argv[0]).expanduser())
        return argv

    # -- contract -----------------------------------------------------------------------------
    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        try:
            spec = self._spec(job, ctx)
            argv = self._argv(job, ctx, spec)
        except AdapterError as exc:
            return [ValidationIssue(exc.code, exc.message, exc.params)]
        cwd = self._cwd(spec)
        exe = argv[0]
        found = shutil.which(exe) if "/" not in exe else (exe if Path(exe).is_file() else None)
        if found is None and cwd is not None and "/" in exe and (cwd / exe).is_file():
            found = str(cwd / exe)
        if found is None:
            return [ValidationIssue(ErrorCode.MISSING_TOOL, f"cannot find {exe}", {"tool": exe})]
        return []

    @staticmethod
    def _cwd(spec: dict[str, Any]) -> Path | None:
        raw = str(spec.get("cwd") or "")
        return Path(raw).expanduser() if raw else None

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        spec = self._spec(job, ctx)
        env = {str(k): str(render_string(str(v), self._values(job, ctx, spec))) for k, v in
               (spec.get("env") or {}).items()}
        values = self._values(job, ctx, spec)
        stdin = None
        if spec.get("stdin") is not None:
            try:
                stdin = str(render_string(str(spec["stdin"]), values))
            except Missing as exc:
                raise AdapterError(ErrorCode.INVALID_INPUT, f"the tool needs a value for {{{exc.name}}}",
                                   detail=exc.name) from exc
        return Command(self._argv(job, ctx, spec), self._cwd(spec), env, stdin)

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        pattern = self._progress_regex(job)
        if pattern is None:
            return 0.0, ""
        matches = list(pattern.finditer(log_text))
        if not matches:
            return 0.0, ""
        try:
            value = float(matches[-1].group(1))
        except (IndexError, ValueError):
            return 0.0, ""
        return (value / 100 if value > 1 else value), f"{value:g}"

    def _progress_regex(self, job: JobRecord) -> re.Pattern[str] | None:
        raw = ""
        try:
            raw = str(self._settings.model(job.model_ref).extra.get("connector", {}).get("progress_regex") or "")
        except (ConfigError, AttributeError):
            return None
        try:
            return re.compile(raw) if raw else None
        except re.error:
            return None

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        spec = self._spec(job, ctx)
        values = self._values(job, ctx, spec)
        outputs = spec.get("outputs")
        if outputs is not None and (not isinstance(outputs, dict) or not outputs):
            raise AdapterError(ErrorCode.INVALID_PARAMS, "connector.outputs must be a non-empty slot/path table",
                               detail="connector.outputs")
        patterns = outputs if outputs is not None else {job.slot: spec.get("output") or "{out}"}
        artifacts = []
        for slot, raw in patterns.items():
            if not isinstance(slot, str) or not slot or not isinstance(raw, str) or not raw:
                raise AdapterError(ErrorCode.INVALID_PARAMS, "connector.outputs needs non-empty strings",
                                   detail="connector.outputs")
            try:
                pattern = str(render_string(raw, values))
            except Missing as exc:
                raise AdapterError(ErrorCode.INVALID_INPUT, f"missing output placeholder: {exc.name}",
                                   detail=exc.name) from exc
            found = self._find_output(pattern, ctx)
            artifacts.append(ArtifactSpec(
                found, slot=slot, metadata={"connector": "cli", "argv0": Path(str(spec["argv"][0])).name,
                                           "prompt": str(job.inputs.get("prompt", ""))[:500]}))
        return artifacts

    @staticmethod
    def _find_output(pattern: str, ctx: JobContext) -> Path:
        p = Path(pattern).expanduser()
        if not p.is_absolute():
            p = ctx.work_dir / p
        if any(c in pattern for c in "*?["):
            hits = sorted((Path(h) for h in glob.glob(str(p)) if Path(h).is_file()),
                          key=lambda x: x.stat().st_mtime)
            found = hits[-1] if hits else None
        else:
            found = p if p.is_file() else None
        if found is None or found.stat().st_size == 0:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, f"the tool finished but wrote no file at {pattern}",
                               detail=pattern)
        return found

    def health(self) -> HealthReport:
        missing: list[str] = []
        for ref in connector_models(self._settings, self.provider):
            spec = self._settings.model(ref).extra.get("connector")
            first = (spec or {}).get("argv", [None])[0] if isinstance(spec, dict) else None
            if not isinstance(first, str) or "{" in first:
                continue
            exe = str(Path(first).expanduser())
            ok = shutil.which(exe) if "/" not in exe else Path(exe).is_file()
            if not ok:
                missing.append(f"{ref}: {first}")
        if missing:
            return HealthReport(HealthState.DEGRADED, "", tuple(missing))
        return HealthReport(HealthState.OK)

