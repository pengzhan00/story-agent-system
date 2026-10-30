"""CPU/I/O-only anchor extraction and exact-duration assembly for durable a2v graphs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..contracts import ArtifactSpec, Capabilities, JobContext, JobRecord, ValidationIssue
from ..probe import find_tool
from ._subprocess import Command, SubprocessAdapter

RUNNER = Path(__file__).resolve().parents[1] / "runners" / "control_tools.py"


class ControlToolsAdapter(SubprocessAdapter):
    provider = "control-tools"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.IMAGE_GENERATE, MediaKind.COMPOSE_ASSEMBLE),
            heavy=False,
            supports_reattach=False,
            poll_interval_sec=0.1,
        )

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        if find_tool(ctx.settings, "ffmpeg") is None:
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "ffmpeg is required", {"tool": "ffmpeg"})]
        if find_tool(ctx.settings, "ffprobe") is None:
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "ffprobe is required", {"tool": "ffprobe"})]
        if job.params.get("operation") not in {"anchor", "stitch"}:
            return [ValidationIssue(ErrorCode.INVALID_PARAMS, "Unknown control utility operation")]
        return []

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "operation": job.params["operation"],
            "inputs": job.inputs,
            "params": job.params,
            "ffmpeg": str(find_tool(ctx.settings, "ffmpeg")),
            "ffprobe": str(find_tool(ctx.settings, "ffprobe")),
            "out": str(ctx.work_dir),
            "service": ctx.settings.service("phosphene").url,
        }
        path = ctx.work_dir / "config.json"
        path.write_text(json.dumps(config))
        return Command([sys.executable, str(RUNNER), str(path)])

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        meta = json.loads((ctx.work_dir / "result.json").read_text())
        return [ArtifactSpec(ctx.work_dir / meta.pop("file"), metadata=meta)]
