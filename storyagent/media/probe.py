"""ffprobe wrapper. Absence of ffprobe degrades gracefully: callers get ``None`` and log a warning."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from storyagent.config import Settings


@dataclass(frozen=True)
class MediaInfo:
    duration_sec: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool | None = None
    has_video: bool = False
    format_name: str = ""


def find_tool(settings: Settings, tool: str) -> Path | None:
    """Locate ffmpeg/ffprobe: configured path first, then PATH."""
    configured = settings.tool_path("ffmpeg", "binary" if tool == "ffmpeg" else "ffprobe")
    if configured and configured.is_file():
        return configured
    found = shutil.which(tool)
    return Path(found) if found else None


def _fps(rate: str) -> float | None:
    try:
        num, _, den = rate.partition("/")
        return round(float(num) / float(den or 1), 3) if float(den or 1) else None
    except (ValueError, ZeroDivisionError):
        return None


def probe(settings: Settings, path: Path, *, timeout: float = 30.0) -> MediaInfo | None:
    """``None`` = ffprobe unavailable or the file could not be parsed as media."""
    ffprobe = find_tool(settings, "ffprobe")
    if ffprobe is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [str(ffprobe), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    dur = fmt.get("duration") or (video or audio or {}).get("duration")
    try:
        duration = float(dur) if dur is not None else None
    except ValueError:
        duration = None
    return MediaInfo(
        duration_sec=duration,
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        fps=_fps(video.get("avg_frame_rate", "")) if video else None,
        has_audio=audio is not None,
        has_video=video is not None,
        format_name=fmt.get("format_name", ""),
    )
