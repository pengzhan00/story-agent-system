"""Shot grammar applied while the script is written, not only checked afterwards.

The storyboard check (``grammar.py``) lists what breaks the rules of thumb of film practice once the shots exist. The
same rules are applied here to a scene's freshly written shots (the ``camera_angle`` and ``screen_direction`` the
writer gave), so a set piece of twelve shots in one room does not come out as twelve close-ups: the first shot of a
scene somewhere new is wide enough to show where we are, neighbouring shots of the same people differ in size, and
one person does not face opposite ways in neighbouring shots. Only these fields are touched, never the story.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from storyagent.config.grammar import Grammar

from .grammar import _sign


def _another_size(rank: int, before: int | None, after: int | None, count: int, step: int) -> int:
    """A size next to ``rank`` that is at least ``step`` away from both neighbours, the nearest one that is."""
    for jump in (1, -1, 2, -2, 3, -3):
        pick = rank + jump
        if 0 <= pick < count and all(n is None or abs(pick - n) >= step for n in (before, after)):
            return pick
    return rank


def fix_framing(shots: Sequence[dict[str, Any]], grammar: Grammar, *, new_place: bool,
                characters: Mapping[int, Sequence[str]] | None = None) -> int:
    """Repair one scene's shots in place and return how many fields were changed. ``new_place``: the scene is
    somewhere the one before it was not."""
    if not grammar.enabled or not shots:
        return 0
    sizes = grammar.sizes
    ranks = [grammar.rank(str(s.get("camera_angle") or "")) for s in shots]
    changed = 0

    def put(i: int, rank: int) -> None:
        nonlocal changed
        shots[i]["camera_angle"] = sizes[rank]
        ranks[i] = rank
        changed += 1

    rule = grammar.rule("establishing")
    if rule and new_place and ranks[0] is not None and ranks[0] > sizes.index(rule.params["max_size"]):
        put(0, sizes.index(rule.params["fix_size"]))
    jump = grammar.rule("jump_size")
    step = int(jump.params["min_step"]) if jump else 0
    for i in range(1, len(shots)):
        before, here = ranks[i - 1], ranks[i]
        if jump and before is not None and here is not None and abs(here - before) < step:
            ahead = ranks[i + 1] if i + 1 < len(shots) else None
            put(i, _another_size(here, before, ahead, len(sizes), step))
        # one person faces one way across a cut (the line of action)
        if grammar.rule("axis_cross") and set(shots[i - 1].get("characters_present") or []) & set(
                shots[i].get("characters_present") or []):
            d0, d1 = (str(shots[k].get("screen_direction") or "") for k in (i - 1, i))
            if _sign(d0) and _sign(d1) and _sign(d0) != _sign(d1):
                shots[i]["screen_direction"] = d0
                changed += 1
    return changed
