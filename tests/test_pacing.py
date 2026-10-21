"""Target length: from a 10 s advert to a feature film, nothing is locked to one format."""
from __future__ import annotations

import pytest

from storyagent.compose.pacing import (
    PacingError,
    duration_window,
    fit_durations,
    parse_target,
    structure_for,
    target_for,
)


def test_target_comes_from_the_run_then_the_project_then_the_profile() -> None:
    profile = {"target_sec": 120}
    assert target_for({}, {}, profile) == 120
    assert target_for({}, {"target_sec": 30}, profile) == 30
    assert target_for({"target_sec": 5400}, {"target_sec": 30}, profile) == 5400
    assert target_for({}, {}, {}) is None


@pytest.mark.parametrize("seconds", [10, 30, 90, 180, 600, 5400, 10800])
def test_every_length_from_an_advert_to_a_feature_gets_a_structure_that_matches(seconds: int) -> None:
    acts, scenes, per_scene = structure_for(seconds, 4.0)
    shots = acts * scenes * per_scene
    assert acts >= 1 and scenes >= 1 and per_scene >= 1
    assert abs(shots * 4.0 - seconds) <= max(seconds * 0.35, 8)      # about as many shots as the length needs
    assert per_scene <= 12                                            # a scene stays a handful of shots


def test_longer_pieces_get_more_acts() -> None:
    assert [structure_for(s)[0] for s in (10, 30, 120, 300, 1800, 10800)] == [1, 2, 3, 4, 5, 6]


def test_nonsense_is_refused_and_empty_means_not_set() -> None:
    assert parse_target(None) is None and parse_target("") is None
    for bad in ("soon", -5, 10**9):
        with pytest.raises(PacingError):
            parse_target(bad)


def test_planned_shot_lengths_are_fitted_to_the_target() -> None:
    fitted = fit_durations([3.0, 3.0, 6.0], 24.0, 2.0, 8.0)
    assert fitted == [6.0, 6.0, 8.0]                                  # scaled, then held inside the shot limits
    assert fit_durations([], 24.0, 2.0, 8.0) == []


def test_the_quality_check_window_is_the_target_plus_minus_a_tolerance() -> None:
    assert duration_window(30, 0.2) == (24.0, 36.0)
