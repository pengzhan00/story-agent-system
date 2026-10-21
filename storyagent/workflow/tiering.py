"""Which shots get a video clip and which stay a keyframe with a slow move (shot tiering).

Machine time decides this: a clip costs minutes to hours, a keyframe seconds. A whole season cannot be all clips, so the
policy picks the shots where motion pays off - the opening hook, the closing cliffhanger, the first shot of each act,
shots with a camera move - up to a share of the episode and, optionally, a series-wide budget of GPU hours. The
creator can overrule it per shot (``shots.treatment`` = video | still); ``auto`` leaves it to the policy.

Pure functions over shot rows: no database, no clock.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

TREATMENTS = ("auto", "video", "still")
STATIC_MOVES = {"", "static", "still", "locked", "locked off", "none"}


@dataclass(frozen=True)
class VideoPolicy:
    share: float = 0.2                  # fraction of an episode's shots that get a clip
    min_per_episode: int = 2
    budget_sec: float | None = None     # series-wide machine time for clips (None = no limit)
    clip_sec: float = 240.0             # what one clip is expected to cost


def policy_from(
    workflow: Mapping[str, Any], project_settings: Mapping[str, Any] | None, clip_sec: float,
) -> VideoPolicy:
    """``[workflow] video_share / video_min_per_episode / video_budget_hours``, each overridable per project."""
    mine = project_settings or {}

    def pick(key: str, default: Any) -> Any:
        value = mine.get(key)
        return workflow.get(key, default) if value is None else value

    hours = float(pick("video_budget_hours", 0) or 0)
    return VideoPolicy(
        share=min(max(float(pick("video_share", 0.2)), 0.0), 1.0),
        min_per_episode=max(int(pick("video_min_per_episode", 2)), 0),
        budget_sec=hours * 3600 if hours > 0 else None, clip_sec=max(float(clip_sec), 1.0))


def score(shot: Mapping[str, Any], index: int, shots: Sequence[Mapping[str, Any]]) -> float:
    """How much a shot gains from moving. Order is the tie-breaker (earlier wins)."""
    s = 0.0
    if index == 0:
        s += 5          # the hook
    if index == len(shots) - 1:
        s += 4          # the cliffhanger
    prev = shots[index - 1] if index else None
    if prev is None or prev["act_number"] != shot["act_number"]:
        s += 3          # an act opens
    elif prev["scene_number"] != shot["scene_number"]:
        s += 1.5        # a scene opens
    move = str(((shot.get("render_payload") or {}).get("camera") or {}).get("movement", "")).strip().lower()
    if move not in STATIC_MOVES:
        s += 1
    return s - index * 1e-6


def video_shots(episodes: Mapping[int, Sequence[Mapping[str, Any]]], policy: VideoPolicy) -> set[int]:
    """Ids of the shots that get a clip. ``episodes``: episode id -> its shots in story order."""
    forced: set[int] = set()
    ranked: list[tuple[float, int]] = []
    for shots in episodes.values():
        mine = [s for s in shots if s.get("treatment", "auto") != "still"]
        want = max(policy.min_per_episode, math.ceil(policy.share * len(shots) - 1e-9))
        have = [s for s in mine if s.get("treatment") == "video"]
        forced.update(int(s["id"]) for s in have)
        auto = sorted(((score(s, shots.index(s), shots), int(s["id"])) for s in mine
                       if s.get("treatment", "auto") == "auto"), key=lambda x: -x[0])
        ranked.extend(auto[: max(want - len(have), 0)])
    chosen = forced | {i for _, i in ranked}
    if policy.budget_sec is not None:
        room = max(int(policy.budget_sec // policy.clip_sec) - len(forced), 0)
        keep = {i for _, i in sorted(ranked, key=lambda x: -x[0])[:room]}
        chosen = forced | keep
    return chosen
