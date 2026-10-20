"""Image generation / editing through the mflux command-line tools (FLUX.2 klein on MLX).

The command, local weights directory and built-in base-model name come from the *model entry*
(``command``, ``weights``, ``base_model`` in models.toml), so another mflux model family is a config entry,
not code. Weights are never downloaded here: a missing weights directory fails validation.

Inputs:  ``prompt``, ``negative_prompt``, ``refs`` = image paths (generate: the first is an init image used with
         ``image_strength``; edit: all of them are the images to edit).
Params:  width, height (multiples of 16), steps, guidance, quantize (3|4|5|6|8), scheduler, low_ram,
         image_strength, loras = [{"path": ..., "scale": 0.8}]  (a LoRA keeps the licence of its base model).
Unset parameters are left to the tool's own defaults. A model entry with ``negative_prompt = false`` (FLUX.2)
has any negative prompt dropped with a ``negative_prompt_ignored`` event.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..contracts import AdapterError, ArtifactSpec, Capabilities, JobContext, JobRecord, ValidationIssue
from ._subprocess import Command, SubprocessAdapter

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
SCHEMA: dict[str, dict[str, Any]] = {
    "width": {"type": "int", "min": 64, "max": 4096},
    "height": {"type": "int", "min": 64, "max": 4096},
    "steps": {"type": "int", "min": 1, "max": 200},
    "guidance": {"type": "float", "min": 0, "max": 30},
    "quantize": {"type": "int", "choices": [3, 4, 5, 6, 8]},
    "scheduler": {"type": "str"},
    "low_ram": {"type": "bool"},
    "image_strength": {"type": "float", "min": 0, "max": 1},
    "loras": {"type": "list"},
}
_STEP = re.compile(r"(\d+)/(\d+)\s*\[")


def _abs(raw: Any, ctx: JobContext) -> Path:
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else ctx.settings.home / p


class MfluxAdapter(SubprocessAdapter):
    provider = "mflux"

    def __init__(self, poll_interval_sec: float = 2.0) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.IMAGE_GENERATE, MediaKind.IMAGE_EDIT), heavy=False, supports_cancel=True,
            supports_reattach=False, param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    # -- config -------------------------------------------------------------------------------
    @staticmethod
    def _entry(job: JobRecord, ctx: JobContext) -> dict[str, Any]:
        model = ctx.settings.models[job.model_ref]
        return dict(model.extra)

    @staticmethod
    def _binary(entry: dict[str, Any], ctx: JobContext) -> Path | None:
        name = str(entry.get("command", ""))
        if not name:
            return None
        bin_dir = ctx.settings.tool_path("mflux", "bin_dir")
        return (bin_dir / name) if bin_dir else Path(name)

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        entry = self._entry(job, ctx)
        binary = self._binary(entry, ctx)
        if binary is None or not binary.is_file():
            return [ValidationIssue(ErrorCode.MISSING_TOOL, f"{entry.get('command')} not found", {"tool": str(binary)})]
        weights = entry.get("weights")
        if not weights or not Path(str(weights)).expanduser().exists():
            return [ValidationIssue(ErrorCode.MISSING_MODEL, f"weights not found: {weights}", {"model": str(weights)})]
        issues: list[ValidationIssue] = []

        def bad(msg: str) -> None:
            issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, msg, {"detail": msg}))

        if not str(job.inputs.get("prompt", "")).strip():
            bad("a prompt is required")
        for dim in ("width", "height"):
            if dim in job.params and int(job.params[dim]) % 16:
                issues.append(ValidationIssue(ErrorCode.INVALID_PARAMS, f"{dim} must be a multiple of 16",
                                              {"detail": f"{dim}={job.params[dim]}"}))
        refs = self._images(job, ctx)
        for ref in refs:
            if not ref.is_file():
                bad(f"reference image not found: {ref}")
        if job.kind is MediaKind.IMAGE_EDIT and not refs:
            bad("image.edit needs at least one image in refs")
        for lora in job.params.get("loras", []):
            if not isinstance(lora, dict) or not lora.get("path") or not _abs(lora["path"], ctx).is_file():
                bad(f"LoRA file not found: {lora}")
        return issues

    @staticmethod
    def _images(job: JobRecord, ctx: JobContext) -> list[Path]:
        return [_abs(r, ctx) for r in job.inputs.get("refs", [])
                if isinstance(r, str) and Path(r).suffix.lower() in IMAGE_EXT]

    # -- run ----------------------------------------------------------------------------------
    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        entry, p = self._entry(job, ctx), job.params
        binary = self._binary(entry, ctx)
        assert binary is not None
        argv = [str(binary), "--model", str(Path(str(entry["weights"])).expanduser())]
        if entry.get("base_model"):
            argv += ["--base-model", str(entry["base_model"])]
        argv += ["--prompt", str(job.inputs["prompt"]), "--output", str(ctx.work_dir / "image.png")]
        if job.seed is not None:
            argv += ["--seed", str(job.seed)]
        if job.inputs.get("negative_prompt"):
            if entry.get("negative_prompt", True):
                argv += ["--negative-prompt", str(job.inputs["negative_prompt"])]
            else:  # FLUX.2 rejects the flag outright; keep the job alive and say so
                ctx.log("negative_prompt_ignored", model=job.model_ref)
        for key, flag in (("width", "--width"), ("height", "--height"), ("steps", "--steps"),
                          ("guidance", "--guidance"), ("quantize", "--quantize"), ("scheduler", "--scheduler")):
            if key in p:
                argv += [flag, str(p[key])]
        if p.get("low_ram"):
            argv.append("--low-ram")
        for lora in p.get("loras", []):
            argv += ["--lora", str(_abs(lora["path"], ctx)), str(lora.get("scale", 1.0))]
        refs = self._images(job, ctx)
        if refs and job.kind is MediaKind.IMAGE_EDIT:
            argv += ["--image-paths", *map(str, refs)]
        elif refs:
            argv += ["--image", str(refs[0]), str(p.get("image_strength", 0.4))]
        return Command(argv)

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        hits = _STEP.findall(log_text)
        if not hits:
            return 0.02, "loading model"
        done, total = map(int, hits[-1])
        return (done / total if total else 0.0), f"step {done}/{total}"

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        img = ctx.work_dir / "image.png"
        if not img.is_file():
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "the tool exited cleanly but wrote no image")
        entry = self._entry(job, ctx)
        meta = {"command": entry.get("command"), "base_model": entry.get("base_model"), "seed": job.seed,
                "loras": job.params.get("loras", [])}
        return [ArtifactSpec(img, metadata=meta)]
