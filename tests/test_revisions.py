"""A rewrite of a written scene is a proposal first: listed next to what it replaces, then taken or left."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings
from storyagent.workflow import revisions

from .test_coherence import Reader, _run_script

SLIP = {"severity": "high", "where": "1.2.1", "problem": "she names the sender before 2.2 reveals him",
        "fix": "she only asks who sent it"}


class Mender(Reader):
    """A writer that changes the line when it is given notes (the stock one writes the same shots every time)."""

    def _shots(self, prompt: str) -> dict[str, Any]:
        out = super()._shots(prompt)
        if "she only asks who sent it" in prompt or "make her kinder" in prompt:
            out["shots"][0]["dialogue"][0]["line"] = "Who sent this?"
        return out


def _line(engine: Any, act: int = 1, scene: int = 2) -> str:
    script = engine.store.get_script(1, 1)
    shots = script["acts"][act - 1]["scenes"][scene - 1]["shots"]
    return str(shots[0]["dialogue"][0]["line"])


def test_the_logic_readers_rewrite_waits_to_be_confirmed_unless_the_run_goes_to_the_film(tmp_path: Path) -> None:
    client = Mender()
    client.script_findings = [[SLIP]]
    engine, out = _run_script(tmp_path, client)
    # the scene was written again, but only as a proposal: the script still has the old line
    check = out["story_check"]["script"]
    assert check["proposed"] == ["1.2"] and check["rewritten"] == [] and check["open_high"] == 1
    assert client.readings == ["outline", "script"]                 # nothing changed: it is not read a second time
    assert _line(engine) == "You are late." and out["revisions_pending"] == 1
    [entry] = revisions.listed(engine.state, 1, 1)
    assert (entry["scene"], entry["source"], entry["status"]) == ("1.2", "logic", "pending")
    assert "she only asks who sent it" in entry["notes"]
    assert entry["before"][0]["dialogue"][0]["line"] == "You are late."
    assert entry["after"][0]["dialogue"][0]["line"] == "Who sent this?"
    # confirmed: it goes into the script
    done = revisions.decide(engine.store, engine.state, 1, 1, accept={entry["id"]})
    assert done == {"applied": ["1.2"], "discarded": [], "reverted": [], "conflicts": []}
    assert _line(engine) == "Who sent this?"
    # and can be taken back
    back = revisions.decide(engine.store, engine.state, 1, 1, revert={entry["id"]})
    assert back["reverted"] == ["1.2"] and _line(engine) == "You are late."
    assert revisions.listed(engine.state, 1, 1)[0]["status"] == "reverted"


def test_a_run_to_the_film_applies_the_rewrite_and_keeps_what_it_replaced(tmp_path: Path) -> None:
    client = Mender()
    client.script_findings = [[SLIP]]
    engine, out = _run_script(tmp_path, client, full=True)
    assert out["story_check"]["script"]["rewritten"] == ["1.2"] and out["story_check"]["script"]["proposed"] == []
    assert _line(engine) == "Who sent this?" and out["revisions_pending"] == 0
    [entry] = revisions.listed(engine.state, 1, 1)
    assert entry["status"] == "applied" and entry["before"][0]["dialogue"][0]["line"] == "You are late."
    revisions.decide(engine.store, engine.state, 1, 1, revert={entry["id"]})
    assert _line(engine) == "You are late."


def test_a_revision_asked_for_by_a_person_is_proposed_and_changes_nothing_until_confirmed(tmp_path: Path) -> None:
    client = Mender()
    engine, _ = _run_script(tmp_path, client, story_check=False)
    engine.run(1, ["scenes", "music", "shotplan", "specs"], {"visual_prompts": False})
    before = {s["stage"]: s["status"] for s in engine.state.stages(1)}
    assert before["script"] == before["specs"] == "completed"
    out = engine.run_stage(1, "script", {"detail": False, "feedback": "make her kinder", "propose": True,
                                         "revise_scenes": ["1.2", "2.1"], "revision_source": "review"})
    assert out == {"episode": 1, "proposed": ["1.2", "2.1"], "kept": True}
    # the script is untouched, its stage stands as it stood, nothing built on it went stale
    assert _line(engine) == "You are late."
    assert {s["stage"]: s["status"] for s in engine.state.stages(1)} == before
    waiting = revisions.pending(engine.state, 1, 1)
    assert [(r["scene"], r["source"]) for r in waiting] == [("2.1", "review"), ("1.2", "review")]
    # a newer proposal for a scene replaces the one that waits; a rewrite that changes nothing is not kept
    engine.run_stage(1, "script", {"detail": False, "feedback": "make her kinder", "revise_scenes": ["1.2"],
                                   "propose": True})
    assert [(r["scene"], r["source"]) for r in revisions.pending(engine.state, 1, 1)] == [
        ("1.2", "own"), ("2.1", "review")]
    assert engine.run_stage(1, "script", {"detail": False, "feedback": "no change asked", "revise_scenes": ["1.1"],
                                          "propose": True})["proposed"] == []
    # one is taken, one is left; a scene somebody edited meanwhile is never overwritten
    one, two = revisions.pending(engine.state, 1, 1)
    script = engine.store.get_script(1, 1)
    edited = [dict(s) for s in script["acts"][1]["scenes"][0]["shots"]]
    edited[0]["action"] = "sits down"                                            # 2.1 edited by hand
    engine.store.update_script_scene(1, 1, 2, 1, {"shots": edited})
    done = revisions.decide(engine.store, engine.state, 1, 1, accept={one["id"], two["id"]})
    assert done == {"applied": ["1.2"], "discarded": [], "reverted": [], "conflicts": ["2.1"]}
    assert _line(engine) == "Who sent this?" and _line(engine, 2, 1) == "You are late."
    assert revisions.pending(engine.state, 1, 1) == []


@pytest.fixture
def client(fsettings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as c:
        yield c


def test_the_revisions_are_listed_as_text_and_decided_over_the_api(client: TestClient, fsettings: Settings) -> None:
    from storyagent.db import Database
    from storyagent.story import StoryStore
    from storyagent.workflow import WorkflowState

    pid = client.post("/api/projects", json={"name": "Rain", "description": "A detective in the rain."}).json()["id"]
    db = Database(fsettings.db_path)
    store, state = StoryStore(db), WorkflowState(db)
    old = [{"shot_number": 1, "camera_angle": "wide", "action": "walks in",
            "dialogue": [{"character": "Lin Wan", "line": "You are late.", "emotion": "cold"}]}]
    new = [{**old[0], "action": "turns the parcel over",
            "dialogue": [{"character": "Lin Wan", "line": "Who sent this?", "emotion": ""}]}]
    store.save_script(pid, 1, title="Rain", synopsis="s", acts=[{"number": 1, "title": "a", "scenes": [
        {"number": 1, "beat": "b", "shots": old}, {"number": 2, "beat": "c", "shots": old}]}])
    state.set_stage(pid, "script", "completed", summary={}, episode=1)
    state.set_stage(pid, "specs", "completed", summary={}, episode=1)
    revisions.propose(state, pid, 1, scene="1.1", source="review", notes="[1.1] too early", before=old, after=new)
    revisions.propose(state, pid, 1, scene="1.2", source="logic", notes="", before=old, after=new)
    db.conn.commit()
    listed = client.get(f"/api/projects/{pid}/revisions?episode=1").json()
    assert [(r["scene"], r["status"], r["source"]) for r in listed["items"]] == [
        ("1.2", "pending", "logic"), ("1.1", "pending", "review")]
    first = listed["items"][1]
    assert "walks in" in first["before_text"] and "Lin Wan (cold): You are late." in first["before_text"]
    assert "turns the parcel over" in first["after_text"] and "Who sent this?" in first["after_text"]
    assert "*" not in first["before_text"]                         # plain text: the page shows it as it is
    ids = {r["scene"]: r["id"] for r in listed["items"]}
    done = client.post(f"/api/projects/{pid}/revisions/decide",
                       json={"episode": 1, "accept": [ids["1.1"]], "discard": [ids["1.2"]]})
    assert done.status_code == 200, done.text
    assert done.json() == {"applied": ["1.1"], "discarded": ["1.2"], "reverted": [], "conflicts": []}
    script = client.get(f"/api/projects/{pid}/scripts/1")
    assert script.status_code == 200, script.text
    assert "Who sent this?" in script.text and "You are late." in script.text     # 1.1 changed, 1.2 did not
    # the script changed: its stage is stale, so the next resume does it and what is built on it again
    stages = {s["stage"]: s["status"] for s in client.get(f"/api/projects/{pid}/workflow").json()["stages"]}
    assert stages["script"] == "stale"
    plan = [s for _, s in state.plan(pid, None, {})]
    assert "script" in plan and "specs" in plan
    # while a run is active nothing is decided
    run = client.post(f"/api/projects/{pid}/workflow/runs", json={"params": {}})
    assert run.status_code == 201, run.text
    held = client.post(f"/api/projects/{pid}/revisions/decide", json={"episode": 1, "revert": [ids["1.1"]]})
    assert held.status_code == 409 and held.json()["error"]["code"] == "conflict"
    db.close()
