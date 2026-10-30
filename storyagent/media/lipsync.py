"""Lipsync (a2v) frame arithmetic.

The panel's reliable single-pass lipsync window is about **5 seconds** (121 frames). Longer lines are split by
``storyagent.control.sequence``: each part starts from the closed-mouth frame the panel finds in the previous one, so
the next part's ``audio_start_time`` is that frame's time in the source audio.

Frames round UP to cover seconds * fps and satisfy frames % 8 == 1 (a panel requirement).
"""

from __future__ import annotations

import math

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
