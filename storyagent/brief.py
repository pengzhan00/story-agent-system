"""The plan made up front: what kind of work, how many seasons and episodes, how long, and what it will cost to make.

``resolve_brief`` turns the creator's answers into a validated brief (defaults and ranges come from the kind in
``kinds.toml``); ``estimate`` counts the tasks that brief implies for the chosen pipeline plan and how much machine time
they take (the ledger's measured times where it has them, the fallbacks the production plan uses otherwise).
Nothing here touches the database.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from storyagent.compose.pacing import shot_count
from storyagent.config import ConfigError, Settings

MAX_SEASONS = 50
MAX_TEXT = 200


def _int(where: str, value: Any, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{where} must be a whole number") from None
    if not lo <= n <= hi:
        raise ConfigError(f"{where} must be between {lo} and {hi}, got {n}")
    return n


def resolve_brief(settings: Settings, kind_name: str = "", answers: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The brief of a project: kind, seasons, episodes per season, episode length, audience, platform (+ totals)."""
    answers = answers or {}
    kind = settings.kind(kind_name)
    seasons = answers.get("seasons")
    if not kind.seasons:
        if seasons not in (None, "", 1):
            raise ConfigError(f"kind {kind.name!r} has no seasons")
        seasons = 1
    else:
        seasons = _int("seasons", 1 if seasons in (None, "") else seasons, 1, MAX_SEASONS)
    given = answers.get("episodes_per_season")
    episodes = _int("episodes_per_season", kind.episodes.default if given in (None, "") else given,
                    int(kind.episodes.min), int(kind.episodes.max))
    sec = answers.get("episode_sec")
    sec = float(kind.episode_sec.default) if sec in (None, "", 0) else float(sec)
    if not kind.episode_sec.clamp_check(sec):
        raise ConfigError(
            f"episode_sec must be between {kind.episode_sec.min:g} and {kind.episode_sec.max:g}, got {sec:g}")
    text = {}
    for key in ("audience", "platform", "logline"):
        value = str(answers.get(key) or "").strip()
        if len(value) > MAX_TEXT:
            raise ConfigError(f"{key} is longer than {MAX_TEXT} characters")
        text[key] = value
    return {"kind": kind.name, "seasons": seasons, "episodes_per_season": episodes, "episode_sec": sec, **text,
            "episodes_total": seasons * episodes, "total_sec": seasons * episodes * sec}


def estimate(settings: Settings, ledger: Any, brief: Mapping[str, Any], pipeline: str = "") -> dict[str, Any]:
    """Tasks and machine time the brief needs under the plan. ``known`` says whether the time per task is measured."""
    from storyagent.workflow.media import FALLBACK_SECONDS

    eff = settings.for_pipeline(pipeline or settings.kind(str(brief.get("kind", ""))).pipeline)
    cfg = eff.delivery.get("estimate", {})
    tier = str(eff.workflow.get("tier", "preview"))
    episodes = int(brief["episodes_total"])
    shots_each = shot_count(float(brief["episode_sec"]), float(eff.delivery["pacing"]["avg_shot_sec"]))
    shots = episodes * shots_each
    share = float(eff.workflow.get("video_share", 0.2))
    clips_each = min(shots_each, max(int(eff.workflow.get("video_min_per_episode", 2)), round(shots_each * share)))
    keyframe_kind = "image.edit" if eff.workflow.get("keyframe_from_references", True) else "image.generate"
    seasons = int(brief["seasons"])
    wanted: list[tuple[str, int]] = [
        ("image.generate", int(cfg.get("characters_per_work", 4)) + seasons * int(cfg.get("locations_per_season", 3))),
        (keyframe_kind, shots),
        ("video.generate", episodes * clips_each),
        ("audio.tts", round(shots * float(cfg.get("lines_per_shot", 1.5)))),
        ("audio.sfx", round(shots * float(cfg.get("sfx_per_shot", 1.0)))),
        ("audio.music", episodes),
        ("compose.assemble", episodes),
    ]
    tasks: list[dict[str, Any]] = []
    for kind, count in wanted:
        if count <= 0 or kind not in eff.default_models:
            continue
        ref = eff.default_model(kind).ref
        known = int(ledger.estimate_seconds(ref, tier)) if ledger is not None else 0
        each = known or int(FALLBACK_SECONDS.get(kind) or FALLBACK_SECONDS.get(kind.split(".")[0], 30.0))
        tasks.append({"kind": kind, "model_ref": ref, "count": count, "each_sec": each, "known": bool(known),
                      "total_sec": each * count})
    total = sum(t["total_sec"] for t in tasks)
    budget = float(eff.workflow.get("video_budget_hours", 0) or 0)
    clip_hours = sum(t["total_sec"] for t in tasks if t["kind"] == "video.generate") / 3600
    return {"pipeline": eff.default_pipeline, "tier": tier, "episodes": episodes, "shots": shots, "tasks": tasks,
            "total_sec": total, "hours": round(total / 3600, 1),
            "video_budget_hours": budget or None, "over_budget": bool(budget and clip_hours > budget)}
