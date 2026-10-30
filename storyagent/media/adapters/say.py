"""audio.tts through the macOS ``say`` command: plain text to speech, no reference voice and no cloning.

The cloned voices (CosyVoice) are picky about the reference clip; when many lines come out wrong this adapter gives
every line a clean, consistent system voice instead.

Inputs: ``text`` (the line to speak).
Params: ``voice`` (a macOS voice name, e.g. ``Tingting``), ``rate`` (words per minute, 60-400).
Output: mono 22.05 kHz wav.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
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

SCHEMA: dict[str, dict[str, Any]] = {
    "voice": {"type": "str"},
    "rate": {"type": "int", "min": 60, "max": 400},
}
DEFAULT_VOICE = "Tingting"
DEFAULT_RATE = 180
SAY = "/usr/bin/say"
DATA_FORMAT = "LEI16@22050"


class SayAdapter(SubprocessAdapter):
    provider = "say"

    def __init__(self, poll_interval_sec: float = 1.0) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.AUDIO_TTS,), heavy=False, supports_cancel=True, supports_reattach=False,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    def health(self) -> HealthReport:
        return HealthReport(HealthState.OK, "macOS say") if self._binary() else \
            HealthReport(HealthState.MISSING_TOOL, "say not found")

    @staticmethod
    def _binary() -> str | None:
        return SAY if Path(SAY).is_file() else (shutil.which("say") or None)

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        if self._binary() is None:
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "the macOS say command is not available", {"tool": "say"})]
        if not str(job.inputs.get("text") or "").strip():
            return [ValidationIssue(ErrorCode.INVALID_INPUT, "text is empty", {"detail": "text"})]
        return []

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        binary = self._binary()
        assert binary is not None
        text = str(job.inputs.get("text") or "").strip()
        voice = str(job.params.get("voice") or DEFAULT_VOICE)
        rate = int(job.params.get("rate") or DEFAULT_RATE)
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        out = ctx.work_dir / "out.wav"
        # "--": a line that starts with "-" is the text to speak, never an option of say.
        return Command([binary, "-v", voice, "-r", str(rate), "-o", str(out), f"--data-format={DATA_FORMAT}", "--",
                        text])

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        out = ctx.work_dir / "out.wav"
        if not out.is_file() or out.stat().st_size == 0:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "say produced no audio")
        return [ArtifactSpec(out, metadata={"voice": str(job.params.get("voice") or DEFAULT_VOICE),
                                            "rate": int(job.params.get("rate") or DEFAULT_RATE)})]
