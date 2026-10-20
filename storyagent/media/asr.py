"""Speech-to-text helpers (whisper.cpp ``whisper-cli``): locate the tool, prepare audio, transcribe.

Used by the ``text.asr`` adapter, by CosyVoice (to get a reference transcript) and later by QC (does the
generated line actually say the script?).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from storyagent.config import Settings

from .probe import find_tool


def find_whisper(settings: Settings) -> Path | None:
    configured = settings.tool_path("whisper", "binary")
    if configured and configured.is_file():
        return configured
    name = str(settings.tools.get("whisper", {}).get("binary", "")) or "whisper-cli"
    found = shutil.which(Path(name).name)
    return Path(found) if found else None


def whisper_model(settings: Settings) -> Path | None:
    return settings.tool_path("whisper", "model")


def to_wav16k(settings: Settings, src: Path, dest: Path) -> Path:
    """16 kHz mono PCM, the format whisper.cpp expects."""
    ffmpeg = find_tool(settings, "ffmpeg")
    if ffmpeg is None:
        raise FileNotFoundError("ffmpeg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(ffmpeg), "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dest)],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0 or not dest.is_file():
        raise RuntimeError(proc.stderr[-300:] or "ffmpeg failed")
    return dest


def whisper_argv(settings: Settings, wav: Path, out_prefix: Path, language: str) -> list[str]:
    binary, model = find_whisper(settings), whisper_model(settings)
    if binary is None or model is None:
        raise FileNotFoundError("whisper-cli")
    return [str(binary), "-m", str(model), "-l", language, "-f", str(wav), "-otxt", "-osrt", "-nt",
            "-of", str(out_prefix)]


def transcribe(settings: Settings, audio: Path, work_dir: Path, language: str = "zh", timeout: float = 300.0) -> str:
    """Blocking transcription of a short clip. Raises ``FileNotFoundError``/``RuntimeError`` on failure."""
    wav = to_wav16k(settings, audio, work_dir / "asr_input.wav")
    prefix = work_dir / "asr_out"
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        whisper_argv(settings, wav, prefix, language), capture_output=True, text=True, timeout=timeout, check=False
    )
    txt = prefix.with_suffix(".txt")
    if proc.returncode != 0 or not txt.is_file():
        raise RuntimeError(proc.stderr[-300:] or "whisper-cli failed")
    return " ".join(txt.read_text("utf-8").split())
