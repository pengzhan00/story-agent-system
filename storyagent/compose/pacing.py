"""Target length of an episode -> how much story to write and how long each shot is planned to be.

Pure functions, no I/O. The target is a number of seconds, anything from a 10 s advert to a three-hour feature:
a project setting
(``projects.settings.target_sec``), a run parameter (``target_sec``) or the delivery profile's ``target_sec``.
Everything else is derived, so changing the target is one number.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Only a sanity check against typos (a day at most): the length itself is never locked to a format.
MIN_TARGET_SEC = 1.0
MAX_TARGET_SEC = 86400.0
MAX_SCENES_PER_ACT = 60


class PacingError(ValueError):
    pass


def parse_target(value: Any) -> float | None:
    """A target in seconds, or ``None`` when not set. Raises ``PacingError`` for nonsense."""
    if value in (None, "", 0):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise PacingError(f"target_sec must be a number of seconds, got {value!r}") from None
    if not MIN_TARGET_SEC <= seconds <= MAX_TARGET_SEC:
        raise PacingError(f"target_sec must be between {MIN_TARGET_SEC:g} and {MAX_TARGET_SEC:g}, got {seconds:g}")
    return seconds


def target_for(
    params: Mapping[str, Any], project_settings: Mapping[str, Any], profile: Mapping[str, Any]
) -> float | None:
    """Run parameter > project setting > the profile's default."""
    for source in (params, project_settings, profile):
        found = parse_target(source.get("target_sec"))
        if found is not None:
            return found
    return None


def shot_count(target_sec: float, avg_shot_sec: float) -> int:
    return max(2, round(target_sec / max(avg_shot_sec, 1.0)))


def structure_for(target_sec: float, avg_shot_sec: float = 4.0) -> tuple[int, int, int]:
    """``(acts, scenes_per_act, shots_per_scene)`` whose product is close to the shot count the target needs.

    More acts for longer pieces (an advert is one act, a series episode three, a feature six); scenes and shots per
    scene follow so that a scene stays a handful of quick shots. Very long pieces end up with many scenes per act:
    the script stage then has to write them in several passes (see docs/SERIES.md)."""
    shots = shot_count(target_sec, avg_shot_sec)
    acts = 1 if target_sec < 20 else 2 if target_sec < 60 else 3 if target_sec < 150 else 4 if target_sec < 600 else (
        5 if target_sec < 3600 else 6)
    per_act = max(1, round(shots / acts))
    scenes = max(1, min(MAX_SCENES_PER_ACT, round(per_act / 3)))
    per_scene = max(1, round(per_act / scenes))
    return acts, scenes, per_scene


def fit_durations(
    planned: list[float], target_sec: float, minimum: float, maximum: float
) -> list[float]:
    """Scale the planned shot lengths so they add up to about the target, each inside ``[minimum, maximum]``."""
    total = sum(planned)
    if not planned or total <= 0:
        return list(planned)
    factor = target_sec / total
    return [round(min(max(d * factor, minimum), maximum), 2) for d in planned]


def duration_window(target_sec: float, tolerance: float) -> tuple[float, float]:
    return round(target_sec * (1 - tolerance), 1), round(target_sec * (1 + tolerance), 1)
