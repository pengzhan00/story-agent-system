from __future__ import annotations

import math

import pytest

from storyagent.media.lipsync import frames_for, plan_parts


@pytest.mark.parametrize("seconds,fps", [(0.01, 24), (0.5, 24), (0.875, 24), (3.5, 24), (5, 24), (1.2, 30)])
def test_rendered_frames_cover_requested_audio_interval(seconds, fps):
    frames = frames_for(seconds, fps)
    assert frames % 8 == 1
    assert frames >= math.ceil(seconds * fps)
    assert frames < math.ceil(seconds * fps) + 10


def test_last_short_lipsync_part_is_not_rounded_below_its_timeline_length():
    parts = plan_parts(3.5, max_part_seconds=3)
    assert parts[-1].seconds == pytest.approx(0.55)
    assert parts[-1].frames == 17


def test_overlap_stays_inside_the_reliable_single_render_window():
    parts = plan_parts(10)
    assert all(p.seconds <= 5 and p.frames <= 121 for p in parts)
    assert parts[1].audio_start == pytest.approx(4.95)
    assert parts[-1].start_in_clip + parts[-1].seconds == pytest.approx(10)
