"""audio.convert through Seed-VC: keep a recording's performance, change who is speaking.

Inputs:  ``audio``      source recording (path / asset ref) - the words, timing and emotion to keep
         ``voice_ref``  {"audio": <reference clip>, "consent": true}  the speaker to sound like
Engines (the model entry's ``engine``):
    v2  ``inference_v2.py`` (speech; style/accent conversion with ``convert_style``) - default
    v1  ``inference.py`` with ``f0_condition`` (pitch-aware 44.1 kHz; use for singing, ``semi_tone_shift`` for range)
Common params: diffusion_steps, length_adjust, ref_min_sec, ref_max_sec
    v2: intelligibility_cfg_rate, similarity_cfg_rate, top_p, temperature, repetition_penalty, convert_style
    v1: inference_cfg_rate, f0_condition, auto_f0_adjust, semi_tone_shift, fp16
Song mode: ``separate_vocals = true`` splits the recording with Demucs, converts only the vocals and mixes the
         accompaniment back (``accompaniment_gain``; ``demucs_model``, ``demucs_device``).
Runs ``runners/seedvc_pipeline.py`` inside the Seed-VC virtualenv. Checkpoints are passed explicitly (model entry
``ar_checkpoint`` / ``cfm_checkpoint``) and the hub is offline by default (``[tools.seedvc] offline``): left to its
own devices Seed-VC looks up missing files on the hub and can hang there for good.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..contracts import AdapterError, ArtifactSpec, Capabilities, JobContext, JobRecord, ValidationIssue
from ..probe import find_tool, probe
from ._subprocess import Command, SubprocessAdapter

SCHEMA: dict[str, dict[str, Any]] = {
    "diffusion_steps": {"type": "int", "min": 1, "max": 200},
    "length_adjust": {"type": "float", "min": 0.3, "max": 3.0},
    "intelligibility_cfg_rate": {"type": "float", "min": 0, "max": 2},
    "similarity_cfg_rate": {"type": "float", "min": 0, "max": 2},
    "top_p": {"type": "float", "min": 0, "max": 1},
    "temperature": {"type": "float", "min": 0, "max": 2},
    "repetition_penalty": {"type": "float", "min": 0.5, "max": 3},
    "convert_style": {"type": "bool"},
    "inference_cfg_rate": {"type": "float", "min": 0, "max": 2},
    "f0_condition": {"type": "bool"},
    "auto_f0_adjust": {"type": "bool"},
    "semi_tone_shift": {"type": "int", "min": -24, "max": 24},
    "fp16": {"type": "bool"},
    "separate_vocals": {"type": "bool"},
    "accompaniment_gain": {"type": "float", "min": 0, "max": 4},
    "demucs_model": {"type": "str"},
    "demucs_device": {"type": "str", "choices": ["mps", "cpu"]},
    "ref_min_sec": {"type": "float", "min": 0},
    "ref_max_sec": {"type": "float", "min": 1},
}
_NUM = {
    "v2": (("diffusion_steps", "--diffusion-steps"), ("length_adjust", "--length-adjust"),
           ("intelligibility_cfg_rate", "--intelligibility-cfg-rate"),
           ("similarity_cfg_rate", "--similarity-cfg-rate"), ("top_p", "--top-p"), ("temperature", "--temperature"),
           ("repetition_penalty", "--repetition-penalty")),
    "v1": (("diffusion_steps", "--diffusion-steps"), ("length_adjust", "--length-adjust"),
           ("inference_cfg_rate", "--inference-cfg-rate"), ("semi_tone_shift", "--semi-tone-shift")),
}
_BOOL = {
    "v2": (("convert_style", "--convert-style"),),
    "v1": (("f0_condition", "--f0-condition"), ("auto_f0_adjust", "--auto-f0-adjust"), ("fp16", "--fp16")),
}
_SCRIPT = {"v2": "inference_v2.py", "v1": "inference.py"}
RUNNER = Path(__file__).resolve().parents[1] / "runners" / "seedvc_pipeline.py"
# The hub client's HEAD probe is slow on this network (LocalEntryNotFoundError) and a missing checkpoint makes it
# hang inside the xet downloader, so by default only the local files are used (``[tools.seedvc] offline``).
_ENV = {"HF_HUB_ETAG_TIMEOUT": "60", "HF_HUB_DOWNLOAD_TIMEOUT": "600"}


def _abs(raw: Any, ctx: JobContext) -> Path:
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else ctx.settings.home / p


class SeedVcAdapter(SubprocessAdapter):
    provider = "seedvc"

    def __init__(self, poll_interval_sec: float = 2.0, runner: Path | None = None) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec
        self.runner = runner or RUNNER

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.AUDIO_CONVERT,), heavy=False, supports_cancel=True, supports_reattach=False,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    @staticmethod
    def _paths(ctx: JobContext) -> tuple[Path | None, Path | None]:
        return ctx.settings.tool_path("seedvc", "python"), ctx.settings.tool_path("seedvc", "root")

    @staticmethod
    def _entry(job: JobRecord, ctx: JobContext) -> dict[str, Any]:
        return dict(ctx.settings.models[job.model_ref].extra) if job.model_ref in ctx.settings.models else {}

    def _engine(self, job: JobRecord, ctx: JobContext) -> str:
        return str(self._entry(job, ctx).get("engine", "v2"))

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        python, root = self._paths(ctx)
        engine = self._engine(job, ctx)
        if engine not in _SCRIPT:
            return [ValidationIssue(ErrorCode.INVALID_PARAMS, f"unknown Seed-VC engine {engine!r}",
                                    {"detail": f"engine={engine}"})]
        if not (python and python.is_file() and root and (root / _SCRIPT[engine]).is_file() and self.runner.is_file()):
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "Seed-VC runtime not found", {"tool": "seedvc"})]
        entry = self._entry(job, ctx)
        for key in ("ar_checkpoint", "cfm_checkpoint"):
            if entry.get(key) and not (root / str(entry[key])).is_file():
                return [ValidationIssue(ErrorCode.MISSING_MODEL, f"Seed-VC checkpoint missing: {entry[key]}",
                                        {"model": str(entry[key])})]
        if not (root / "checkpoints").is_dir():
            return [ValidationIssue(ErrorCode.MISSING_MODEL, "Seed-VC checkpoints missing", {"model": "seed-vc"})]
        issues: list[ValidationIssue] = []

        def bad(msg: str) -> None:
            issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, msg, {"detail": msg}))

        src = _abs(job.inputs.get("audio", ""), ctx) if job.inputs.get("audio") else None
        if src is None or not src.is_file():
            bad(f"source audio not found: {src}")
        ref_raw = (job.inputs.get("voice_ref") or {}).get("audio")
        ref = _abs(ref_raw, ctx) if ref_raw else None
        if ref is None or not ref.is_file():
            bad(f"reference voice not found: {ref}")
        else:
            info = probe(ctx.settings, ref)
            lo, hi = float(job.params.get("ref_min_sec", 3)), float(job.params.get("ref_max_sec", 60))
            if info and info.duration_sec is not None and not lo <= info.duration_sec <= hi:
                bad(f"reference voice must be {lo:g}-{hi:g} s long (it is {info.duration_sec:.1f} s)")
        if job.params.get("separate_vocals") and find_tool(ctx.settings, "ffmpeg") is None:
            bad("ffmpeg is needed to mix the accompaniment back")
        return issues

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        python, root = self._paths(ctx)
        assert python is not None and root is not None
        engine, p, entry = self._engine(job, ctx), job.params, self._entry(job, ctx)
        args: list[str] = []
        for key, flag in _NUM[engine]:
            if key in p:
                args += [flag, str(p[key])]
        for key, flag in _BOOL[engine]:
            if key in p:
                args += [flag, "true" if p[key] else "false"]
        if engine == "v2":
            for key, flag in (("ar_checkpoint", "--ar-checkpoint-path"), ("cfm_checkpoint", "--cfm-checkpoint-path")):
                if entry.get(key):
                    args += [flag, str(root / str(entry[key]))]
        env = dict(_ENV)
        if ctx.settings.tools.get("seedvc", {}).get("offline", True):
            env["HF_HUB_OFFLINE"] = "1"
        ffmpeg = find_tool(ctx.settings, "ffmpeg")
        cfg = {
            "root": str(root), "script": _SCRIPT[engine], "args": args, "env": env,
            "source": str(_abs(job.inputs["audio"], ctx)), "target": str(_abs(job.inputs["voice_ref"]["audio"], ctx)),
            "work_dir": str(ctx.work_dir), "separate": bool(p.get("separate_vocals")),
            "demucs_model": str(p.get("demucs_model", "htdemucs")), "demucs_device": str(p.get("demucs_device", "mps")),
            "accompaniment_gain": float(p.get("accompaniment_gain", 1.0)), "ffmpeg": str(ffmpeg or ""),
        }
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        (ctx.work_dir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
        return super().submit(job, ctx)

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        python, root = self._paths(ctx)
        assert python is not None
        return Command([str(python), str(self.runner), str(ctx.work_dir / "config.json")], cwd=root)

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        for stage, frac in (("mix", 0.9), ("convert", 0.5), ("separate", 0.15)):
            if f'"name": "{stage}"' in log_text:
                return frac, stage
        return 0.03, "loading"

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        final = ctx.work_dir / "final.wav"
        if not final.is_file():
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "Seed-VC produced no audio")
        p = job.params
        meta: dict[str, Any] = {
            "engine": self._engine(job, ctx), "source": Path(str(job.inputs["audio"])).name,
            "reference": Path(str(job.inputs["voice_ref"]["audio"])).name,
            "separate_vocals": bool(p.get("separate_vocals")),
        }
        meta.update({k: p[k] for k in ("diffusion_steps", "length_adjust", "f0_condition") if k in p})
        return [ArtifactSpec(final, metadata=meta)]
