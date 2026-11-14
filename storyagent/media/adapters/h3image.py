"""Image generation / reference editing through the local H3-Image MLX command (``h3-image-mlx generate``).

What only this model does on this machine: native up to 4096x4096, and up to 16 reference images edited by a sentence
(``<Picture n>`` in the prompt is the n-th reference). The sampler is fixed (6 steps), so there are no steps / guidance
parameters; they are never sent.

The command and the weights folder are paths in ``[tools.h3image]`` (``binary``, ``model_dir``); nothing is downloaded
here: an incomplete weights folder fails validation as ``missing_model``.

Inputs:  ``prompt`` (natural-language sentences), ``refs`` = image paths in order (``<Picture 1>`` is the first one).
Params:  width, height (multiples of 32, 256-4096), dit_mode (``resident`` fast, ~21 GB at 1024; ``stream`` slow,
         3-7 GB; ``auto``), precision (``int8`` | ``bf16``), backend (``auto`` | ``w8a8`` | ``portable``).
Unset parameters are left to the tool's own defaults. One picture per process; the kind is heavy, so the broker never
runs two at once nor next to another heavy job.
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
MAX_REFERENCES = 16
MIN_SIDE, MAX_SIDE, STEP = 256, 4096, 32
REQUIRED_WEIGHTS = ("diffusion_models", "text_encoders", "vae", "tokenizer.json")
SCHEMA: dict[str, dict[str, Any]] = {
    "width": {"type": "int", "min": MIN_SIDE, "max": MAX_SIDE},
    "height": {"type": "int", "min": MIN_SIDE, "max": MAX_SIDE},
    "dit_mode": {"type": "str", "choices": ["auto", "stream", "resident"]},
    "precision": {"type": "str", "choices": ["int8", "bf16"]},
    "backend": {"type": "str", "choices": ["auto", "w8a8", "portable"]},
}
_PICTURE = re.compile(r"<\s*Picture\s+(\d+)\s*>", re.IGNORECASE)


def _abs(raw: Any, ctx: JobContext) -> Path:
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else ctx.settings.home / p


class H3ImageAdapter(SubprocessAdapter):
    provider = "h3image"

    def __init__(self, poll_interval_sec: float = 2.0) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.IMAGE_GENERATE, MediaKind.IMAGE_EDIT), heavy=True, supports_cancel=True,
            supports_reattach=False, param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    # -- config -------------------------------------------------------------------------------
    @staticmethod
    def _binary(ctx: JobContext) -> Path | None:
        return ctx.settings.tool_path("h3image", "binary")

    @staticmethod
    def _models_dir(ctx: JobContext) -> Path | None:
        return ctx.settings.tool_path("h3image", "model_dir")

    @staticmethod
    def _references(job: JobRecord, ctx: JobContext) -> list[Path]:
        return [_abs(r, ctx) for r in job.inputs.get("refs", [])
                if isinstance(r, str) and Path(r).suffix.lower() in IMAGE_EXT]

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        binary, models = self._binary(ctx), self._models_dir(ctx)
        if binary is None or not binary.is_file():
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "h3-image-mlx not found", {"tool": str(binary)})]
        missing = [n for n in REQUIRED_WEIGHTS if models is None or not (models / n).exists()]
        if missing:
            return [ValidationIssue(ErrorCode.MISSING_MODEL, f"H3-Image weights incomplete: {', '.join(missing)}",
                                    {"model": str(models), "missing": ", ".join(missing)})]
        issues: list[ValidationIssue] = []

        def bad(msg: str) -> None:
            issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, msg, {"detail": msg}))

        prompt = str(job.inputs.get("prompt", ""))
        if not prompt.strip():
            bad("a prompt is required")
        for dim in ("width", "height"):
            if dim in job.params:
                value = int(job.params[dim])
                if value % STEP or not MIN_SIDE <= value <= MAX_SIDE:
                    issues.append(ValidationIssue(
                        ErrorCode.INVALID_PARAMS,
                        f"{dim} must be a multiple of {STEP} between {MIN_SIDE} and {MAX_SIDE}",
                        {"detail": f"{dim}={value}"}))
        refs = self._references(job, ctx)
        if len(refs) > MAX_REFERENCES:
            bad(f"at most {MAX_REFERENCES} reference images (got {len(refs)})")
        for ref in refs:
            if not ref.is_file():
                bad(f"reference image not found: {ref}")
        if job.kind is MediaKind.IMAGE_EDIT and not refs:
            bad("image.edit needs at least one image in refs")
        too_far = sorted({int(n) for n in _PICTURE.findall(prompt) if int(n) < 1 or int(n) > len(refs)})
        if too_far:
            bad(f"the prompt names <Picture {too_far[0]}> but there are {len(refs)} reference image(s)")
        return issues

    # -- run ----------------------------------------------------------------------------------
    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        binary, models = self._binary(ctx), self._models_dir(ctx)
        assert binary is not None and models is not None
        p = job.params
        argv = [str(binary), "generate", "--models", str(models), "--output", str(ctx.work_dir / "image.png"),
                "--prompt", str(job.inputs["prompt"])]
        if job.seed is not None:
            argv += ["--seed", str(job.seed)]
        for key, flag in (("width", "--width"), ("height", "--height"), ("dit_mode", "--dit-mode"),
                          ("precision", "--precision"), ("backend", "--backend")):
            if key in p:
                argv += [flag, str(p[key])]
        for ref in self._references(job, ctx):
            argv += ["--reference", str(ref)]
        return Command(argv)

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        return 0.05, "loading model" if not log_text.strip() else "sampling"

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        img = ctx.work_dir / "image.png"
        if not img.is_file() or img.stat().st_size == 0:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "the tool exited cleanly but wrote no image")
        p = job.params
        meta = {"seed": job.seed, "dit_mode": p.get("dit_mode"), "precision": p.get("precision"),
                "references": len(self._references(job, ctx)), "sampler": "fixed 6-step"}
        return [ArtifactSpec(img, metadata=meta)]
