"""train.lora through ``mflux-train``: photos -> a character LoRA that image.generate can load.

Inputs:
    images       list of image paths / asset refs (the training set; at least ``min_images``)
    captions     optional list, same length: one caption per image. Missing -> "<trigger> <description>".
    trigger      rare token that will call the character up in prompts (required)
    description  fixed text after the trigger (default "photo of a person")
    previews     optional prompts rendered during training so you can see whether it learned
    consent      true = the depicted person agreed to this (checked by the compliance gate)
Params (override the model entry's ``train_template``; everything in it is configurable):
    epochs, rank, blocks (int, number of transformer blocks to adapt), single_blocks, learning_rate,
    max_resolution, seed, min_images, save_frequency, train = {...}   (deep-merged last: any mflux-train key)
Output: ``train.lora`` asset = the adapter of the newest checkpoint (taken out of ``*_checkpoint.zip``);
        metadata: trigger, base model, rank, epochs, final loss ...
        The LoRA keeps the licence of its base model (the model entry's ``license_tag`` is what is recorded).
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..contracts import AdapterError, ArtifactSpec, Capabilities, JobContext, JobRecord, ValidationIssue
from ..probe import find_tool
from ._subprocess import Command, SubprocessAdapter

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
SCHEMA: dict[str, dict[str, Any]] = {
    "epochs": {"type": "int", "min": 1, "max": 1000},
    "rank": {"type": "int", "min": 1, "max": 256},
    "blocks": {"type": "int", "min": 1, "max": 64},
    "single_blocks": {"type": "int", "min": 0, "max": 128},
    "learning_rate": {"type": "float", "min": 0},
    "max_resolution": {"type": "int", "min": 64, "max": 4096},
    "seed": {"type": "int"},
    "min_images": {"type": "int", "min": 1},
    "save_frequency": {"type": "int", "min": 1},
    "train": {"type": "dict"},
}
_STEP = re.compile(r"(\d+)/(\d+)\s*\[")
_TRIGGER = re.compile(r"^[A-Za-z0-9_\- ]{2,40}$")


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _abs(raw: Any, ctx: JobContext) -> Path:
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else ctx.settings.home / p


class LoraTrainAdapter(SubprocessAdapter):
    provider = "lora_train"

    def __init__(self, poll_interval_sec: float = 5.0) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.TRAIN_LORA,), heavy=True, supports_cancel=True, supports_reattach=False,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    # -- config -------------------------------------------------------------------------------
    @staticmethod
    def _tool(ctx: JobContext) -> tuple[Path | None, Path | None]:
        s = ctx.settings
        return s.tool_path("lora_train", "command"), s.tool_path("lora_train", "base_model_dir")

    @staticmethod
    def _images(job: JobRecord, ctx: JobContext) -> list[Path]:
        return [_abs(i, ctx) for i in job.inputs.get("images", [])]

    def train_config(self, job: JobRecord, ctx: JobContext, data: Path, out: Path) -> dict[str, Any]:
        """The mflux-train JSON: model template + job params, with the paths this run owns injected."""
        s, p = ctx.settings, job.params
        template = dict(s.models[job.model_ref].extra.get("train_template", {}))
        _, base_dir = self._tool(ctx)
        cfg = copy.deepcopy(template)
        if base_dir is not None:
            cfg["model_path"] = str(base_dir)
        cfg["data"] = str(data)
        cfg.setdefault("checkpoint", {})["output_path"] = str(out)
        if "seed" in p:
            cfg["seed"] = int(p["seed"])
        if "max_resolution" in p:
            cfg["max_resolution"] = int(p["max_resolution"])
        if "epochs" in p:
            cfg.setdefault("training_loop", {})["num_epochs"] = int(p["epochs"])
        if "learning_rate" in p:
            cfg.setdefault("optimizer", {})["learning_rate"] = float(p["learning_rate"])
        if "save_frequency" in p:
            cfg["checkpoint"]["save_frequency"] = int(p["save_frequency"])
        for target in cfg.get("lora_layers", {}).get("targets", []):
            if "rank" in p:
                target["rank"] = int(p["rank"])
            span = target.get("blocks")
            single = "single_transformer_blocks" in str(target.get("module_path", ""))
            count = p.get("single_blocks" if single else "blocks")
            if count is not None and isinstance(span, dict):
                span["end"] = int(span.get("start", 0)) + int(count)
        return deep_merge(cfg, dict(p.get("train", {})))

    # -- validation ---------------------------------------------------------------------------
    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        command, base_dir = self._tool(ctx)
        if command is None or not command.is_file():
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "mflux-train not found", {"tool": str(command)})]
        if base_dir is None or not base_dir.exists():
            return [ValidationIssue(ErrorCode.MISSING_MODEL, f"base model not found: {base_dir}",
                                    {"model": str(base_dir)})]
        issues: list[ValidationIssue] = []

        def bad(msg: str) -> None:
            issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, msg, {"detail": msg}))

        trigger = str(job.inputs.get("trigger", "")).strip()
        if not _TRIGGER.match(trigger):
            bad("trigger must be 2-40 characters of letters, digits, space, _ or - (use a rare word, e.g. 'lwx woman')")
        images = self._images(job, ctx)
        minimum = int(job.params.get("min_images", 4))
        if len(images) < minimum:
            bad(f"need at least {minimum} training images (got {len(images)})")
        for img in images:
            if not img.is_file() or img.suffix.lower() not in IMAGE_EXT:
                bad(f"training image not found or not an image: {img}")
        captions = job.inputs.get("captions")
        if captions is not None and len(captions) != len(images):
            bad("captions must have one entry per image")
        convertible = all(i.suffix.lower() in (".png", ".jpg", ".jpeg") for i in images)
        if not convertible and find_tool(ctx.settings, "ffmpeg") is None:
            bad("ffmpeg is needed to convert this image format")
        if "model" not in ctx.settings.models[job.model_ref].extra.get("train_template", {}):
            bad("the model entry has no train_template.model")
        return issues

    # -- run ----------------------------------------------------------------------------------
    def _prepare(self, job: JobRecord, ctx: JobContext) -> Path:
        """<work>/data: NN.png + NN.txt pairs and preview_N.txt, the layout mflux-train reads."""
        data = ctx.work_dir / "data"
        data.mkdir(parents=True, exist_ok=True)
        trigger = str(job.inputs["trigger"]).strip()
        desc = str(job.inputs.get("description", "photo of a person")).strip()
        limit = int(job.params.get("max_resolution", ctx.settings.models[job.model_ref].extra.get(
            "train_template", {}).get("max_resolution", 1024)))
        captions = job.inputs.get("captions")
        ffmpeg = find_tool(ctx.settings, "ffmpeg")
        for n, src in enumerate(self._images(job, ctx), start=1):
            stem = f"{n:02d}"
            dest = data / f"{stem}.png"
            if ffmpeg is not None:  # also caps the long edge at max_resolution
                scale = f"scale=w='if(gt(iw,ih),min({limit},iw),-2)':h='if(gt(iw,ih),-2,min({limit},ih))'"
                argv = [str(ffmpeg), "-y", "-loglevel", "error", "-i", str(src), "-vf", scale, "-frames:v", "1",
                        str(dest)]
                proc = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603 - fixed argv
                if proc.returncode != 0 or not dest.is_file():
                    raise AdapterError(ErrorCode.INVALID_INPUT, f"cannot read image {src.name}: {proc.stderr[-120:]}",
                                       detail=src.name)
            else:
                shutil.copy2(src, dest)
            caption = str(captions[n - 1]).strip() if captions else f"{trigger} {desc}"
            (data / f"{stem}.txt").write_text(caption + "\n", "utf-8")
        previews = job.inputs.get("previews") or [
            f"{trigger} {desc}, standing on a city street at dusk, cinematic lighting",
            f"{trigger} {desc}, close-up portrait, soft studio light, looking at camera",
        ]
        for i, text in enumerate(previews, start=1):
            (data / f"preview_{i}.txt").write_text(str(text) + "\n", "utf-8")
        return data

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        data = self._prepare(job, ctx)
        out = ctx.work_dir / "out"
        out.mkdir(parents=True, exist_ok=True)
        cfg = self.train_config(job, ctx, data, out)
        (ctx.work_dir / "train.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
        return super().submit(job, ctx)

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        command, _ = self._tool(ctx)
        assert command is not None
        return Command([str(command), "--config", str(ctx.work_dir / "train.json")])

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        hits = _STEP.findall(log_text)
        if not hits:
            return 0.02, "preparing"
        done, total = map(int, hits[-1])
        return (done / total if total else 0.0), f"{done}/{total}"

    @staticmethod
    def _adapter_file(ctx: JobContext) -> tuple[Path | None, dict[str, Any]]:
        """The newest adapter. mflux-train writes ``checkpoints/<step>_checkpoint.zip`` (adapter + optimizer + loss)
        into "<output_path>_<timestamp>" next to the configured folder, so every ``out*`` folder is searched."""
        dirs = list(ctx.work_dir.glob("out*"))
        loose = [f for d in dirs for f in d.rglob("*_adapter.safetensors")]
        zips = [f for d in dirs for f in d.rglob("*_checkpoint.zip")]
        newest = max(loose + zips, key=lambda p: p.stat().st_mtime, default=None)
        if newest is None:
            return None, {}
        if newest.suffix != ".zip":
            return newest, {}
        extra: dict[str, Any] = {}
        with zipfile.ZipFile(newest) as zf:
            names = zf.namelist()
            adapter = next((n for n in names if n.endswith("_adapter.safetensors")), None)
            if adapter is None:
                return None, {}
            dest = ctx.work_dir / "extracted"
            dest.mkdir(exist_ok=True)
            zf.extract(adapter, dest)
            loss = next((n for n in names if n.endswith("_loss.json")), None)
            if loss is not None:
                try:
                    data = json.loads(zf.read(loss))
                    values = data.get("loss", data) if isinstance(data, dict) else data
                    flat = [float(v) for v in values if isinstance(v, int | float)]
                    if flat:
                        extra = {"final_loss": round(sum(flat[-5:]) / len(flat[-5:]), 5), "loss_points": len(flat)}
                except (ValueError, TypeError):
                    pass
        return dest / adapter, extra

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        adapter, extra = self._adapter_file(ctx)
        if adapter is None:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "training finished without writing an adapter")
        cfg = json.loads((ctx.work_dir / "train.json").read_text("utf-8"))
        ranks = {t.get("rank") for t in cfg.get("lora_layers", {}).get("targets", [])}
        meta = {
            "trigger": str(job.inputs["trigger"]).strip(), "base_model": cfg.get("model"),
            "base_license": job.license_tag, "epochs": cfg.get("training_loop", {}).get("num_epochs"),
            "rank": max((r for r in ranks if r), default=None), "images": len(self._images(job, ctx)),
            "learning_rate": cfg.get("optimizer", {}).get("learning_rate"), "adapter_file": adapter.name,
            "max_resolution": cfg.get("max_resolution"), **extra,
        }
        return [ArtifactSpec(adapter, metadata=meta)]
