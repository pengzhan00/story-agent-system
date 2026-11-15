"""CosyVoice2 voice cloning / TTS (subprocess, one model load per job).

Inputs:
    text                       one line to speak, or
    lines                      [{"id": "l01", "text": "..."}]  several lines in one model load (one asset each,
                               slot = id)
    voice_ref                  {"audio": <3-20 s wav>, "text": <transcript>, "consent": true}
                               (a missing transcript is produced with whisper unless auto_transcribe=false)
                               Optional: absent reference uses a synthetic macOS system-voice sample automatically.
    instruct                   style instruction for mode=instruct (e.g. "speak softly, with a sad tone")
    prompt                     the line's emotion ("furious", "hesitant"): with mode=zero_shot it is spoken in that tone
                               (the job runs as ``instruct`` with ``emotion_prompt``); ``emotion=false`` turns it off
Params: mode (zero_shot | cross_lingual | instruct), speed (0.5-2.0), output_format (wav | mp3),
        emotion (bool, default true), emotion_prompt (template with {emotion}),
        ref_min_sec / ref_max_sec (reference length window), auto_transcribe, language (for that transcript).
Output: mono wav at the model's sample rate (24 kHz); the real duration is measured by ffprobe when stored,
so the timeline is built from what was actually spoken, not from a character-count guess.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
import uuid
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..asr import find_whisper, transcribe, whisper_model
from ..contracts import AdapterError, ArtifactSpec, Capabilities, JobContext, JobRecord, ValidationIssue
from ..probe import find_tool, probe
from ._subprocess import Command, SubprocessAdapter
from .say import SayAdapter

SCHEMA: dict[str, dict[str, Any]] = {
    "auto_reference": {"type": "bool"},
    "mode": {"type": "str", "choices": ["zero_shot", "cross_lingual", "instruct"]},
    "emotion": {"type": "bool"},
    "emotion_prompt": {"type": "str"},
    "speed": {"type": "float", "min": 0.5, "max": 2.0},
    "output_format": {"type": "str", "choices": ["wav", "mp3"]},
    "ref_min_sec": {"type": "float", "min": 0},
    "ref_max_sec": {"type": "float", "min": 1},
    "auto_transcribe": {"type": "bool"},
    "language": {"type": "str"},
    "sample_rate": {"type": "int"},  # informational: the model decides
}
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
DEFAULT_RUNNER = Path(__file__).resolve().parents[1] / "runners" / "cosyvoice_zero_shot.py"
AUTO_VOICE = tomllib.loads((Path(__file__).resolve().parents[2] / "voices.toml").read_text("utf-8"))["automatic"]


def mode_of(job: JobRecord) -> tuple[str, str]:
    """``(mode, instruct)`` the job really runs with. A line that carries an emotion is spoken in that tone: a plain
    zero-shot job becomes ``instruct`` (the reference voice is kept, the style comes from the emotion text)."""
    mode = str(job.params.get("mode", "zero_shot"))
    instruct = str(job.inputs.get("instruct", "")).strip()
    emotion = str(job.inputs.get("prompt") or "").strip()
    if mode == "zero_shot" and emotion and not instruct and bool(job.params.get("emotion", True)):
        template = str(job.params.get("emotion_prompt") or "Speak in a very {emotion} tone, with strong feeling")
        return "instruct", template.replace("{emotion}", emotion)
    return mode, instruct


def lines_of(job: JobRecord) -> list[dict[str, str]]:
    lines = job.inputs.get("lines")
    if lines:
        return [{"id": str(x.get("id", "")), "text": str(x.get("text", ""))} for x in lines]
    return [{"id": "line", "text": str(job.inputs.get("text", ""))}]


class CosyVoiceAdapter(SubprocessAdapter):
    provider = "cosyvoice"

    def __init__(self, runner: Path | None = None, poll_interval_sec: float = 2.0) -> None:
        super().__init__()
        self.runner = runner or DEFAULT_RUNNER
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.AUDIO_TTS,), heavy=False, supports_cancel=True, supports_reattach=False,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    # -- paths --------------------------------------------------------------------------------
    @staticmethod
    def _paths(ctx: JobContext, job: JobRecord | None = None) -> tuple[Path | None, Path | None, Path | None]:
        """python, work dir, model dir. The model entry's ``model_dir`` (CosyVoice 3) beats ``[tools.cosyvoice]``."""
        s = ctx.settings
        model_dir = s.tool_path("cosyvoice", "model_dir")
        entry = s.models[job.model_ref].extra if job is not None and job.model_ref in s.models else {}
        if entry.get("model_dir"):
            model_dir = Path(str(entry["model_dir"])).expanduser()
        return s.tool_path("cosyvoice", "python"), s.tool_path("cosyvoice", "workdir"), model_dir

    @staticmethod
    def _engine(job: JobRecord, ctx: JobContext) -> str:
        entry = ctx.settings.models[job.model_ref].extra if job.model_ref in ctx.settings.models else {}
        return str(entry.get("engine", "cv2"))

    @staticmethod
    def _ref_audio(job: JobRecord, ctx: JobContext) -> Path | None:
        raw = (job.inputs.get("voice_ref") or {}).get("audio")
        if not raw:
            return None
        p = Path(str(raw)).expanduser()
        return p if p.is_absolute() else ctx.settings.home / p

    @staticmethod
    def _automatic(job: JobRecord) -> bool:
        # An explicit but invalid reference must fail rather than be silently replaced.
        return not (job.inputs.get("voice_ref") or {}).get("audio") and bool(job.params.get("auto_reference", True))

    @staticmethod
    def _say(ctx: JobContext) -> str | None:
        return str(ctx.settings.tools.get("say", {}).get("binary") or SayAdapter._binary() or "") or None

    def _make_reference(self, ctx: JobContext) -> tuple[Path, str, str]:
        binary = self._say(ctx)
        if not binary:
            raise AdapterError(ErrorCode.MISSING_TOOL, "automatic voice needs macOS say", tool="say")
        config = ctx.settings.tools.get("cosyvoice", {})
        voice = str(config.get("automatic_voice") or AUTO_VOICE["voice"])
        text = str(config.get("automatic_voice_text") or AUTO_VOICE["text"])
        key = hashlib.sha256(json.dumps([binary, voice, text, 180, "LEI16@22050"]).encode()).hexdigest()
        cached = ctx.settings.home / "cache" / "tts-references" / f"{key}.wav"
        info = probe(ctx.settings, cached) if cached.is_file() else None
        if info and info.duration_sec is not None and 3 <= info.duration_sec <= 20:
            return cached, text, voice
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        ref = ctx.work_dir / "automatic-reference.wav"
        try:
            result = subprocess.run(
                [binary, "-v", voice, "-r", "180", "-o", str(ref), "--data-format=LEI16@22050", "--", text],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdapterError(ErrorCode.TOOL_FAILED, "could not prepare automatic voice reference",
                               exit_code=-1) from exc
        if result.returncode or not ref.is_file() or ref.stat().st_size == 0:
            raise AdapterError(ErrorCode.TOOL_FAILED, "could not prepare automatic voice reference",
                               exit_code=result.returncode, detail=result.stderr[-200:])
        info = probe(ctx.settings, ref)
        if info is None or info.duration_sec is None or not 3 <= info.duration_sec <= 20:
            raise AdapterError(ErrorCode.INVALID_INPUT, "automatic voice reference must be 3-20 s long",
                               detail="automatic_voice_text")
        cached.parent.mkdir(parents=True, exist_ok=True)
        temp = cached.with_name(f"{key}-{uuid.uuid4().hex}.tmp")
        try:
            shutil.copyfile(ref, temp)
            os.replace(temp, cached)
        finally:
            temp.unlink(missing_ok=True)
        return cached, text, voice

    # -- validation ---------------------------------------------------------------------------
    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        python, workdir, model_dir = self._paths(ctx, job)
        runtime_ok = python and python.is_file() and workdir and workdir.is_dir() and self.runner.is_file()
        if not runtime_ok:
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "CosyVoice runtime not found", {"tool": "cosyvoice"})]
        if model_dir is None or not model_dir.exists():
            name = model_dir.name if model_dir else "CosyVoice"
            return [ValidationIssue(ErrorCode.MISSING_MODEL, f"{name} not found", {"model": name})]
        issues: list[ValidationIssue] = []

        def bad(msg: str) -> None:
            issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, msg, {"detail": msg}))

        lines = lines_of(job)
        ids = [x["id"] for x in lines]
        if not all(x["text"].strip() for x in lines):
            bad("every line needs text")
        if not all(_ID.match(i) for i in ids) or len(set(ids)) != len(ids):
            bad("line ids must be unique and file-safe (letters, digits, _ . -)")
        mode, instruct = mode_of(job)
        if mode == "instruct" and not instruct:
            bad("mode instruct needs an instruct text")
        if self._automatic(job):
            if not self._say(ctx):
                issues.append(ValidationIssue(ErrorCode.MISSING_TOOL, "automatic voice needs macOS say",
                                              {"tool": "say"}))
            return issues
        ref = self._ref_audio(job, ctx)
        if ref is None or not ref.is_file():
            bad(f"reference voice audio not found: {ref}")
            return issues
        info = probe(ctx.settings, ref)
        lo, hi = float(job.params.get("ref_min_sec", 3)), float(job.params.get("ref_max_sec", 20))
        if info and info.duration_sec is not None and not lo <= info.duration_sec <= hi:
            bad(f"reference voice must be {lo:g}-{hi:g} s long (it is {info.duration_sec:.1f} s)")
        has_text = bool((job.inputs.get("voice_ref") or {}).get("text"))
        if mode == "zero_shot" and not has_text:
            model = whisper_model(ctx.settings)
            wants_asr = bool(job.params.get("auto_transcribe", True))
            if not (wants_asr and find_whisper(ctx.settings) and model and model.is_file()):
                bad("zero_shot needs the transcript of the reference voice (voice_ref.text)")
        return issues

    # -- run ----------------------------------------------------------------------------------
    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        python, workdir, model_dir = self._paths(ctx, job)
        assert python and workdir and model_dir
        ref = self._ref_audio(job, ctx)
        ref_text = str((job.inputs.get("voice_ref") or {}).get("text") or "").strip()
        automatic = self._automatic(job)
        voice = ""
        if automatic:
            ref, ref_text, voice = self._make_reference(ctx)
        assert ref is not None
        mode, instruct = mode_of(job)
        if mode == "zero_shot" and not ref_text:
            try:
                ref_text = transcribe(ctx.settings, ref, ctx.work_dir, str(job.params.get("language", "zh")))
            except (FileNotFoundError, RuntimeError, subprocess.SubprocessError) as exc:
                raise AdapterError(ErrorCode.TOOL_FAILED, f"could not transcribe the reference voice: {exc}",
                                   exit_code=-1) from exc
            ctx.log("ref_transcribed", text=ref_text[:120])
        cfg = {
            "root": str(workdir), "model_dir": str(model_dir), "engine": self._engine(job, ctx), "mode": mode,
            "ref_wav": str(ref), "ref_text": ref_text, "instruct": instruct,
            "speed": float(job.params.get("speed", 1.0)), "seed": job.seed, "out_dir": str(ctx.work_dir / "out"),
            "items": lines_of(job),
            "voice_source": "synthetic_system_reference" if automatic else "explicit_reference",
            "system_voice": voice,
        }
        (ctx.work_dir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
        return super().submit(job, ctx)

    def command(self, job: JobRecord, ctx: JobContext) -> Command:
        python, workdir, _ = self._paths(ctx)
        assert python is not None
        return Command([str(python), str(self.runner), str(ctx.work_dir / "config.json")], cwd=workdir)

    def progress(self, job: JobRecord, log_text: str) -> tuple[float, str]:
        total = len(lines_of(job))
        done = log_text.count('"event": "item"')
        if '"event": "loaded"' not in log_text:
            return 0.02, "loading model"
        return 0.05 + 0.9 * done / total, f"{done}/{total} lines"

    def collect(self, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        lines = lines_of(job)
        fmt = str(job.params.get("output_format", "wav"))
        cfg = json.loads((ctx.work_dir / "config.json").read_text("utf-8"))
        out: list[ArtifactSpec] = []
        for line in lines:
            wav = ctx.work_dir / "out" / f"{line['id']}.wav"
            if not wav.is_file():
                raise AdapterError(ErrorCode.OUTPUT_MISSING, f"no audio for line {line['id']}", path=str(wav))
            final = self._convert(wav, ctx) if fmt == "mp3" else wav
            slot = job.slot if len(lines) == 1 else line["id"]
            out.append(ArtifactSpec(final, slot=slot, metadata={
                "text": line["text"], "mode": cfg["mode"], "speed": cfg["speed"],
                "ref_text": cfg["ref_text"], "ref_audio": cfg["ref_wav"],
                "voice_source": cfg["voice_source"], "system_voice": cfg["system_voice"],
            }))
        return out

    @staticmethod
    def _convert(wav: Path, ctx: JobContext) -> Path:
        ffmpeg = find_tool(ctx.settings, "ffmpeg")
        if ffmpeg is None:
            raise AdapterError(ErrorCode.MISSING_TOOL, "ffmpeg is needed for mp3 output", tool="ffmpeg")
        dest = wav.with_suffix(".mp3")
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [str(ffmpeg), "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "2", str(dest)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0 or not dest.is_file():
            raise AdapterError(ErrorCode.TOOL_FAILED, proc.stderr[-200:], exit_code=proc.returncode)
        return dest
