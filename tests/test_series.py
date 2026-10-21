"""Series > season > episode: stage state per scope (migration 007) and the season records."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings
from storyagent.db import Database, migrate
from storyagent.db import migrator as migrate_mod
from storyagent.workflow import STAGES, WorkflowState

from .story_kit import make_engine, new_project


def test_migration_007_moves_the_old_stage_rows_to_season_1_episode_1(
    settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = migrate_mod.discover()
    monkeypatch.setattr(migrate_mod, "discover", lambda: [m for m in real if m.version <= 6])
    db = Database(settings.db_path)
    migrate(db)
    db.conn.execute("INSERT INTO projects(slug,name) VALUES ('p','P')")
    db.conn.execute("INSERT INTO episodes(project_id, number) VALUES (1, 3)")
    for stage in ("analyze", "cast", "script", "shotplan"):
        db.conn.execute("INSERT INTO workflow_stages(project_id, stage, status) VALUES (1, ?, 'completed')", (stage,))
    monkeypatch.setattr(migrate_mod, "discover", lambda: real)
    assert migrate(db) == (6, real[-1].version)
    found = db.conn.execute("SELECT * FROM workflow_stages")
    rows = {r["stage"]: (r["season"], r["episode"], r["status"]) for r in found}
    assert rows == {"analyze": (0, 0, "completed"), "cast": (0, 0, "completed"),
                    "script": (1, 1, "completed"), "shotplan": (1, 1, "completed")}
    ep = db.conn.execute("SELECT season, number_in_season FROM episodes").fetchone()
    assert (ep["season"], ep["number_in_season"]) == (1, 3)
    assert db.conn.execute("SELECT number FROM seasons WHERE project_id=1").fetchall()[0]["number"] == 1
    db.close()


def test_series_stages_are_shared_and_episode_stages_are_kept_per_episode(db: Database) -> None:
    db.conn.execute("INSERT INTO projects(slug,name) VALUES ('p','P')")
    state = WorkflowState(db)
    for stage in ("analyze", "cast", "script", "scenes"):
        state.set_stage(1, stage, "completed", episode=1)
    state.set_stage(1, "script", "completed", episode=2)
    st1 = {s["stage"]: s["status"] for s in state.stages(1, 1)}
    st2 = {s["stage"]: s["status"] for s in state.stages(1, 2)}
    assert st1["analyze"] == st2["analyze"] == "completed"           # the series' own
    assert st1["scenes"] == "completed" and st2["scenes"] == "pending"
    assert st2["script"] == "completed"
    assert state.pending_stages(1, None, 2)[0] == "scenes"
    assert "script" not in state.pending_stages(1, None, 2) and "script" in state.pending_stages(1, None, 3)


def test_finishing_an_episode_stage_leaves_other_episodes_alone_but_a_series_stage_outdates_all(db: Database) -> None:
    db.conn.execute("INSERT INTO projects(slug,name) VALUES ('p','P')")
    state = WorkflowState(db)
    for ep in (1, 2):
        state.set_stage(1, "script", "completed", episode=ep)
        state.set_stage(1, "scenes", "completed", episode=ep)
    state.set_stage(1, "script", "completed", episode=1)             # re-run for episode 1 only
    assert {s["stage"]: s["status"] for s in state.stages(1, 1)}["scenes"] == "stale"
    assert {s["stage"]: s["status"] for s in state.stages(1, 2)}["scenes"] == "completed"
    state.set_stage(1, "cast", "completed")                          # the series changed: everything below is outdated
    assert {s["stage"]: s["status"] for s in state.stages(1, 2)}["scenes"] == "stale"
    assert {s["stage"]: s["status"] for s in state.stages(1, 2)}["script"] == "stale"


def test_a_second_episode_runs_without_touching_the_first(tmp_path: Path) -> None:
    engine, _client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, None, {"visual_prompts": False})
    before = {s["stage"]: s["status"] for s in engine.state.stages(pid, 1)}
    assert all(v == "completed" for k, v in before.items() if k in ("analyze", "cast", "script", "specs"))
    assert engine.stages_to_run(pid, None, 2)[0] == "script"         # analyze and cast are done for the series
    engine.run(pid, None, {"visual_prompts": False, "episode": 2})
    assert {e["number"] for e in engine.store.list_episodes(pid)} == {1, 2}
    assert {s["stage"]: s["status"] for s in engine.state.stages(pid, 1)} == before
    assert engine.store.get_script(pid, 2) is not None


def test_episodes_belong_to_seasons_and_seasons_are_editable(tmp_path: Path) -> None:
    engine, _client, _ = make_engine(tmp_path)
    store = engine.store
    pid = new_project(engine)
    assert [(s["number"], s["episodes"]) for s in store.list_seasons(pid)] == [(1, 0)]
    store.ensure_episode(pid, 1)
    store.ensure_episode(pid, 2)
    store.ensure_episode(pid, 3, season=2)
    store.ensure_episode(pid, 4, season=2)
    eps = {e["number"]: (e["season"], e["number_in_season"]) for e in store.list_episodes(pid)}
    assert eps == {1: (1, 1), 2: (1, 2), 3: (2, 1), 4: (2, 2)}
    assert [(s["number"], s["episodes"]) for s in store.list_seasons(pid)] == [(1, 2), (2, 2)]
    assert store.season_of(pid, 3) == 2 and store.season_of(pid, 99) == 1
    assert store.update_season(pid, 2, {"title": "Aftermath", "planned_episodes": 30})["planned_episodes"] == 30


def test_seasons_api(fsettings: Settings) -> None:
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as client:
        pid = client.post("/api/projects", json={"name": "Saga"}).json()["id"]
        assert [s["number"] for s in client.get(f"/api/projects/{pid}/seasons").json()] == [1]
        made = client.patch(f"/api/projects/{pid}/seasons/2", json={"title": "Second", "planned_episodes": 12})
        assert made.status_code == 200 and made.json()["planned_episodes"] == 12
        assert client.patch(f"/api/projects/{pid}/seasons/2", json={"bogus": 1}).status_code == 422
        assert [s["number"] for s in client.get(f"/api/projects/{pid}/seasons").json()] == [1, 2]
        assert client.get(f"/api/projects/{pid}/workflow?episode=2").json()["episode"] == 2


def test_each_episode_gets_its_own_scene_music(tmp_path: Path) -> None:
    engine, _client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, None, {"visual_prompts": False})
    engine.run(pid, None, {"visual_prompts": False, "episode": 2})
    refs = sorted(m["scene_ref"] for m in engine.store.list_music(pid) if m["kind"] == "bgm")
    assert refs and all(r.startswith(("ep01_", "ep02_")) for r in refs)
    assert any(r.startswith("ep01_") for r in refs) and any(r.startswith("ep02_") for r in refs)
    assert sum(1 for m in engine.store.list_music(pid) if m["kind"] == "theme") == 1    


def test_migration_008_moves_old_scene_music_to_episode_1(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    real = migrate_mod.discover()
    monkeypatch.setattr(migrate_mod, "discover", lambda: [m for m in real if m.version <= 7])
    old = Database(settings.db_path)
    migrate(old)
    old.conn.execute("INSERT INTO projects(slug,name) VALUES ('q','Q')")
    old.conn.execute("INSERT INTO music_themes(project_id, kind, scene_ref) VALUES (1,'bgm','act01_scene02')")
    old.conn.execute("INSERT INTO music_themes(project_id, kind, scene_ref) VALUES (1,'theme','')")
    monkeypatch.setattr(migrate_mod, "discover", lambda: real)
    migrate(old)
    got = [r["scene_ref"] for r in old.conn.execute("SELECT scene_ref FROM music_themes ORDER BY id")]
    assert got == ["ep01_act01_scene02", ""]
    old.close()


def test_episode_lists_are_parsed_and_checked() -> None:
    from storyagent.workflow.state import WorkflowStateError, parse_episodes

    assert parse_episodes("1-3") == [1, 2, 3] and parse_episodes("1,3,5-6") == [1, 3, 5, 6]
    assert parse_episodes([2, 1, 2]) == [1, 2] and parse_episodes(4) == [4]
    for bad in ("0-2", "a", "", "3-1", "1-9999"):
        with pytest.raises(WorkflowStateError):
            parse_episodes(bad)


def test_a_batch_runs_the_series_stages_once_and_each_episode_resumes_on_its_own(tmp_path: Path) -> None:
    engine, _client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, None, {"visual_prompts": False})                      # episode 1 is done
    plan, several = engine.plan(pid, None, {"episodes": "1-3", "resume": True})
    assert several and [(e, s) for e, s in plan if e == 1] == []         # nothing left for episode 1
    assert {e for e, _ in plan} == {2, 3} and not any(s in ("analyze", "cast") for _, s in plan)
    fresh = engine.state.plan(new_project(engine), None, {"episodes": "1-2"})
    assert fresh.count((0, "analyze")) == 1 and fresh.count((0, "cast")) == 1      # once, not once per episode
    assert fresh[2:4] == [(1, "script"), (1, "scenes")]


def test_a_queued_batch_reports_a_bar_per_episode_stage(tmp_path: Path) -> None:
    engine, _client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    params = {"visual_prompts": False, "episodes": "1-2", "resume": True}
    plan, _several = engine.plan(pid, None, params)
    stages = [x for x in STAGES if any(x == name for _, name in plan)]
    run = engine.state.enqueue(pid, stages, params)
    claimed = engine.state.claim_next("t", 60.0)
    assert claimed is not None and claimed["id"] == run["id"]
    engine.execute_run(claimed, "t", 60.0, 0.05)
    done = engine.state.get_run(run["id"])
    assert done["status"] == "completed", done["error_message"]
    bars = done["progress"]["stages"]
    assert [(b["stage"], b["episode"]) for b in bars][:3] == [("analyze", None), ("cast", None), ("script", 1)]
    assert {b["episode"] for b in bars} == {None, 1, 2} and all(b["status"] == "completed" for b in bars)
    assert done["progress"]["percent"] == 100.0
    assert {e["number"] for e in engine.store.list_episodes(pid)} == {1, 2}


def test_a_season_run_covers_the_planned_episodes_and_the_api_validates(fsettings: Settings) -> None:
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as client:
        pid = client.post("/api/projects", json={"name": "Saga"}).json()["id"]
        bad = client.post(f"/api/projects/{pid}/workflow/runs", json={"params": {"episodes": "0-2"}})
        assert bad.status_code == 422
        no_plan = client.post(f"/api/projects/{pid}/workflow/runs", json={"params": {"season": 2}})
        assert no_plan.status_code == 422                                   # nothing planned for season 2 yet
        client.patch(f"/api/projects/{pid}/seasons/2", json={"planned_episodes": 3})
        ok = client.post(f"/api/projects/{pid}/workflow/runs", json={"params": {"season": 2}})
        assert ok.status_code == 201 and ok.json()["params"]["resume"] is True


def test_the_season_outline_plans_every_episode_and_the_script_follows_it(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    assert "outline" not in engine.stages_to_run(pid, None)                  # a one-episode project skips it
    engine.run(pid, ["analyze", "cast"])
    with pytest.raises(Exception, match="planned_episodes"):
        engine.run(pid, ["outline"])                                        # nothing says how long the season is
    engine.store.update_season(pid, 1, {"planned_episodes": 4})
    assert engine.stages_to_run(pid, None)[:1] == ["outline"]               # a planned season is outlined first
    engine.run(pid, ["outline"])
    eps = {e["number"]: e for e in engine.store.list_episodes(pid)}
    assert sorted(eps) == [1, 2, 3, 4] and eps[3]["hook"] == "hook 3" and eps[3]["cliffhanger"] == "cliff 3"
    assert eps[2]["summary"] == "Things escalate 2." and eps[4]["number_in_season"] == 4
    season = engine.store.list_seasons(pid)[0]
    assert season["summary"].startswith("A secret") and season["title"] == "Season" and season["episodes"] == 4
    assert {s["stage"]: s["status"] for s in engine.state.stages(pid, 2)}["outline"] == "completed"
    engine.run(pid, ["script"], {"visual_prompts": False, "episode": 2})
    prompt = next(p for p in client.prompts if "Open on this hook: hook 2" in p)
    assert "End on this: cliff 2" in prompt and "next episode (Episode 3)" in prompt and "Season arc:" in prompt
    assert engine.store.get_script(pid, 2) is not None
    engine.run(pid, ["outline"], {"force": True})                            # a new outline outdates every script
    assert {s["stage"]: s["status"] for s in engine.state.stages(pid, 2)}["script"] == "stale"


def test_a_second_season_numbers_its_episodes_after_the_first(tmp_path: Path) -> None:
    engine, _client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, ["analyze", "cast"])
    engine.store.update_season(pid, 1, {"planned_episodes": 3})
    engine.store.update_season(pid, 2, {"planned_episodes": 2})
    engine.run(pid, ["outline"], {"season": 1})
    engine.run(pid, ["outline"], {"season": 2})
    got = {e["number"]: (e["season"], e["number_in_season"]) for e in engine.store.list_episodes(pid)}
    assert got == {1: (1, 1), 2: (1, 2), 3: (1, 3), 4: (2, 1), 5: (2, 2)}


def test_a_new_speaker_in_a_later_episode_joins_the_cast(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    client.speaker = "Old Wen"
    first = engine.run(pid, None, {"visual_prompts": False})["script"]
    assert first["unknown_speakers"] == ["Old Wen"] and first["new_characters"] == []     # episode 1: only the cast
    second = engine.run(pid, None, {"visual_prompts": False, "episode": 2})["script"]
    assert second["new_characters"] == ["Old Wen"] and second["unknown_speakers"] == []
    who = engine.store.find_character(pid, "Old Wen")
    assert who is not None and who["introduced_episode"] == 2 and who["appearance"].startswith("short black hair")
    assert engine.store.find_character(pid, "Lin Wan")["introduced_episode"] == 1
    assert any(s.subject_type == "character.portrait" and s.subject_id == str(who["id"])      # specs follow the cast
               for s in engine.spec_store.list_current(pid))


def test_narration_labels_and_the_opt_out_do_not_add_characters(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, ["analyze", "cast"])
    client.speaker = "Narrator"
    out = engine.run(pid, ["script"], {"visual_prompts": False, "episode": 2})["script"]
    assert out["new_characters"] == [] and out["unknown_speakers"] == ["Narrator"]
    client.speaker = "Old Wen"
    off = engine.run(pid, ["script"], {"visual_prompts": False, "episode": 3, "auto_cast": False})["script"]
    assert off["new_characters"] == [] and engine.store.find_character(pid, "Old Wen") is None


def test_a_locked_character_survives_a_forced_cast(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, ["analyze", "cast"])
    keep = engine.store.find_character(pid, "Lin Wan")
    engine.store.update_character(keep["id"], {"appearance": "scar over the left eye", "locked": True})
    before = client.calls.count("character")
    out = engine.run(pid, ["cast"], {"force": True})["cast"]
    assert engine.store.find_character(pid, "Lin Wan")["appearance"] == "scar over the left eye"
    assert out["skipped"] == 1 and client.calls.count("character") - before == 1       # only the unlocked one redone
    assert engine.store.find_character(pid, "Lin Wan")["locked"] is True
