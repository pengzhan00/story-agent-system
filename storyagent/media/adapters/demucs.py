"""audio.separate through Demucs (htdemucs): split a mix into the vocals and the accompaniment.

The one tool for "take the vocals out of this recording": the clean vocal stem feeds voice conversion (Seed-VC),
voice cloning and lip-sync; the accompaniment feeds a karaoke or a re-mix. It is the same Demucs the Phosphene panel
uses for its a2v flow, run here as a first-class job so the workshop can call it on its own.

Inputs: ``audio`` (a file path; falls back to the first audio entry of ``refs``).
Params: ``model`` (``htdemucs`` by default, also ``htdemucs_ft`` / ``mdx_extra`` ...), ``device`` (``mps`` | ``cpu``).
Outputs: ``main`` = the vocals, ``accompaniment`` = everything else (Demucs' ``no_vocals.wav``).
"""
from __future__ import annotations

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
    "model": {"type": "str"},
    "device": {"type": "str", "choices": ["mps", "cpu"]},
}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".mp4", ".mov", ".mkv"}
DEFAULT_MODEL = "htdemucs"
DEFAULT_DEVICE = "mps"


def source_of(job: JobRecord, ctx: JobContext) -> Path | None:
    raw = job.inputs.get("audio")
    if not raw:
        refs = [r for r in job.inputs.get("refs", []) if isinstance(r, str) and Path(r).suffix.lower() in AUDIO_EXT]
        raw = refs[0] if refs else ""
    if not raw:
        return None
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else ctx.settings.home / p


class DemucsAdapter(SubprocessAdapter):
    provider = "demucs"

    def __init__(self, poll_interval_sec: float = 1.0) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.AUDIO_SEPARATE,), heavy=False, supports_cancel=True, supports_reattach=False,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    @staticmethod
    def _python(ctx: JobContext) -> Path | None:
        return ctx.settings.tool_path("seedvc", "python") or ctx.settings.tool_path("demucs", "python")

    def health(self) -> HealthReport:
        return HealthReport(HealthState.OK, "Demucs")

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        if self._python(ctx) is None:
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "Demucs runtime not found (the Seed-VC venv carries it)",
                                    {"tool": "demucs"})]
        src = source_of(job, ctx)
        if src is None or not src.is_file():
            return [ValidationIssue(ErrorCode.INVALID_INPUT, f"source audio not found: {src}", {"detail": "audio"})]
        return []

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        python = self._python(ctx)
        assert python is not None
        src = source_of(job, ctx)
        assert src is not None
        p = job.params
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        out = ctx.work_dir / "sep"
        return Command([str(python), "-m", "demucs.separate", "--two-stems=vocals",
                        "-n", str(p.get("model") or DEFAULT_MODEL),
                        "-d", str(p.get("device") or DEFAULT_DEVICE), "-o", str(out), str(src)])

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        stem = source_of(job, ctx)
        name = stem.stem if stem is not None else ""
        vocals = sorted((ctx.work_dir / "sep").rglob(f"{name}/vocals.wav"))
        rest = sorted((ctx.work_dir / "sep").rglob(f"{name}/no_vocals.wav"))
        if not vocals or not rest:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "Demucs wrote no vocals/no_vocals stems")
        model = str(job.params.get("model") or DEFAULT_MODEL)
        return [ArtifactSpec(vocals[0], metadata={"stem": "vocals", "model": model}),
                ArtifactSpec(rest[0], slot="accompaniment", metadata={"stem": "no_vocals", "model": model})]
