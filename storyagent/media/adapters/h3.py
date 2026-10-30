"""MiniMax H3 (video + synchronised audio) through the h3.c command-line tool.

Inputs:  ``prompt``; ``refs`` = files whose role follows from the extension and ``inputs.mode``:
         first_frame (FL2VA, default when an image is given), last_frame, ref_images (Ref2VA), ref_audio, ref_video.
         FL2VA (first/last frame) and Ref2VA (reference images) are different model variants and cannot be mixed.
Params:  width, height, frames (rounded to 5 + 17n unless ``snap_frames`` is false) or seconds (24 fps), steps,
         reuse (1 close .. 3 aggressive), layers (50 exact .. 40 aggressive), core_reuse (1|4|6), token_reduction,
         ssd_streaming, int8_row_fc2 (faster FC2 on M5), ref_image_size (match | max), render_width, render_height.
         Size rules (h3.c refuses otherwise): width/height must be multiples of 32 and width*height <= 768*1344 =
         1,032,192 px, so native 1080p is impossible - 1080 comes from upscaling. Portrait projects get the same
         pixel budget reshaped by ``orient_size`` (1024x576 -> 576x1024). Defaults are the anatomy-first quality set:
         steps 50 / layers 50 / reuse 1 / core_reuse 1; the ``draft`` tier drops to 4 steps for composition checks.
Licence: the model's licence excludes EU/UK/South Korea/USA and requires the attribution text; both are carried by the
         model entry (``commercial_ok = false``) so the compliance gate asks for an explicit acknowledgement.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..contracts import AdapterError, ArtifactSpec, Capabilities, JobContext, JobRecord, ValidationIssue
from ..probe import find_tool
from ._subprocess import Command, SubprocessAdapter

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}
SCHEMA: dict[str, dict[str, Any]] = {
    "width": {"type": "int", "min": 64, "max": 4096},
    "height": {"type": "int", "min": 64, "max": 4096},
    "render_width": {"type": "int", "min": 64, "max": 4096},
    "render_height": {"type": "int", "min": 64, "max": 4096},
    "frames": {"type": "int", "min": 5, "max": 1000},
    "seconds": {"type": "float", "min": 0.5, "max": 40},
    "steps": {"type": "int", "min": 1, "max": 200},
    "reuse": {"type": "int", "choices": [1, 2, 3]},
    "layers": {"type": "int", "choices": [40, 45, 50]},
    "core_reuse": {"type": "int", "choices": [1, 4, 6]},
    "token_reduction": {"type": "bool"},
    "ssd_streaming": {"type": "bool"},
    "snap_frames": {"type": "bool"},
    "int8_row_fc2": {"type": "bool"},
    "ref_image_size": {"type": "str", "choices": ["match", "max"]},
}
_STEP = re.compile(r"(?:step|pass|denois\w*)\D{0,12}(\d+)\s*/\s*(\d+)", re.IGNORECASE)
FPS = 24


def snap_frames(frames: int) -> int:
    """H3 renders 5 + 17n frames: round to the nearest valid count (never below 5 + 17)."""
    n = max(round((frames - 5) / 17), 1)
    return 5 + 17 * n


def _abs(raw: Any, ctx: JobContext) -> Path:
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else ctx.settings.home / p


def classify(job: JobRecord, ctx: JobContext) -> dict[str, list[Path]]:
    """Sort the job's reference files into the roles h3 understands."""
    out: dict[str, list[Path]] = {"first": [], "last": [], "ref_images": [], "ref_audio": [], "ref_video": []}
    mode = str(job.inputs.get("mode", ""))
    for raw in job.inputs.get("refs", []):
        p = _abs(raw, ctx)
        ext = p.suffix.lower()
        if ext in IMAGE_EXT:
            if mode == "ref2va":
                out["ref_images"].append(p)
            else:
                out["first" if not out["first"] else "last"].append(p)
        elif ext in AUDIO_EXT:
            out["ref_audio"].append(p)
        elif ext in VIDEO_EXT:
            out["ref_video"].append(p)
    for key, name in (("last_frame", "last"), ("first_frame", "first")):
        if job.inputs.get(key):
            out[name] = [_abs(job.inputs[key], ctx)]
    return out


class H3Adapter(SubprocessAdapter):
    provider = "h3"

    def __init__(self, poll_interval_sec: float = 3.0) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.VIDEO_AV, MediaKind.VIDEO_GENERATE), heavy=True, supports_cancel=True,
            supports_reattach=False, param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    @staticmethod
    def _paths(ctx: JobContext) -> tuple[Path | None, Path | None]:
        return ctx.settings.tool_path("h3", "binary"), ctx.settings.tool_path("h3", "model_dir")

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        binary, model_dir = self._paths(ctx)
        if binary is None or not binary.is_file() or not os.access(binary, os.X_OK):
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "h3 binary not found", {"tool": str(binary)})]
        if model_dir is None or not (model_dir / "model_index.json").is_file():
            return [ValidationIssue(ErrorCode.MISSING_MODEL, f"MiniMax-H3 weights not found: {model_dir}",
                                    {"model": str(model_dir)})]
        if find_tool(ctx.settings, "ffmpeg") is None:
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "h3 needs ffmpeg on PATH", {"tool": "ffmpeg"})]
        issues: list[ValidationIssue] = []

        def bad(msg: str) -> None:
            issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, msg, {"detail": msg}))

        if not str(job.inputs.get("prompt", "")).strip():
            bad("a prompt is required")
        roles = classify(job, ctx)
        for files in roles.values():
            for f in files:
                if not f.is_file():
                    bad(f"reference file not found: {f}")
        if (roles["first"] or roles["last"]) and roles["ref_images"]:
            bad("first/last frame (FL2VA) and reference images (Ref2VA) cannot be combined")
        if roles["last"] and not roles["first"]:
            bad("a last frame needs a first frame")
        if "frames" in job.params and "seconds" in job.params:
            bad("give frames or seconds, not both")
        return issues

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        binary, model_dir = self._paths(ctx)
        assert binary is not None and model_dir is not None
        p = job.params
        argv = [str(binary), "-d", str(model_dir), "-p", str(job.inputs["prompt"]), "-o", str(ctx.work_dir / "h3.mp4")]
        for key, flag in (("width", "--width"), ("height", "--height"), ("render_width", "--render-width"),
                          ("render_height", "--render-height"), ("steps", "--steps"), ("reuse", "--reuse"),
                          ("layers", "--layers"), ("core_reuse", "--core-reuse")):
            if key in p:
                argv += [flag, str(p[key])]
        if "seconds" in p:
            argv += ["--seconds", str(p["seconds"])]
        elif "frames" in p:
            frames = int(p["frames"])
            argv += ["--frames", str(snap_frames(frames) if p.get("snap_frames", True) else frames)]
        if job.seed is not None:
            argv += ["--seed", str(job.seed)]
        if "ref_image_size" in p:
            argv += ["--ref-image-size", str(p["ref_image_size"])]
        for key, flag in (("token_reduction", "--token-reduction"), ("ssd_streaming", "--ssd-streaming"),
                          ("int8_row_fc2", "--use-int8-row-fc2")):
            if p.get(key):
                argv.append(flag)
        roles = classify(job, ctx)
        for f in roles["first"]:
            argv += ["--first-frame", str(f)]
        for f in roles["last"]:
            argv += ["--last-frame", str(f)]
        for f in roles["ref_images"]:
            argv += ["--ref-image", str(f)]
        for f in roles["ref_audio"]:
            argv += ["--ref-audio", str(f)]
        for f in roles["ref_video"]:
            argv += ["--ref-video", str(f)]
        ffmpeg = find_tool(ctx.settings, "ffmpeg")
        path = os.environ.get("PATH", "")
        env = {"PATH": f"{ffmpeg.parent}{os.pathsep}{path}"} if ffmpeg else {}
        # h3 looks for its Metal shader source next to the binary, so it must run from there (all paths are absolute)
        return Command(argv, cwd=binary.parent, env=env)

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        hits = _STEP.findall(log_text)
        if not hits:
            return 0.02, "loading model"
        done, total = map(int, hits[-1])
        return min(0.05 + 0.9 * done / max(total, 1), 0.97), f"step {done}/{total}"

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        out = ctx.work_dir / "h3.mp4"
        if not out.is_file() or out.stat().st_size == 0:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "h3 finished but wrote no video")
        model = ctx.settings.models[job.model_ref]
        meta = {"engine": "h3.c", "seed": job.seed, "attribution": model.attribution, "license_tag": model.license_tag,
                "frames": job.params.get("frames"), "fps": FPS}
        return [ArtifactSpec(out, metadata=meta)]
