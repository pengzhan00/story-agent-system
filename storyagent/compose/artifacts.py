"""Picture artifacts that a person sees at once and the usual QC filters miss: flicker (brightness pumping from frame
to frame) and horizontal banding (stripes, e.g. the rows across the legs of a walking figure in a portrait video model's
output).

Both are measured on a small grey copy of the picture decoded by ffmpeg (``scale`` + ``format=gray`` -> raw bytes),
with plain Python on the bytes - no numpy, no image library. The numbers are rules of thumb: the limits live in
``delivery.toml [qc]`` and the checks are advisory until they have been calibrated on real output.

* flicker  : the frame's mean brightness swings up and down every other frame by more than ``flicker_amp`` levels
             (0-255). A cut is one jump and a fade is a ramp, so neither counts; an alternating run does.
* banding  : in some block of the frame the rows differ from their neighbours much more than the columns do
             (``stripe_amp`` levels of high-passed row means, and ``stripe_ratio`` times the column variation).
"""
from __future__ import annotations

import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import pstdev
from typing import Any

GRID_X, GRID_Y = 2, 4           # blocks the stripe test looks at separately (stripes are often local)
HP_RADIUS = 3                   # high-pass: value minus the mean of its 2*radius+1 neighbourhood


@dataclass(frozen=True)
class FrameStats:
    mean: float
    stripe: float                # strongest block score: row variation in levels
    stripe_ratio: float          # ... relative to the column variation of the same block


@dataclass
class ArtifactReport:
    fps: float = 0.0
    frames: int = 0
    flicker_windows: list[tuple[float, float]] = field(default_factory=list)      # (start, end) seconds
    banding_windows: list[tuple[float, float]] = field(default_factory=list)
    flicker_sec: float = 0.0
    banding_sec: float = 0.0
    stripe_peak: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"fps": self.fps, "frames": self.frames, "flicker_sec": round(self.flicker_sec, 2),
                "banding_sec": round(self.banding_sec, 2), "stripe_peak": round(self.stripe_peak, 2),
                "flicker_windows": [[round(a, 2), round(b, 2)] for a, b in self.flicker_windows],
                "banding_windows": [[round(a, 2), round(b, 2)] for a, b in self.banding_windows]}


def _highpass_std(values: Sequence[float]) -> float:
    n = len(values)
    if n <= 2 * HP_RADIUS + 1:
        return 0.0
    prefix = [0.0]
    for v in values:
        prefix.append(prefix[-1] + v)
    out = []
    for i in range(n):
        lo, hi = max(i - HP_RADIUS, 0), min(i + HP_RADIUS + 1, n)
        out.append(values[i] - (prefix[hi] - prefix[lo]) / (hi - lo))
    return pstdev(out)


def frame_stats(frame: bytes, w: int, h: int) -> FrameStats:
    """Mean brightness and the strongest horizontal-stripe block of one grey frame (``w*h`` bytes)."""
    bw, bh = w // GRID_X, h // GRID_Y
    best, best_ratio = 0.0, 0.0
    for gy in range(GRID_Y):
        for gx in range(GRID_X):
            x0, y0 = gx * bw, gy * bh
            rows = [sum(frame[(y0 + y) * w + x0:(y0 + y) * w + x0 + bw]) / bw for y in range(bh)]
            cols = [sum(frame[(y0 * w + x0 + x):((y0 + bh) * w):w][:bh]) / bh for x in range(bw)]
            s_h, s_v = _highpass_std(rows), _highpass_std(cols)
            ratio = s_h / (s_v + 0.25)
            if s_h * min(ratio, 4.0) > best * min(best_ratio, 4.0):
                best, best_ratio = s_h, ratio
    return FrameStats(sum(frame) / len(frame), best, best_ratio)


def decode_gray(ffmpeg: Path, path: Path, *, fps: float, w: int, h: int, timeout: float = 600
                ) -> Iterator[bytes]:
    """Frames of ``path`` as grey ``w*h`` byte strings at ``fps`` frames per second."""
    argv = [str(ffmpeg), "-nostdin", "-loglevel", "error", "-i", str(path), "-an", "-vf",
            f"fps={fps:g},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)  # noqa: S603 - fixed argv
    assert proc.stdout is not None
    size = w * h
    try:
        while True:
            chunk = proc.stdout.read(size)
            if len(chunk) < size:
                break
            yield chunk
    finally:
        proc.stdout.close()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()


def _runs(flags: Sequence[bool], fps: float, min_len: int) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    start: int | None = None
    for i, on in enumerate([*flags, False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= min_len:
                out.append((start / fps, i / fps))
            start = None
    return out


def analyse(stats: Sequence[FrameStats], fps: float, cfg: Mapping[str, Any]) -> ArtifactReport:
    """Turn the per-frame numbers into windows of flicker and banding."""
    rep = ArtifactReport(fps=fps, frames=len(stats))
    if not stats or fps <= 0:
        return rep
    amp, window = float(cfg["flicker_amp"]), max(int(float(cfg["flicker_window_sec"]) * fps), 4)
    means = [s.mean for s in stats]
    delta = [means[i] - means[i - 1] for i in range(1, len(means))]
    # an up-down-up-down run of steps that are each at least `amp` levels
    alt = [False] * len(stats)
    for i in range(1, len(delta)):
        if abs(delta[i]) >= amp and abs(delta[i - 1]) >= amp and delta[i] * delta[i - 1] < 0:
            alt[i + 1] = alt[i] = True
    dense = [sum(alt[max(i - window // 2, 0):i + window // 2]) >= window // 2 for i in range(len(alt))]
    rep.flicker_windows = _runs(dense, fps, max(window // 2, 2))
    rep.flicker_sec = sum(b - a for a, b in rep.flicker_windows)
    s_amp, s_ratio = float(cfg["stripe_amp"]), float(cfg["stripe_ratio"])
    striped = [s.stripe >= s_amp and s.stripe_ratio >= s_ratio for s in stats]
    rep.stripe_peak = max((s.stripe for s in stats), default=0.0)
    rep.banding_windows = _runs(striped, fps, max(int(float(cfg["stripe_min_sec"]) * fps), 2))
    rep.banding_sec = sum(b - a for a, b in rep.banding_windows)
    return rep


def measure(ffmpeg: Path, path: Path, cfg: Mapping[str, Any], *, native_fps: float | None = None) -> ArtifactReport:
    fps = min(native_fps or 24.0, float(cfg["artifact_fps"]))
    w, h = int(cfg["artifact_width"]), int(cfg["artifact_height"])
    stats = [frame_stats(f, w, h) for f in decode_gray(ffmpeg, path, fps=fps, w=w, h=h)]
    return analyse(stats, fps, cfg)


def shots_hit(windows: Sequence[tuple[float, float]], segments: Sequence[Mapping[str, Any]]) -> list[int]:
    """Shot ids whose time range a window overlaps (cards, shot id 0, are left out)."""
    hit: list[int] = []
    for seg in segments:
        a, b = float(seg["start"]), float(seg["start"]) + float(seg["duration"])
        sid = int(seg.get("shot_id") or 0)
        if sid and sid not in hit and any(w0 < b and w1 > a for w0, w1 in windows):
            hit.append(sid)
    return hit
