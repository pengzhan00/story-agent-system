"""Continuity: which costume a character wears at a point in the story, and which props a shot shows.

Pure functions over the rows of ``character_looks`` / ``props``; the shot plan calls them so every shot payload (and
through it every prompt) carries the same words for the same costume and the same prop."""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def look_for(looks: Iterable[Mapping[str, Any]], character_id: int, episode: int, act: int, scene: int
             ) -> Mapping[str, Any] | None:
    """The look in force at (episode, act, scene): the latest one of this character that starts at or before it."""
    here = (episode, act, scene)
    best: Mapping[str, Any] | None = None
    best_key: tuple[int, int, int, int] = (0, 0, 0, 0)
    for lk in looks:
        if lk["character_id"] != character_id:
            continue
        start = (int(lk["from_episode"]), int(lk["from_act"]), int(lk["from_scene"]))
        if start <= here and (*start, int(lk["id"])) >= best_key:
            best, best_key = lk, (*start, int(lk["id"]))
    return best


def props_in(props: Sequence[Mapping[str, Any]], texts: Iterable[str], episode: int) -> list[dict[str, str]]:
    """Props (already introduced by ``episode``) whose name occurs in the shot's text, case-insensitively."""
    haystack = " ".join(t for t in texts if t).lower()
    out = []
    for p in props:
        name = str(p["name"]).strip()
        if name and int(p["introduced_episode"]) <= episode and name.lower() in haystack:
            out.append({"name": name, "description": str(p["description"])})
    return out
