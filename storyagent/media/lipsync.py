"""Lipsync (a2v) capability: part planning and audio_start derivation.

Why a separate module:

1) The panel's reliable single-pass lipsync window is about **5 seconds** (121 frames). The
   front-end lipsync splits at 5 s and uses the **closed-mouth frame** of the previous part as
   the anchor image for the next one. Handing a 10 s line straight to the panel does work
   (241 frames renders), but the longer the part, the more the mouth drifts against the audio.
2) `audio_start_time` has to come from the **final timeline**, not from a guess. This module
   derives "the cumulative start second of this shot in the cut"; the scheduler only calls it.

Conventions:

- frames round UP to cover seconds * fps, and satisfy frames % 8 == 1 (panel requirement)
- part N+1 uses the last frame of part N as its image (anchor); re-using the portrait instead
  makes the mouth jump at the seam.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_MAX_PART_SECONDS = 5.0
DEFAULT_OVERLAP_SECONDS = 0.05
DEFAULT_FPS = 24
FRAME_MOD = 8  # frames % 8 == 1


def frames_for(seconds: float, fps: int = DEFAULT_FPS) -> int:
    """Round up to 8k+1 so the rendered picture covers the requested interval.

    Rounding down made a 0.5-second tail just 9 frames (0.375 s at 24 fps).
    The final audio then hid the missing video duration in the container's length.
    """
    if not math.isfinite(seconds) or seconds <= 0 or fps <= 0:
        raise ValueError("seconds and fps must be positive and finite")
    return max(math.ceil(seconds * fps / FRAME_MOD) * FRAME_MOD + 1, FRAME_MOD + 1)


@dataclass(frozen=True)
class LipsyncPart:
    index: int  # 1-based
    start_in_clip: float  # start of this part inside the shot, seconds
    seconds: float  # part duration in seconds (overlap included)
    audio_start: float  # value for the panel's audio_start_time, in song coordinates
    frames: int
    needs_anchor: bool  # True = use the previous part's last frame as the image


def plan_parts(
    total_seconds: float,
    audio_start: float = 0.0,
    max_part_seconds: float = DEFAULT_MAX_PART_SECONDS,
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS,
    fps: int = DEFAULT_FPS,
) -> list[LipsyncPart]:
    """Split one singing/speaking shot into parts the panel can hold.

    ``overlap_seconds`` gives adjacent parts a tiny overlap (trimmed when stitching) so the
    mouth does not freeze at a seam.
    """
    if total_seconds <= 0:
        raise ValueError("total_seconds must be > 0")
    if max_part_seconds <= 0:
        raise ValueError("max_part_seconds must be > 0")
    if overlap_seconds < 0 or overlap_seconds >= max_part_seconds:
        raise ValueError("overlap_seconds must be >= 0 and below max_part_seconds")

    parts: list[LipsyncPart] = []
    t = 0.0
    idx = 1
    while t < total_seconds - 1e-6:
        dur = min(max_part_seconds, total_seconds - t)
        last = t + dur >= total_seconds - 1e-6
        # Overlap belongs inside the bounded window, not beyond its five-second ceiling.
        padded = dur
        parts.append(
            LipsyncPart(
                index=idx,
                start_in_clip=round(t, 4),
                seconds=round(padded, 4),
                audio_start=round(audio_start + t, 3),
                frames=frames_for(padded, fps),
                needs_anchor=idx > 1,
            )
        )
        t += dur if last else dur - overlap_seconds
        idx += 1
    return parts


def derive_audio_start(shot_seconds: list[float], index: int, section_offset: float = 0.0) -> float:
    """Timeline derivation: cumulative start of shot ``index`` (0-based) plus an in-shot offset."""
    if index < 0 or index >= len(shot_seconds):
        raise IndexError(f"shot index {index} out of range (0..{len(shot_seconds) - 1})")
    return round(sum(shot_seconds[:index]) + section_offset, 3)
