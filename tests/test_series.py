"""Series > season > episode: stage state per scope (migration 007) and the season records."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings
from storyagent.db import Database, migrate
from storyagent.db import migrator as migrate_mod
from storyagent.workflow import WorkflowState

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
    assert migrate(db) == (6, 8)
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
