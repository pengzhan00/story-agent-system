"""The series board: every season and episode of a project with its stage states and how much media is made.

One read model for the dashboard. Every number is derived from the same tables the drill-down views read (episodes,
shots, specs, the asset ledger, stage states), so a count on the board always matches the list behind it.
"""
from __future__ import annotations

from typing import Any

from storyagent.config import Settings
from storyagent.media.ledger import Ledger
from storyagent.media.specs import SpecStore
from storyagent.story import StoryStore

from .media import current_asset, up_to_date
from .state import WorkflowState

KEYFRAME, VIDEO = "shot.keyframe", "shot.video"


def _made(
    specs: SpecStore, ledger: Ledger, settings: Settings | None, project_id: int, subject_type: str,
) -> dict[str, bool]:
    """subject_id -> True when its current asset is up to date (False = outdated). Judged exactly as the production
    plan judges it (``up_to_date``), so the board's counts and the plan's list agree."""
    out: dict[str, bool] = {}
    for spec in specs.list_current(project_id):
        if spec.subject_type == subject_type and current_asset(ledger, spec) is not None:
            out[spec.subject_id] = up_to_date(ledger, spec, settings)
    return out


def series_board(
    story: StoryStore, state: WorkflowState, specs: SpecStore, ledger: Ledger, project_id: int,
    settings: Settings | None = None,
) -> dict[str, Any]:
    episodes = {e["number"]: e for e in story.list_episodes(project_id)}
    shots: dict[int, list[dict[str, Any]]] = {}
    for sh in story.list_shots(project_id):
        shots.setdefault(sh["episode_id"], []).append(sh)
    keyframes = _made(specs, ledger, settings, project_id, KEYFRAME)
    videos = _made(specs, ledger, settings, project_id, VIDEO)
    films = {a.slot: a for a in ledger.list_assets(
        project_id=project_id, kind="compose.assemble", current_only=True, limit=1000)}
    out_seasons: list[dict[str, Any]] = []
    totals = {"episodes": 0, "planned": 0, "scripted": 0, "filmed": 0, "shots": 0, "keyframes": 0, "videos": 0}
    next_number = 1
    for season in story.list_seasons(project_id):
        mine = sorted((e for e in episodes.values() if e["season"] == season["number"]), key=lambda e: e["number"])
        rows: list[dict[str, Any]] = []
        for e in mine:
            ids = [str(s["id"]) for s in shots.get(e["id"], [])]
            film = films.get(f"episode_{e['number']:02d}")
            states = {s["stage"]: s["status"] for s in state.stages(project_id, e["number"])}
            rows.append({
                "number": e["number"], "number_in_season": e["number_in_season"], "title": e["title"],
                "summary": e["summary"], "hook": e["hook"], "cliffhanger": e["cliffhanger"], "exists": True,
                "stages": states, "shots": len(ids), "keyframes": sum(1 for i in ids if i in keyframes),
                "videos": sum(1 for i in ids if i in videos),
                "outdated": sum(1 for i in ids if keyframes.get(i) is False or videos.get(i) is False),
                "film_asset_id": film.id if film else None, "film_seconds": film.duration_sec if film else None,
            })
        planned = max(season["planned_episodes"], len(mine))
        top = max((e["number"] for e in mine), default=next_number - 1)
        for i in range(len(mine), planned):                       # planned but not started: shown, not counted as made
            number = top + 1 + (i - len(mine))
            rows.append({"number": number, "number_in_season": i + 1, "title": "", "summary": "", "hook": "",
                         "cliffhanger": "", "exists": False, "stages": {}, "shots": 0, "keyframes": 0, "videos": 0,
                         "outdated": 0, "film_asset_id": None, "film_seconds": None})
        next_number = max([r["number"] for r in rows] + [next_number - 1]) + 1
        out_seasons.append({"number": season["number"], "title": season["title"], "summary": season["summary"],
                            "planned_episodes": season["planned_episodes"], "episodes": rows,
                            "filmed": sum(1 for r in rows if r["film_asset_id"])})
        totals["episodes"] += len(rows)
        totals["planned"] += planned
        totals["scripted"] += sum(1 for r in rows if r["stages"].get("script") == "completed")
        totals["filmed"] += out_seasons[-1]["filmed"]
        totals["shots"] += sum(r["shots"] for r in rows)
        totals["keyframes"] += sum(r["keyframes"] for r in rows)
        totals["videos"] += sum(r["videos"] for r in rows)
    return {"seasons": out_seasons, "totals": totals}
