"""LongCat-Video-Avatar 1.5 (MLX): a portrait + speech -> a talking-head video (``video.avatar``).

Inputs:  ``image`` (portrait; or the first image in ``refs``), ``audio`` (speech; or the first audio in ``refs``),
         ``prompt`` (what the person does; English reads best).
Params:  width, height, num_frames (default 93), seed comes from the job.
The tool is a Python script in its own virtualenv. It takes the portrait/speech/prompt from environment variables
(LONGCAT_IMAGE / LONGCAT_AUDIO / LONGCAT_PROMPT) and writes ``--out``; the variant directory under ``weights_dir``
(``[tools.longcat] variant_dir``) must exist - a missing variant is reported as a missing model, never downloaded.
A 480p clip peaks around 50 GB in bf16, so the model entry is ``heavy`` and runs alone.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..contracts import AdapterError, ArtifactSpec, Capabilities, JobContext, JobRecord, ValidationIssue
from ._subprocess import Command, SubprocessAdapter

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
SCHEMA: dict[str, dict[str, Any]] = {
    "width": {"type": "int", "min": 64, "max": 2048},
    "height": {"type": "int", "min": 64, "max": 2048},
    "num_frames": {"type": "int", "min": 5, "max": 600},
}
_STEP = re.compile(r"(\d+)\s*/\s*(\d+)")


def _abs(raw: Any, ctx: JobContext) -> Path:
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else ctx.settings.home / p


class LongCatAdapter(SubprocessAdapter):
    provider = "longcat"

    def __init__(self, poll_interval_sec: float = 3.0) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.VIDEO_AVATAR,), heavy=True, supports_cancel=True, supports_reattach=False,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    @staticmethod
    def _paths(ctx: JobContext) -> tuple[Path | None, Path | None, Path | None]:
        s = ctx.settings
        root, python = s.tool_path("longcat", "root"), s.tool_path("longcat", "python")
        weights, variant = s.tool_path("longcat", "weights_dir"), str(s.tools.get("longcat", {}).get("variant_dir", ""))
        return root, python, (weights / variant) if weights and variant else None

    @staticmethod
    def _sources(job: JobRecord, ctx: JobContext) -> tuple[Path | None, Path | None]:
        refs = [_abs(r, ctx) for r in job.inputs.get("refs", []) if isinstance(r, str)]
        image = _abs(job.inputs["image"], ctx) if job.inputs.get("image") else next(
            (r for r in refs if r.suffix.lower() in IMAGE_EXT), None)
        audio = _abs(job.inputs["audio"], ctx) if job.inputs.get("audio") else next(
            (r for r in refs if r.suffix.lower() in AUDIO_EXT), None)
        return image, audio

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        root, python, variant = self._paths(ctx)
        script = root / "scripts" / "run_inference.py" if root else None
        if python is None or not python.is_file() or script is None or not script.is_file():
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "LongCat runtime not found", {"tool": "longcat"})]
        if variant is None or not variant.exists():
            return [ValidationIssue(ErrorCode.MISSING_MODEL, f"LongCat weights not found: {variant}",
                                    {"model": str(variant)})]
        issues: list[ValidationIssue] = []
        image, audio = self._sources(job, ctx)
        for label, f in (("portrait image", image), ("speech audio", audio)):
            if f is None or not f.is_file():
                issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, f"{label} not found: {f}", {"detail": label}))
        return issues

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        root, python, variant = self._paths(ctx)
        assert root and python and variant
        image, audio = self._sources(job, ctx)
        assert image and audio
        p = job.params
        argv = [str(python), str(root / "scripts" / "run_inference.py"), "--weights", str(variant),
                "--out", str(ctx.work_dir / "avatar.mp4")]
        for key, flag in (("height", "--height"), ("width", "--width"), ("num_frames", "--num-frames")):
            if key in p:
                argv += [flag, str(p[key])]
        if job.seed is not None:
            argv += ["--seed", str(job.seed)]
        env = {"LONGCAT_IMAGE": str(image), "LONGCAT_AUDIO": str(audio),
               "LONGCAT_PROMPT": str(job.inputs.get("prompt") or "a person speaking to the camera")}
        return Command(argv, cwd=root, env=env)

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        hits = _STEP.findall(log_text)
        if not hits:
            return 0.02, "loading model"
        done, total = map(int, hits[-1])
        return min(0.05 + 0.9 * done / max(total, 1), 0.97), f"step {done}/{total}"

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        out = ctx.work_dir / "avatar.mp4"
        if not out.is_file() or out.stat().st_size == 0:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "LongCat finished but wrote no video")
        return [ArtifactSpec(out, metadata={"engine": "longcat-avatar-1.5", "seed": job.seed})]
