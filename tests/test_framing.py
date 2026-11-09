"""Shot grammar applied to a scene's shots as they are written."""
from __future__ import annotations

from typing import Any

from storyagent.config import load_settings
from storyagent.workflow.framing import fix_framing


def _shots(*sizes: str, **extra: Any) -> list[dict[str, Any]]:
    return [{"camera_angle": s, "characters_present": ["a", "b"], **extra} for s in sizes]


def test_a_scene_somewhere_new_opens_wide_and_neighbours_differ() -> None:
    grammar = load_settings().grammar
    shots = _shots("close-up", "close-up", "close-up", "close-up", "medium")
    assert fix_framing(shots, grammar, new_place=True) >= 2
    ranks = [grammar.rank(s["camera_angle"]) for s in shots]
    assert ranks[0] is not None and ranks[0] <= grammar.sizes.index("full")           # it opens wide
    assert all(abs(a - b) >= 1 for a, b in zip(ranks, ranks[1:], strict=False))      # no two neighbours alike


def test_a_scene_in_the_same_place_keeps_its_first_shot() -> None:
    grammar = load_settings().grammar
    shots = _shots("close-up", "medium")
    fix_framing(shots, grammar, new_place=False)
    assert shots[0]["camera_angle"] == "close-up"


def test_one_person_keeps_one_screen_direction_across_a_cut() -> None:
    grammar = load_settings().grammar
    shots = _shots("wide", "medium", "close-up")
    for shot, way in zip(shots, ("left", "right", "left"), strict=True):
        shot["screen_direction"] = way
    fix_framing(shots, grammar, new_place=False)
    assert [s["screen_direction"] for s in shots] == ["left", "left", "left"]


def test_a_size_it_does_not_know_is_left_alone() -> None:
    grammar = load_settings().grammar
    shots = _shots("dutch tilt", "dutch tilt")
    assert fix_framing(shots, grammar, new_place=True) == 0
