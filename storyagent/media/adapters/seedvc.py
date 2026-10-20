"""audio.convert through Seed-VC (``inference.py``): keep a recording's performance, change who is speaking.

Only the pitch-aware 44.1 kHz checkpoint is installed locally, so ``f0_condition`` must stay true (speech: also
set ``auto_f0_adjust``; singing: ``semi_tone_shift`` for the octave). Seed-VC's v2
(``inference_v2.py``, autoregressive) hung on this machine's MPS backend, so v1 is the supported path.
The checkout carries two local patches (float32 F0 for MPS, soundfile instead of torchaudio.save).

Inputs:  ``audio``      source recording (path / asset ref) - the words, timing and emotion to keep
         ``voice_ref``  {"audio": <reference clip>, "consent": true}  the speaker to sound like
Params:  diffusion_steps, length_adjust, inference_cfg_rate, f0_condition, auto_f0_adjust, semi_tone_shift, fp16,
         ref_min_sec, ref_max_sec
Output:  one wav (``main``). The model is loaded for each job (its own CLI), so batch several lines as separate jobs
         only when the load time is acceptable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..contracts import AdapterError, ArtifactSpec, Capabilities, JobContext, JobRecord, ValidationIssue
from ..probe import probe
from ._subprocess import Command, SubprocessAdapter

SCHEMA: dict[str, dict[str, Any]] = {
    "diffusion_steps": {"type": "int", "min": 1, "max": 200},
    "length_adjust": {"type": "float", "min": 0.3, "max": 3.0},
    "inference_cfg_rate": {"type": "float", "min": 0, "max": 2},
    "f0_condition": {"type": "bool"},
    "auto_f0_adjust": {"type": "bool"},
    "semi_tone_shift": {"type": "int", "min": -24, "max": 24},
    "fp16": {"type": "bool"},
    "ref_min_sec": {"type": "float", "min": 0},
    "ref_max_sec": {"type": "float", "min": 1},
}
_NUM_FLAGS = (
    ("diffusion_steps", "--diffusion-steps"), ("length_adjust", "--length-adjust"),
    ("inference_cfg_rate", "--inference-cfg-rate"), ("semi_tone_shift", "--semi-tone-shift"),
)
_BOOL_FLAGS = (("f0_condition", "--f0-condition"), ("auto_f0_adjust", "--auto-f0-adjust"), ("fp16", "--fp16"))
# The hub client's HEAD probe is slow on this network (LocalEntryNotFoundError) and a missing checkpoint makes it
# hang inside the xet downloader, so by default only the local files are used (``[tools.seedvc] offline``).
_ENV = {"HF_HUB_ETAG_TIMEOUT": "60", "HF_HUB_DOWNLOAD_TIMEOUT": "600"}


def _abs(raw: Any, ctx: JobContext) -> Path:
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else ctx.settings.home / p


class SeedVcAdapter(SubprocessAdapter):
    provider = "seedvc"

    def __init__(self, poll_interval_sec: float = 2.0) -> None:
        super().__init__()
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.AUDIO_CONVERT,), heavy=False, supports_cancel=True, supports_reattach=False,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    @staticmethod
    def _paths(ctx: JobContext) -> tuple[Path | None, Path | None]:
        return ctx.settings.tool_path("seedvc", "python"), ctx.settings.tool_path("seedvc", "root")

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        python, root = self._paths(ctx)
        if not (python and python.is_file() and root and (root / "inference.py").is_file()):
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "Seed-VC runtime not found", {"tool": "seedvc"})]
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
            lo, hi = float(job.params.get("ref_min_sec", 5)), float(job.params.get("ref_max_sec", 60))
            if info and info.duration_sec is not None and not lo <= info.duration_sec <= hi:
                bad(f"reference voice must be {lo:g}-{hi:g} s long (it is {info.duration_sec:.1f} s)")
        return issues

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        python, root = self._paths(ctx)
        assert python is not None and root is not None
        p = job.params
        argv = [str(python), "inference.py", "--source", str(_abs(job.inputs["audio"], ctx)),
                "--target", str(_abs(job.inputs["voice_ref"]["audio"], ctx)), "--output", str(ctx.work_dir / "out")]
        for key, flag in _NUM_FLAGS:
            if key in p:
                argv += [flag, str(p[key])]
        for key, flag in _BOOL_FLAGS:
            if key in p:
                argv += [flag, "true" if p[key] else "false"]
        env = dict(_ENV)
        if ctx.settings.tools.get("seedvc", {}).get("offline", True):
            env["HF_HUB_OFFLINE"] = "1"
        return Command(argv, cwd=root, env=env)

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        return (0.9, "converting") if "it/s" in log_text or "Loading" in log_text else (0.05, "loading model")

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        out = sorted((ctx.work_dir / "out").glob("*.wav"), key=lambda p: p.stat().st_mtime)
        if not out:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "Seed-VC produced no audio")
        p = job.params
        meta: dict[str, Any] = {
            "source": Path(str(job.inputs["audio"])).name,
            "reference": Path(str(job.inputs["voice_ref"]["audio"])).name,
        }
        keep = ("diffusion_steps", "length_adjust", "inference_cfg_rate", "f0_condition")
        meta.update({k: p[k] for k in keep if k in p})
        return [ArtifactSpec(out[-1], metadata=meta)]
