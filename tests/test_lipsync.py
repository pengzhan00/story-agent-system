from __future__ import annotations

import math

import pytest

from storyagent.media.lipsync import frames_for


@pytest.mark.parametrize("seconds,fps", [(0.01, 24), (0.5, 24), (0.875, 24), (3.5, 24), (5, 24), (1.2, 30)])
def test_rendered_frames_cover_requested_audio_interval(seconds, fps):
    frames = frames_for(seconds, fps)
    assert frames % 8 == 1
    assert frames >= math.ceil(seconds * fps)
    assert frames < math.ceil(seconds * fps) + 10
