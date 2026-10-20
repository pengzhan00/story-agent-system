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
    assert [s["status"] for s in wf["stages"]] == ["pending"] * 9 and wf["active_run"] is None
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
    done_then_pending = ["completed"] * 7 + ["pending"] * 2
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
