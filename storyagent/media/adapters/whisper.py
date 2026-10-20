"""text.asr through whisper.cpp: transcript text + SRT with timestamps.

Inputs: ``audio`` (a file path; falls back to the first audio entry of ``refs``).
Params: ``language`` (``zh``, ``en`` ... or ``auto``).
Artifacts: ``main`` = transcript.txt, ``srt`` = transcript.srt.
"""
from __future__ import annotations

from pathlib import Path

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..asr import find_whisper, to_wav16k, whisper_argv, whisper_model
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

SCHEMA = {"language": {"type": "str"}}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".mp4", ".mov", ".mkv"}


def source_of(job: JobRecord, ctx: JobContext) -> Path | None:
    raw = job.inputs.get("audio")
    if not raw:
        refs = [r for r in job.inputs.get("refs", []) if isinstance(r, str) and Path(r).suffix.lower() in AUDIO_EXT]
        raw = refs[0] if refs else ""
    if not raw:
        return None
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else ctx.settings.home / p


class WhisperAdapter(SubprocessAdapter):
    provider = "whisper"

    def __init__(self, poll_interval_sec: float = 1.0) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.TEXT_ASR,), heavy=False, supports_cancel=True, supports_reattach=False,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    def health(self) -> HealthReport:  # settings-free: the tool health lives in the healthcheck's tool.whisper entry
        return HealthReport(HealthState.OK, "subprocess tool")

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        s = ctx.settings
        if find_whisper(s) is None:
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "whisper-cli not found", {"tool": "whisper-cli"})]
        model = whisper_model(s)
        if model is None or not model.is_file():
            return [ValidationIssue(ErrorCode.MISSING_MODEL, "whisper model missing", {"model": str(model)})]
        src = source_of(job, ctx)
        if src is None or not src.is_file():
            return [ValidationIssue(ErrorCode.INVALID_INPUT, f"audio not found: {src}", {"detail": str(src)})]
        return []

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        src = source_of(job, ctx)
        assert src is not None
        try:
            to_wav16k(ctx.settings, src, ctx.work_dir / "input.wav")
        except (FileNotFoundError, RuntimeError) as exc:
            raise AdapterError(ErrorCode.TOOL_FAILED, str(exc), exit_code=-1) from exc
        return super().submit(job, ctx)

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        language = str(job.params.get("language", "zh"))
        return Command(whisper_argv(ctx.settings, ctx.work_dir / "input.wav", ctx.work_dir / "transcript", language))

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        txt, srt = ctx.work_dir / "transcript.txt", ctx.work_dir / "transcript.srt"
        if not txt.is_file():
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "whisper produced no transcript")
        text = " ".join(txt.read_text("utf-8").split())
        meta = {"language": str(job.params.get("language", "zh")), "text": text[:2000]}
        out = [ArtifactSpec(txt, metadata=meta)]
        if srt.is_file():
            out.append(ArtifactSpec(srt, slot="srt", metadata={"language": meta["language"]}))
        return out
