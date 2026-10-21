"""Shot tiering: a few key shots get a video clip, the rest stay a keyframe with a slow move."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from storyagent.domain import QualityTier
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker
from storyagent.workflow.plan import produce_plan, totals, video_summary
from storyagent.workflow.tiering import VideoPolicy, policy_from, score, video_shots

from .adapter_kit import FAST
from .story_kit import make_engine, new_project
from .test_workflow_media import FAKE_MODELS, serving


def shots(n: int, per_act: int = 4, first_id: int = 1, **over: Any) -> list[dict[str, Any]]:
    return [
        {"id": first_id + i, "act_number": 1 + i // per_act, "scene_number": 1 + (i // 2) % 2, "shot_number": i % 2 + 1,
         "treatment": "auto", "render_payload": {"camera": {"movement": "static"}}, **over}
        for i in range(n)]


def test_the_hook_the_cliffhanger_and_act_openers_win() -> None:
    eps = {1: shots(10)}
    got = video_shots(eps, VideoPolicy(share=0.3, min_per_episode=0))
    assert got == {1, 10, 5}                                    # first shot, last shot, the opener of act 2
    assert score(eps[1][0], 0, eps[1]) > score(eps[1][9], 9, eps[1]) > score(eps[1][4], 4, eps[1])


def test_the_share_has_a_floor_and_a_ceiling() -> None:
    assert len(video_shots({1: shots(10)}, VideoPolicy(share=0.0, min_per_episode=2))) == 2
    assert len(video_shots({1: shots(10)}, VideoPolicy(share=1.0))) == 10
    assert video_shots({1: shots(10)}, VideoPolicy(share=0.0, min_per_episode=0)) == set()


def test_the_creator_overrules_the_policy_per_shot() -> None:
    eps = {1: shots(10)}
    eps[1][0]["treatment"] = "still"                            # the hook stays a still ...
    eps[1][6]["treatment"] = "video"                            # ... and an ordinary shot gets a clip
    got = video_shots(eps, VideoPolicy(share=0.2, min_per_episode=0))
    assert 1 not in got and 7 in got and 10 in got and len(got) == 2


def test_a_series_budget_keeps_the_best_shots_and_never_drops_a_forced_one() -> None:
    eps = {1: shots(10), 2: shots(10, first_id=11)}
    eps[2][5]["treatment"] = "video"
    free = video_shots(eps, VideoPolicy(share=0.4, min_per_episode=0))
    tight = video_shots(eps, VideoPolicy(share=0.4, min_per_episode=0, budget_sec=4 * 240, clip_sec=240))
    assert len(free) == 8 and len(tight) == 4 and 16 in tight and tight <= free        # 16 = the forced shot
    assert video_shots(eps, VideoPolicy(share=1.0, budget_sec=0, clip_sec=240)) == {16}


def test_policy_reads_config_and_the_project_can_override_it() -> None:
    wf = {"video_share": 0.25, "video_min_per_episode": 3, "video_budget_hours": 2}
    p = policy_from(wf, None, 600)
    assert (p.share, p.min_per_episode, p.budget_sec, p.clip_sec) == (0.25, 3, 7200, 600)
    q = policy_from(wf, {"video_share": 0.5, "video_budget_hours": 0}, 600)
    assert q.share == 0.5 and q.budget_sec is None


def story(tmp_path: Path):  # noqa: ANN201
    services = FAST + '\n[workflow]\npoll_seconds = 0.02\ndefault_voice = ""\n'          # the default tiering policy
    engine, _, settings = make_engine(tmp_path, toml=FAKE_MODELS, services=services)
    pid = new_project(engine)
    engine.run(pid, None, {"visual_prompts": False})
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    worker = Worker(settings, engine.db, reg, ledger=engine.spec_store.ledger, probes=[], workflow=engine)
    return engine, worker, pid


def test_the_plan_leaves_most_shots_as_stills_and_a_confirmed_clip_is_still_made(tmp_path: Path) -> None:
    engine, worker, pid = story(tmp_path)
    ledger = engine.spec_store.ledger
    plan = produce_plan(engine.settings, engine.store, engine.spec_store, ledger, pid, QualityTier.DRAFT)
    clips = [p for p in plan if p.subject_type == "shot.video"]
    chosen = [p for p in clips if p.state == "new"]
    assert len(clips) == 8 and len(chosen) == 2 and sum(p.state == "skip" for p in clips) == 6
    assert all(p.reason == "still" for p in clips if p.state == "skip")
    summary = video_summary(plan, None)
    assert (summary["shots"], summary["chosen"]) == (8, 2)
    assert summary["est_seconds"] == sum(p.est_seconds for p in chosen)
    assert totals(plan)["by_state"]["skip"] == 6
    assert totals(plan)["count"] == len([p for p in plan if p.state == "new"])
    extra = next(p for p in clips if p.state == "skip")
    keyframes = [p.spec_id for p in plan if p.subject_type == "shot.keyframe"]
    with serving(worker):
        out = engine.run_stage(pid, "produce", {"only": [*keyframes, *(p.spec_id for p in chosen), extra.spec_id]})
    assert out["failed"] == 0
    videos = ledger.list_assets(project_id=pid, kind="video.generate", current_only=True)
    assert len(videos) == 3                                     # the two the policy picked plus the one ticked by hand


def test_a_plain_produce_run_only_makes_the_chosen_clips(tmp_path: Path) -> None:
    engine, worker, pid = story(tmp_path)
    ledger = engine.spec_store.ledger
    with serving(worker):
        out = engine.run_stage(pid, "produce", {})
        assert out["failed"] == 0
        assert len(ledger.list_assets(project_id=pid, kind="video.generate", current_only=True)) == 2
        assert sum(1 for s in out["skipped_detail"] if s["reason"] == "still") == 6
        engine.store.update_shot(engine.store.list_shots(pid)[3]["id"], {"treatment": "video"})     # one more by hand
        engine.run_stage(pid, "produce", {})
    assert len(ledger.list_assets(project_id=pid, kind="video.generate", current_only=True)) == 3


def test_treatment_is_validated(tmp_path: Path) -> None:
    import sqlite3

    engine, _worker, pid = story(tmp_path)
    shot = engine.store.list_shots(pid)[0]
    with pytest.raises(sqlite3.IntegrityError):
        engine.store.update_shot(shot["id"], {"treatment": "bogus"})
