"""The story + workflow API: edit the story, queue a run, watch its progress, cancel it."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.agents import AgentRunner
from storyagent.api.app import create_app
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.llm import LLMRouter
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker
from storyagent.workflow import WorkflowEngine

from .story_kit import NAMES, StoryClient


@pytest.fixture
def client(fsettings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as c:
        yield c


def run_to_end(fsettings: Settings) -> None:
    db = Database(fsettings.db_path)
    router = LLMRouter(fsettings, db, clients={"ollama": StoryClient()})
    engine = WorkflowEngine(fsettings, db, router, agents=AgentRunner(fsettings, router))
    worker = Worker(fsettings, db, AdapterRegistry(), probes=[], workflow=engine)
    try:
        assert worker.drain(30)
    finally:
        worker.shutdown()


def new_project(client: TestClient) -> int:
    r = client.post("/api/projects", json={"name": "Rain", "description": "A detective hunts a secret in the rain."})
    assert r.status_code == 201
    return int(r.json()["id"])


def started(client: TestClient, pid: int, **body: Any) -> dict[str, Any]:
    r = client.post(f"/api/projects/{pid}/workflow/runs", json={"params": {"visual_prompts": False}, **body})
    assert r.status_code == 201, r.text
    return r.json()


def test_queue_a_run_and_read_its_progress(client: TestClient, fsettings: Settings) -> None:
    pid = new_project(client)
    wf = client.get(f"/api/projects/{pid}/workflow").json()
    assert [s["status"] for s in wf["stages"]] == ["pending"] * 10 and wf["active_run"] is None
    run = started(client, pid)
    # a plain run is the story, not the media
    assert run["status"] == "queued" and run["stages"][0] == "analyze" and len(run["stages"]) == 7
    assert run["progress"]["percent"] == 0
    assert client.get(f"/api/projects/{pid}/workflow").json()["active_run"]["id"] == run["id"]
    assert client.post(f"/api/projects/{pid}/workflow/runs", json={}).status_code == 409     # one active run
    run_to_end(fsettings)
    done = client.get(f"/api/workflow/runs/{run['id']}").json()
    assert done["status"] == "completed" and done["progress"]["percent"] == 100
    assert [s["status"] for s in done["progress"]["stages"]] == ["completed"] * 7
    assert done["progress"]["stage_count"] == 7 and done["progress"]["elapsed_sec"] >= 0
    wf = client.get(f"/api/projects/{pid}/workflow").json()
    # cast, analyze done, the optional season outline pending, the rest of the story done, media and film pending
    done_then_pending = ["completed"] * 2 + ["pending"] + ["completed"] * 5 + ["pending"] * 2
    assert [s["status"] for s in wf["stages"]] == done_then_pending and wf["active_run"] is None
    nothing = client.post(f"/api/projects/{pid}/workflow/runs", json={})
    assert nothing.status_code == 409 and nothing.json()["error"]["code"] == "nothing_to_do"
    assert [r["id"] for r in client.get(f"/api/projects/{pid}/workflow/runs").json()] == [run["id"]]


def test_cancel_a_queued_run(client: TestClient) -> None:
    pid = new_project(client)
    run = started(client, pid, stages=["analyze"])
    cancelled = client.post(f"/api/workflow/runs/{run['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert client.post("/api/workflow/runs/nope/cancel").status_code == 404
    assert client.post("/api/projects/999/workflow/runs", json={}).status_code == 404


def test_edit_the_story(client: TestClient, fsettings: Settings) -> None:
    pid = new_project(client)
    started(client, pid)
    run_to_end(fsettings)
    base = f"/api/projects/{pid}"
    chars = client.get(f"{base}/characters").json()
    assert {c["name"] for c in chars} == set(NAMES)
    cid = chars[0]["id"]
    changed = client.patch(f"{base}/characters/{cid}", json={"appearance": "silver hair"}).json()
    assert changed["appearance"] == "silver hair"
    assert client.patch(f"{base}/characters/{cid}", json={"nonsense": 1}).status_code == 422
    made = client.post(f"{base}/characters", json={"name": "Extra"})
    assert made.status_code == 201 and client.delete(f"{base}/characters/{made.json()['id']}").status_code == 204
    other = new_project(client)
    assert client.get(f"/api/projects/{other}/characters/{cid}").status_code == 404      # not another project's

    assert len(client.get(f"{base}/locations").json()) == 2
    assert len(client.get(f"{base}/music").json()) >= 2 and len(client.get(f"{base}/sfx").json()) == 1
    script = client.get(f"{base}/scripts/1").json()
    assert script["title"] == "Rain Night" and client.get(f"{base}/scripts/9").status_code == 404
    scene = client.patch(f"{base}/scripts/1/acts/1/scenes/1", json={"description": "rewritten"})
    assert scene.status_code == 200 and scene.json()["acts"][0]["scenes"][0]["description"] == "rewritten"

    shots = client.get(f"{base}/shots").json()
    assert len(shots) == 8
    sid = shots[0]["id"]
    assert client.put(f"{base}/shots/{sid}/lock", json={"locked": True}).json()["locked"] is True
    locked = client.patch(f"{base}/shots/{sid}", json={"narration": "x"})
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "shot_locked"
    assert client.put(f"{base}/shots/{sid}/lock", json={"locked": False}).status_code == 200
    assert client.patch(f"{base}/shots/{sid}", json={"narration": "x"}).json()["narration"] == "x"
    assert client.get(f"/api/projects/{other}/shots/{sid}").status_code == 404

    lines = client.get(f"{base}/dialogue").json()
    assert lines and all(line["character_id"] for line in lines)
    fixed = client.put(f"{base}/shots/{lines[0]['shot_id']}/dialogue/{lines[0]['index']}/speaker",
                       json={"character": NAMES[1]})
    assert fixed.status_code == 200
    assert client.put(f"{base}/shots/{sid}/dialogue/99/speaker", json={"character": "x"}).status_code == 404


def test_the_event_stream_carries_workflow_progress(client: TestClient, fsettings: Settings) -> None:
    pid = new_project(client)
    run = started(client, pid, stages=["analyze", "cast"])
    run_to_end(fsettings)
    with client.stream("GET", f"/api/events?project_id={pid}&timeout=1") as r:
        text = "".join(r.iter_text())
    frames = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    assert any(f.get("id") == run["id"] and f["status"] == "completed" and f["progress"]["percent"] == 100
               for f in frames)
    assert "event: workflow" in text


def test_the_built_web_client_is_served_with_spa_fallback(fsettings: Settings, tmp_path: Any) -> None:
    from dataclasses import replace

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    s = replace(fsettings, api={**fsettings.api, "web_dir": str(dist)})
    with TestClient(create_app(s), raise_server_exceptions=False) as c:
        assert c.get("/projects/1/workflow").text == "<div id=root></div>"        # client-side route
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/ready").json()["ok"] is True                           # the API still wins
        assert c.get("/api/nope").status_code == 404
        assert c.get("/../../etc/passwd").text == "<div id=root></div>"           # never leaves the web root


def test_a_package_of_an_unassembled_episode_is_a_clear_conflict(client: TestClient) -> None:
    pid = new_project(client)
    r = client.get(f"/api/projects/{pid}/episodes/1/package")
    assert r.status_code == 409 and r.json()["error"]["code"] == "episode_not_ready"
    assert client.get("/api/projects/999/episodes/1/package").status_code == 404


def test_a_project_goes_to_the_recycle_bin_and_comes_back(client: TestClient) -> None:
    pid = new_project(client)
    assert client.delete(f"/api/projects/{pid}").json()["status"] == "trashed"
    assert [p["id"] for p in client.get("/api/projects").json()] == []
    assert [p["id"] for p in client.get("/api/projects?trashed=true").json()] == [pid]
    r = client.post(f"/api/projects/{pid}/workflow/runs", json={})
    assert r.status_code == 409 and "recycle bin" in json.dumps(r.json())
    assert client.post(f"/api/projects/{pid}/restore").json()["status"] == "draft"
    assert client.post(f"/api/projects/{pid}/restore").status_code == 409        # not in the bin any more
    assert [p["id"] for p in client.get("/api/projects").json()] == [pid]


def test_a_project_with_a_run_in_progress_cannot_be_trashed(client: TestClient) -> None:
    pid = new_project(client)
    started(client, pid)
    assert client.delete(f"/api/projects/{pid}").status_code == 409


def test_script_review_over_the_api(client: TestClient, fsettings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from storyagent.api import routes_workflow

    pid = new_project(client)
    assert client.post(f"/api/projects/{pid}/review").status_code == 409          # no script yet
    started(client, pid)
    run_to_end(fsettings)

    def fake_review(self: WorkflowEngine, project_id: int, episode: int = 1) -> dict[str, Any]:
        return {"score": 6, "summary": "ok", "issues": [{"severity": "high", "where": "1.1.1", "problem": "slow"}]}

    monkeypatch.setattr(routes_workflow.WorkflowEngine, "review_script", fake_review)
    body = client.post(f"/api/projects/{pid}/review").json()
    assert body["score"] == 6 and body["issues"][0]["fix"] == "" and body["issues"][0]["severity"] == "high"


def test_a_character_gets_its_voice_from_an_uploaded_sample(client: TestClient, fsettings: Settings) -> None:
    from .adapter_kit import wav_bytes

    pid = new_project(client)
    started(client, pid)
    run_to_end(fsettings)
    char = client.get(f"/api/projects/{pid}/characters").json()[0]
    up = client.post("/api/library/upload", params={"filename": "me.wav", "role": "voice", "name": char["name"],
                                                    "text": "It is raining again.", "consent": "true",
                                                    "replace": "true"},
                     content=wav_bytes(4.0), headers={"Content-Type": "application/octet-stream"})
    assert up.status_code == 201, up.text
    r = client.patch(f"/api/projects/{pid}/characters/{char['id']}",
                     json={"voice_ref_audio": f"library:voice/{char['name']}", "voice_ref_text": "It is raining again.",
                           "voice_ref_consent": True})
    assert r.status_code == 200 and r.json()["voice_ref_audio"].startswith("library:voice/")
    assert r.json()["voice_ref_consent"] is True


def test_every_model_call_can_be_read_back_in_full_and_prompts_are_editable(
    client: TestClient, fsettings: Settings
) -> None:
    pid = new_project(client)
    started(client, pid)
    run_to_end(fsettings)
    calls = client.get(f"/api/projects/{pid}/ai-calls").json()
    assert calls and {c["label"] for c in calls} >= {"director.analyze", "screenwriter.outline"}
    full = client.get(f"/api/ai-calls/{calls[-1]['id']}").json()
    assert full["system"] and full["prompt"] and full["response"].startswith("{") and full["label"]
    assert client.get(f"/api/projects/{pid}/ai-calls", params={"label": "director.analyze"}).json()

    agents = {a["name"]: a for a in client.get("/api/agents").json()}
    assert "reviewer.script" in agents and not agents["reviewer.script"]["overridden"]
    new_prompt = agents["reviewer.script"]["prompt"] + "\nBe brief."
    r = client.put("/api/agents/reviewer.script", json={"prompt": new_prompt, "temperature": 0.1})
    assert r.status_code == 200 and r.json()["overridden"] and r.json()["temperature"] == 0.1
    assert r.json()["default_prompt"] == agents["reviewer.script"]["prompt"]          # the packaged text is kept
    bad = client.put("/api/agents/reviewer.script", json={"prompt": "uses $undeclared_placeholder"})
    assert bad.status_code == 422
    assert client.get("/api/agents").json()                                              # still loads after a bad edit
    assert client.put("/api/agents/nope", json={"prompt": "x"}).status_code == 404
    reset = client.delete("/api/agents/reviewer.script").json()
    assert not reset["overridden"] and reset["prompt"] == agents["reviewer.script"]["prompt"]


def test_plan_and_style_are_readable_and_editable(client: TestClient, fsettings: Settings) -> None:
    pid = new_project(client)
    started(client, pid)
    run_to_end(fsettings)
    plan = client.get(f"/api/projects/{pid}/data/plan").json()
    assert plan["project_name"] == "Rain Night"
    style = client.get(f"/api/projects/{pid}/data/style").json()
    style["visual_style"] = "hand-painted watercolour"
    assert client.put(f"/api/projects/{pid}/data/style", json=style).json()["visual_style"].startswith("hand")
    assert client.get(f"/api/projects/{pid}/data/style").json()["visual_style"] == "hand-painted watercolour"
    assert client.get(f"/api/projects/{pid}/data/other").status_code == 404


def test_the_flow_describes_every_step_with_its_models(client: TestClient, fsettings: Settings) -> None:
    generic = client.get("/api/flow").json()["steps"]
    assert [s["stage"] for s in generic][:2] == ["analyze", "cast"] and generic[-1]["stage"] == "compose"
    analyze = generic[0]
    assert {a["name"] for a in analyze["agents"]} == {"director.analyze", "art.style"}
    assert all(a["backend"] and a["model"] for a in analyze["agents"]) and analyze["status"] is None
    produce = next(s for s in generic if s["stage"] == "produce")
    video = next(m for m in produce["media"] if m["subject"] == "shot.video")
    assert video["kind"] == "video.generate" and video["models"][0]["params"]["draft"] is not None
    pid = new_project(client)
    started(client, pid)
    run_to_end(fsettings)
    live = {s["stage"]: s for s in client.get("/api/flow", params={"project_id": pid}).json()["steps"]}
    assert live["analyze"]["status"] == "completed" and live["specs"]["status"] == "completed"
    dialogue = next(m for m in live["produce"]["media"] if m["subject"] == "dialogue.line")
    assert dialogue["counts"]["specs"] >= 1 and dialogue["counts"]["made"] == 0
    assert client.get("/api/flow", params={"project_id": 999}).status_code == 404


def test_the_project_overview_points_at_the_finished_episode(client: TestClient, fsettings: Settings) -> None:
    pid = new_project(client)
    card = client.get("/api/projects/overview").json()[0]
    assert card["id"] == pid and card["stages_done"] == 0 and card["stages_total"] == 9
    assert card["episode_asset_id"] is None and card["running"] is False
    started(client, pid)
    assert client.get("/api/projects/overview").json()[0]["running"] is True
    run_to_end(fsettings)
    card = client.get("/api/projects/overview").json()[0]
    assert card["stages_done"] == 7 and card["running"] is False
    db = Database(fsettings.db_path)
    db.conn.execute("INSERT INTO media_assets(id, project_id, kind, slot, path, sha256, bytes, duration_sec, version,"
                    " is_current) VALUES ('film1', ?, 'compose.assemble', 'episode_01', 'x.mp4', 'h', 5, 12.5, 1, 1)",
                    (pid,))
    card = client.get("/api/projects/overview").json()[0]
    assert card["episode_asset_id"] == "film1" and card["episode_seconds"] == 12.5 and card["episode_number"] == 1
    client.delete(f"/api/projects/{pid}")
    assert client.get("/api/projects/overview").json() == []
    assert client.get("/api/projects/overview", params={"trashed": True}).json()[0]["id"] == pid


def test_the_flow_names_the_default_model_for_specs_that_have_none(client: TestClient, fsettings: Settings) -> None:
    pid = new_project(client)
    started(client, pid)
    run_to_end(fsettings)
    steps = {s["stage"]: s for s in client.get("/api/flow", params={"project_id": pid}).json()["steps"]}
    refs = {m["subject"]: [x["ref"] for x in m["models"]] for m in steps["produce"]["media"]}
    assert refs["shot.video"] and refs["shot.video"][0] and refs["dialogue.line"][0]
    assert refs["shot.sfx"] == ["stable-audio-3-medium"]  # sound effects come from Stable Audio via ComfyUI


def test_script_review_is_refused_while_a_heavy_job_runs(client: TestClient, fsettings: Settings) -> None:
    pid = new_project(client)
    started(client, pid)
    run_to_end(fsettings)
    db = Database(fsettings.db_path)
    db.conn.execute("INSERT INTO media_jobs(id, project_id, kind, provider, model_ref, heavy, status)"
                    " VALUES ('hj', ?, 'video.avatar', 'longcat', 'x', 1, 'running')", (pid,))
    r = client.post(f"/api/projects/{pid}/review")
    assert r.status_code == 409 and r.json()["error"]["code"] == "heavy_busy"


def test_the_production_plan_is_listed_for_confirmation(client: TestClient, fsettings: Settings) -> None:
    pid = new_project(client)
    started(client, pid)
    run_to_end(fsettings)
    plan = client.get(f"/api/projects/{pid}/produce/plan?tier=draft").json()
    assert plan["tier"] == "draft" and plan["totals"]["count"] > 0
    assert {t["subject_type"] for t in plan["tasks"]} >= {"character.portrait", "shot.keyframe", "shot.video"}
    assert all(t["spec_id"] and t["label"] and t["est_seconds"] > 0 for t in plan["tasks"])
    assert client.get(f"/api/projects/{pid}/produce/plan?tier=bogus").status_code == 422
    assert client.get("/api/projects/999/produce/plan").status_code == 404


def test_a_project_from_a_written_script_queues_the_story_stages_but_no_media(
    client: TestClient, fsettings: Settings
) -> None:
    text = "SCENE 1. PIER - NIGHT\nMara: You are late.\nSCENE 2. OFFICE - DAY\nJon: The case is closed."
    made = client.post("/api/projects/from-text", json={"name": "From script", "text": text})
    assert made.status_code == 201
    body = made.json()
    assert body["project"]["description"].startswith("SCENE 1. PIER") and body["run"]["status"] == "queued"
    assert body["run"]["params"]["source_text"] == text and len(body["run"]["stages"]) == 7   # story only, no media
    assert client.post("/api/projects/from-text", json={"name": "x", "text": "  "}).status_code == 422
    assert client.post("/api/projects/from-text", json={"name": "x", "asset_id": "0" * 32}).status_code == 404


def test_a_project_can_choose_its_default_voice(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "Voices"}).json()["id"]
    ok = client.patch(f"/api/projects/{pid}/settings", json={"default_voice": "library:voice/narrator"})
    assert ok.status_code == 200 and ok.json()["settings"]["default_voice"] == "library:voice/narrator"
    assert client.patch(f"/api/projects/{pid}/settings", json={"default_voice": "/etc/passwd"}).status_code == 422
    cleared = client.patch(f"/api/projects/{pid}/settings", json={"default_voice": None})
    assert "default_voice" not in cleared.json()["settings"]


def test_the_series_board_lists_seasons_and_planned_episodes(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "Saga"}).json()["id"]
    client.patch(f"/api/projects/{pid}/seasons/1", json={"planned_episodes": 3})
    board = client.get(f"/api/projects/{pid}/series").json()
    eps = board["seasons"][0]["episodes"]
    assert [(e["number"], e["number_in_season"], e["exists"]) for e in eps] == [
        (1, 1, False), (2, 2, False), (3, 3, False)]
    assert board["totals"] == {"episodes": 3, "planned": 3, "scripted": 0, "filmed": 0, "shots": 0, "keyframes": 0,
                               "videos": 0}
    assert client.get("/api/projects/999/series").status_code == 404


def test_project_edit_settings_are_validated(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "Edit"}).json()["id"]
    ok = client.patch(f"/api/projects/{pid}/settings", json={"edit": {"title_card": True, "scene_fade_sec": 0.3}})
    assert ok.status_code == 200 and ok.json()["settings"]["edit"]["title_card"] is True
    for bad in ({"title_card": "yes"}, {"scene_fade_sec": 99}, {"bogus": 1}, {"recap_sec": -1}):
        assert client.patch(f"/api/projects/{pid}/settings", json={"edit": bad}).status_code == 422


def test_looks_and_props_api(client: TestClient) -> None:
    pid = new_project(client)
    hero = client.post(f"/api/projects/{pid}/characters", json={"name": "Hero"}).json()
    base = f"/api/projects/{pid}"
    look = client.post(f"{base}/looks", json={"character_id": hero["id"], "label": "night", "costume": "orange vest",
                                              "from_episode": 2})
    assert look.status_code == 201 and look.json()["from_act"] == 1
    assert client.post(f"{base}/looks", json={"character_id": hero["id"], "costume": " "}).status_code == 422
    assert client.post(f"{base}/looks", json={"character_id": 999, "costume": "x"}).status_code == 404
    assert client.patch(f"{base}/looks/{look.json()['id']}", json={"from_scene": 4}).json()["from_scene"] == 4
    assert [lk["costume"] for lk in client.get(f"{base}/looks").json()] == ["orange vest"]
    prop = client.post(f"{base}/props", json={"name": "Slip", "description": "yellowed paper"}).json()
    assert client.post(f"{base}/props", json={"name": "Slip"}).status_code == 422          # names are unique
    assert client.get(f"{base}/props").json()[0]["description"] == "yellowed paper"
    assert client.delete(f"{base}/props/{prop['id']}").status_code == 204
    assert client.delete(f"{base}/looks/{look.json()['id']}").status_code == 204
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    assert client.get(f"/api/projects/{other}/looks/{look.json()['id']}").status_code == 404


def test_the_queue_lists_the_story_runs_of_every_project(client: TestClient, fsettings: Settings) -> None:
    first = new_project(client)
    second = int(client.post("/api/projects", json={"name": "Snow", "description": "A quiet town."}).json()["id"])
    old = started(client, first)
    run_to_end(fsettings)
    waiting = started(client, second, stages=["analyze"])
    rows = client.get("/api/workflow/runs").json()
    assert [r["id"] for r in rows] == [waiting["id"], old["id"]]           # the waiting one first, then the newest
    assert [(r["project_name"], r["status"]) for r in rows] == [("Snow", "queued"), ("Rain", "completed")]
    assert [r["id"] for r in client.get("/api/workflow/runs", params={"active": True}).json()] == [waiting["id"]]
    assert len(client.get("/api/workflow/runs", params={"limit": 1}).json()) == 1
    assert client.post(f"/api/workflow/runs/{waiting['id']}/cancel").json()["status"] == "cancelled"
    binned = client.delete(f"/api/projects/{second}")                     # a project in the bin leaves the list
    assert binned.status_code == 200, binned.text
    assert [r["project_name"] for r in client.get("/api/workflow/runs").json()] == ["Rain"]
