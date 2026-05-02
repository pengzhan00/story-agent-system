"""
Quality Gate — 自动质检 render / composite 产物
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class QualityCheckResult:
    passed: bool
    stage: str
    checks: list[str]
    errors: list[str]
    metrics: dict


def _ffprobe_json(path: str) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_streams", "-show_format",
            "-of", "json", path,
        ],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-500:] or "ffprobe failed")
    return json.loads(result.stdout or "{}")


def _duration(path: str) -> float:
    try:
        meta = _ffprobe_json(path)
        return float(meta.get("format", {}).get("duration") or 0.0)
    except Exception:
        return 0.0


def _has_audio_stream(path: str) -> bool:
    try:
        meta = _ffprobe_json(path)
    except Exception:
        return False
    return any(s.get("codec_type") == "audio" for s in meta.get("streams", []))


def _video_stream_metrics(path: str) -> dict:
    meta = _ffprobe_json(path)
    for stream in meta.get("streams", []):
        if stream.get("codec_type") == "video":
            fps_raw = stream.get("avg_frame_rate") or "0/1"
            try:
                num, den = fps_raw.split("/")
                fps = float(num) / max(float(den), 1.0)
            except Exception:
                fps = 0.0
            return {
                "width": int(stream.get("width") or 0),
                "height": int(stream.get("height") or 0),
                "fps": fps,
                "nb_frames": int(stream.get("nb_frames") or 0),
            }
    return {"width": 0, "height": 0, "fps": 0.0, "nb_frames": 0}


def _black_ratio(path: str) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", path,
            "-vf", "blackdetect=d=0.1:pic_th=0.98:pix_th=0.10",
            "-an", "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=60,
    )
    text = (result.stderr or "")[-8000:]
    total = _duration(path)
    if total <= 0:
        return 1.0
    black = 0.0
    for line in text.splitlines():
        if "black_duration:" in line:
            try:
                black += float(line.split("black_duration:")[-1].strip().split()[0])
            except Exception:
                pass
    return min(1.0, black / total)


def _has_freeze(path: str, freeze_dur: float = 1.5) -> bool:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", path,
            "-vf", f"freezedetect=n=0.001:d={freeze_dur}",
            "-an", "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=60,
    )
    return "freeze_start" in (result.stderr or "")


def _mean_volume_db(path: str) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", path,
            "-af", "volumedetect",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=60,
    )
    text = result.stderr or ""
    for line in text.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[-1].split(" dB")[0].strip())
            except Exception:
                pass
    return -120.0


def validate_render_output(path: str, min_duration: float = 1.0) -> QualityCheckResult:
    checks: list[str] = []
    errors: list[str] = []
    p = Path(path)
    metrics = {"path": path}
    if not p.exists() or p.stat().st_size < 4096:
        errors.append("输出文件不存在或过小")
        return QualityCheckResult(False, "render", checks, errors, metrics)

    dur = _duration(path)
    video = _video_stream_metrics(path)
    black_ratio = _black_ratio(path)
    freeze = _has_freeze(path)
    metrics.update({"duration": dur, **video, "black_ratio": black_ratio, "freeze_detected": freeze})

    checks.append("file_exists")
    if dur < min_duration:
        errors.append(f"视频时长过短: {dur:.2f}s")
    else:
        checks.append("duration_ok")
    if video["width"] < 320 or video["height"] < 180:
        errors.append(f"视频分辨率过低: {video['width']}x{video['height']}")
    else:
        checks.append("resolution_ok")
    if black_ratio > 0.85:
        errors.append(f"黑帧比例过高: {black_ratio:.2%}")
    else:
        checks.append("black_ratio_ok")
    if freeze and dur >= 2.0:
        errors.append("检测到长时间静帧/冻结")
    else:
        checks.append("motion_ok")
    return QualityCheckResult(len(errors) == 0, "render", checks, errors, metrics)


def validate_composite_output(
    path: str,
    require_audio: bool = True,
    subtitle_expected: bool = False,
    min_duration: float = 1.0,
) -> QualityCheckResult:
    checks: list[str] = []
    errors: list[str] = []
    base = validate_render_output(path, min_duration=min_duration)
    checks.extend(base.checks)
    errors.extend(base.errors)
    metrics = dict(base.metrics)

    has_audio = _has_audio_stream(path)
    mean_volume = _mean_volume_db(path) if has_audio else -120.0
    metrics.update({"has_audio": has_audio, "mean_volume_db": mean_volume, "subtitle_expected": subtitle_expected})

    if require_audio and not has_audio:
        errors.append("成片缺少音频流")
    elif has_audio:
        checks.append("audio_stream_ok")

    if require_audio and has_audio and mean_volume < -45.0:
        errors.append(f"音量过低: {mean_volume:.1f} dB")
    elif has_audio:
        checks.append("audio_volume_ok")

    if subtitle_expected:
        checks.append("subtitle_expected")

    return QualityCheckResult(len(errors) == 0, "composite", checks, errors, metrics)
