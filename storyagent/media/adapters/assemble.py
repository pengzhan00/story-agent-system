"""compose.assemble: render an episode from a timeline with ffmpeg (runner script in a subprocess).

Inputs:  ``timeline`` (see ``storyagent/compose/timeline.py``; every ``asset`` is an ``asset:<id>`` reference that the
         worker has already turned into a file path)
Params:  burn_subtitles (default true; needs an ffmpeg with libass - without it the SRT is still delivered)
Output:  ``episode.mp4`` (main) and ``episode.srt`` (slot ``srt``); the report (warnings, burned or not) is in the
         metadata. Progress is one step per shot plus three (video, audio, final).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..contracts import AdapterError, ArtifactSpec, Capabilities, JobContext, JobRecord, ValidationIssue
from ..probe import find_tool
from ._subprocess import Command, SubprocessAdapter

SCHEMA: dict[str, dict[str, Any]] = {"burn_subtitles": {"type": "bool"}}
RUNNER = Path(__file__).resolve().parents[1] / "runners" / "assemble_episode.py"
_STEP = re.compile(r'"event": "step", "done": (\d+), "total": (\d+), "msg": "([^"]*)"')


def _with_paths(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**i, "path": i["asset"]} for i in items if i.get("asset")]


def has_libass(ffmpeg: str) -> bool:
    """Whether this ffmpeg can burn subtitles in (the ``subtitles`` filter needs libass)."""
    proc = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, check=False)  # noqa: S603
    return any(line.split()[1:2] == ["subtitles"] for line in proc.stdout.splitlines())


class AssembleAdapter(SubprocessAdapter):
    provider = "ffmpeg"

    def __init__(self, poll_interval_sec: float = 1.0) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.COMPOSE_ASSEMBLE,), heavy=False, supports_cancel=True, supports_reattach=False,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        if find_tool(ctx.settings, "ffmpeg") is None:
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "ffmpeg not found", {"tool": "ffmpeg"})]
        tl = job.inputs.get("timeline")
        if not isinstance(tl, dict) or not tl.get("segments"):
            return [ValidationIssue(ErrorCode.INVALID_INPUT, "a timeline with at least one segment is required",
                                    {"detail": "timeline"})]
        ffmpeg = find_tool(ctx.settings, "ffmpeg")
        if tl.get("label") and ffmpeg is not None and not has_libass(str(ffmpeg)):
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "the visible AIGC label needs an ffmpeg with libass",
                                    {"tool": "ffmpeg-full"})]
        generated = ("black", "card")                       # made by ffmpeg itself, no file behind them
        missing = [s["shot_id"] for s in tl["segments"]
                   if s["source"]["type"] not in generated and not Path(str(s["source"].get("asset", ""))).is_file()]
        if missing:
            return [ValidationIssue(ErrorCode.INVALID_INPUT, f"picture file missing for shots {missing}",
                                    {"detail": f"shots {missing}"})]
        return []

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        tl = json.loads(json.dumps(job.inputs["timeline"]))
        for seg in tl["segments"]:
            if seg["source"].get("asset"):
                seg["source"]["path"] = seg["source"]["asset"]
        for key in ("speech", "sfx", "music"):
            tl[key] = _with_paths(tl[key])
        ffmpeg = find_tool(ctx.settings, "ffmpeg")
        assert ffmpeg is not None
        cfg = {"ffmpeg": str(ffmpeg), "timeline": tl, "out_dir": str(ctx.work_dir / "out"),
               "burn_subtitles": bool(job.params.get("burn_subtitles", True))}
        (ctx.work_dir / "out").mkdir(parents=True, exist_ok=True)
        (ctx.work_dir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
        return super().submit(job, ctx)

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        return Command([sys.executable, str(RUNNER), str(ctx.work_dir / "config.json")])

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        hits = _STEP.findall(log_text)
        if not hits:
            return 0.01, "starting"
        done, total, msg = hits[-1]
        return int(done) / max(int(total), 1), msg

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        out = ctx.work_dir / "out"
        mp4 = out / "episode.mp4"
        if not mp4.is_file():
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "the assembler finished without an episode file")
        report: dict[str, Any] = {}
        if (out / "report.json").is_file():
            report = json.loads((out / "report.json").read_text("utf-8"))
        artifacts = [ArtifactSpec(mp4, metadata=report)]
        if (out / "episode.srt").is_file() and (out / "episode.srt").stat().st_size:
            artifacts.append(ArtifactSpec(out / "episode.srt", slot="srt", metadata={"cues": True}))
        return artifacts
