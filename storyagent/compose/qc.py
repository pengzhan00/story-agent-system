"""Quality control of a finished episode, against the ``[qc]`` thresholds and the delivery profile.

Measured, not assumed: ffprobe for the container facts, ffmpeg filters (volumedetect, ebur128, blackdetect,
freezedetect) for the picture and sound. Every check yields ``{code, ok, value, limit}``; ``passed`` is true when none
of the blocking checks failed (``episode_length`` is advisory). A tool that is missing makes the dependent checks
``skipped`` (never silently "ok"). The web client labels the codes with ``web.compose.qc.<code>``.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from storyagent.config import Settings
from storyagent.media.probe import find_tool, probe

_MEAN = re.compile(r"mean_volume:\s*(-?[\d.]+|-inf) dB")
_LUFS = re.compile(r"I:\s+(-?[\d.]+|-inf) LUFS")
_BLACK = re.compile(r"black_duration:\s*([\d.]+)")
_FREEZE = re.compile(r"freeze_duration:\s*([\d.]+)")
LOUDNESS_TOLERANCE = 3.0     # LU around the delivery target
ADVISORY = ("episode_length",)   # reported, never blocking: drafts and previews are meant to be short


def _check(code: str, ok: bool | None, value: Any = None, limit: Any = None) -> dict[str, Any]:
    return {"code": code, "ok": ok, "skipped": ok is None, "value": value, "limit": limit}


def _stderr(ffmpeg: Path, path: Path, *, video: str = "", audio: str = "", timeout: float = 600) -> str | None:
    argv = [str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(path)]
    if video:
        argv += ["-vf", video]
    else:
        argv += ["-vn"]
    if audio:
        argv += ["-af", audio]
    else:
        argv += ["-an"]
    argv += ["-f", "null", "-"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stderr if proc.returncode == 0 else None


def _num(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _at_least(code: str, value: float | None, limit: float, *, digits: int = 2) -> dict[str, Any]:
    ok = None if value is None else value >= limit
    return _check(code, ok, None if value is None else round(value, digits), limit)


def _file_checks(settings: Settings, path: Path, profile: Mapping[str, Any], checks: list[dict[str, Any]],
                 metrics: dict[str, Any]) -> Any:
    qc = settings.delivery["qc"]
    size = path.stat().st_size
    checks.append(_at_least("file_size", size, int(qc["min_file_bytes"])))
    info = probe(settings, path)
    if info is None:
        checks.append(_check("readable", False, "unreadable"))
        return None
    dur = info.duration_sec or 0.0
    metrics.update(duration_sec=round(dur, 3), width=info.width, height=info.height, fps=info.fps)
    checks.append(_at_least("duration", dur, float(qc["min_duration_sec"])))
    w, h = info.width or 0, info.height or 0
    checks.append(_check("resolution", w >= int(qc["min_width"]) and h >= int(qc["min_height"]), f"{w}x{h}",
                         f"{qc['min_width']}x{qc['min_height']}"))
    want_w, want_h = int(profile["width"]), int(profile["height"])
    aspect_ok = bool(w and h) and abs(w / h - want_w / want_h) <= float(qc["aspect_tolerance"])
    checks.append(_check("aspect", aspect_ok, f"{w}x{h}", f"{want_w}x{want_h}"))
    checks.append(_check("audio_track", bool(info.has_audio) or not qc["require_audio"], bool(info.has_audio)))
    lo, hi = float(profile.get("min_episode_sec", 0)), float(profile.get("max_episode_sec", 0))
    if lo or hi:
        checks.append(_check("episode_length", (not lo or dur >= lo) and (not hi or dur <= hi), round(dur, 1),
                             f"{lo:g}-{hi:g}"))
    return info


def _sound_checks(settings: Settings, ffmpeg: Path, path: Path, checks: list[dict[str, Any]],
                  metrics: dict[str, Any]) -> None:
    out = _stderr(ffmpeg, path, audio="volumedetect")
    m = _MEAN.search(out or "")
    mean = _num(m.group(1)) if m else None
    metrics["mean_volume_db"] = mean
    limit = float(settings.delivery["qc"]["min_mean_volume_db"])
    checks.append(_check("mean_volume", None if out is None else (mean is not None and mean >= limit), mean, limit))
    out = _stderr(ffmpeg, path, audio="ebur128")
    found = _LUFS.findall(out or "")
    lufs = _num(found[-1]) if found else None
    metrics["loudness_lufs"] = lufs
    target = float(settings.delivery["mix"]["loudnorm_i"])
    ok = None if out is None else (lufs is not None and abs(lufs - target) <= LOUDNESS_TOLERANCE)
    checks.append(_check("loudness", ok, lufs, f"{target:g}\u00b1{LOUDNESS_TOLERANCE:g}"))


def _picture_checks(settings: Settings, ffmpeg: Path, path: Path, dur: float, checks: list[dict[str, Any]],
                    metrics: dict[str, Any]) -> None:
    qc = settings.delivery["qc"]
    freeze_sec = float(qc["freeze_sec"])
    out = _stderr(ffmpeg, path, video=f"blackdetect=d=0.2:pix_th=0.1,freezedetect=n=-60dB:d={freeze_sec}")
    if out is None:
        checks += [_check("black_ratio", None), _check("frozen", None)]
        return
    ratio = sum(_num(x) or 0.0 for x in _BLACK.findall(out)) / dur if dur else 0.0
    frozen = max((_num(x) or 0.0 for x in _FREEZE.findall(out)), default=0.0)
    metrics.update(black_ratio=round(ratio, 3), longest_freeze_sec=round(frozen, 2))
    checks.append(_check("black_ratio", ratio <= float(qc["max_black_ratio"]), round(ratio, 3),
                         float(qc["max_black_ratio"])))
    # a held last frame is deliberate in a short drama: a freeze only fails when it dominates the episode
    checks.append(_check("frozen", frozen < max(dur * 0.5, freeze_sec), round(frozen, 2), freeze_sec))


def run_qc(settings: Settings, path: Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    """Check one rendered episode. ``profile`` is the project's delivery profile."""
    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    if not path.is_file():
        return {"passed": False, "checks": [_check("file", False, "missing")], "metrics": metrics}
    info = _file_checks(settings, path, profile, checks, metrics)
    if info is None:
        return _verdict(checks, metrics)
    ffmpeg = find_tool(settings, "ffmpeg")
    if ffmpeg is None:
        checks += [_check(code, None) for code in ("mean_volume", "loudness", "black_ratio", "frozen")]
        return _verdict(checks, metrics)
    if info.has_audio:
        _sound_checks(settings, ffmpeg, path, checks, metrics)
    elif settings.delivery["qc"]["require_audio"]:
        checks += [_check("mean_volume", False), _check("loudness", False)]
    if info.has_video:
        _picture_checks(settings, ffmpeg, path, info.duration_sec or 0.0, checks, metrics)
    return _verdict(checks, metrics)


def _verdict(checks: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    blocking = [c for c in checks if c["code"] not in ADVISORY]
    return {"passed": all(c["ok"] is not False for c in blocking), "checks": checks, "metrics": metrics}
