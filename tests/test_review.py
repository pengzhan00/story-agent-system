"""The review loop through the API: note -> redo (spec edit + candidates) -> compare -> adopt resolves the note."""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from storyagent.config import Settings

from . import test_api as base_tests
from .test_api import make_shots, project, spec_body

client = base_tests.client             # the API fixtures are shared with test_api
run_worker = base_tests.run_worker

SUBJ = "shot.video/s1"


def setup_take(client: TestClient, fsettings: Settings, run_worker) -> tuple[int, int, str, dict[str, Any]]:  # noqa: ANN001
    pid = project(client)["id"]
    [shot] = make_shots(fsettings, pid, 1)
    base = f"/api/projects/{pid}/specs"
    client.post(base, json=spec_body(subject_type="shot.video", subject_id="s1", prompt="the hero walks"))
    client.post(f"{base}/{SUBJ}/regenerate", json={"tier": "draft", "shot_id": shot})
    run_worker()
    [asset] = client.get("/api/assets", params={"project_id": pid}).json()
    return pid, shot, asset["id"], asset


def test_a_note_drives_a_redo_and_adopting_the_new_take_resolves_it(client: TestClient, fsettings: Settings,
                                                                    run_worker) -> None:  # noqa: ANN001
    pid, shot, asset_id, _ = setup_take(client, fsettings, run_worker)
    notes = f"/api/projects/{pid}/notes"
    n = client.post(notes, json={"subject_type": "shot.video", "subject_id": "s1", "asset_id": asset_id,
                                 "body": "She turns away; keep her facing the camera."})
    assert n.status_code == 201, n.text
    note = n.json()
    assert note["status"] == "open" and note["shot_id"] == shot and note["author"] == "local"

    redo = client.post(f"{notes}/{note['id']}/redo", json={
        "prompt": "the hero walks toward the camera, facing it", "n_candidates": 2, "tier": "draft"})
    assert redo.status_code == 201, redo.text
    out = redo.json()
    assert out["note"]["status"] == "redoing" and out["note"]["redo_spec_version"] == 2 and len(out["jobs"]) == 2
    assert all(j["spec_version"] == 2 and j["shot_id"] == shot for j in out["jobs"])
    versions = client.get(f"/api/projects/{pid}/specs/{SUBJ}/versions").json()
    latest = max(versions, key=lambda v: v["version"])
    assert latest["prompt"].startswith("the hero walks toward") and f"review #{note['id']}" in latest["note"]

    run_worker()
    cmp = client.get(f"{notes}/{note['id']}/compare").json()
    assert cmp["before"]["id"] == asset_id and len(cmp["after"]) == 2
    assert all(a["spec_version"] == 2 for a in cmp["after"]) and {j["status"] for j in cmp["jobs"]} == {"completed"}

    board = client.get(f"/api/projects/{pid}/review/board").json()
    assert [(b["shot_id"], b["redoing_notes"], b["open_notes"]) for b in board] == [(shot, 1, 0)]
    # adopting a take the redo made resolves the note; adopting anything else would not
    new_id = cmp["after"][0]["id"]
    client.post(f"/api/assets/{new_id}/adopt")
    done = client.get(notes, params={"status": "resolved"}).json()
    assert [x["id"] for x in done] == [note["id"]] and done[0]["resolution"] == f"adopted:{new_id}"
    assert done[0]["resolved_by"] == "local"


def test_a_redo_with_no_changes_is_the_same_spec_with_new_seeds(client: TestClient, fsettings: Settings,
                                                                run_worker) -> None:  # noqa: ANN001
    pid, _, asset_id, _ = setup_take(client, fsettings, run_worker)
    notes = f"/api/projects/{pid}/notes"
    note = client.post(notes, json={"subject_type": "shot.video", "subject_id": "s1", "asset_id": asset_id,
                                    "body": "Flicker in the second half."}).json()
    out = client.post(f"{notes}/{note['id']}/redo", json={"n_candidates": 1, "tier": "draft"}).json()
    assert out["note"]["redo_spec_version"] == 1
    assert len(client.get(f"/api/projects/{pid}/specs/{SUBJ}/versions").json()) == 1


def test_notes_are_validated_filtered_and_closed_by_hand(client: TestClient, fsettings: Settings,
                                                         run_worker) -> None:  # noqa: ANN001
    pid, shot, asset_id, _ = setup_take(client, fsettings, run_worker)
    notes = f"/api/projects/{pid}/notes"
    subject = {"subject_type": "shot.video", "subject_id": "s1"}
    assert client.post(notes, json={**subject, "body": "   "}).status_code == 422
    assert client.post(notes, json={**subject, "body": "x", "subject_id": "nope"}).status_code == 404
    assert client.post(notes, json={**subject, "body": "x", "asset_id": "no-such-asset"}).status_code == 404
    a = client.post(notes, json={**subject, "body": "too dark"}).json()
    b = client.post(notes, json={**subject, "body": "wrong prop", "shot_id": shot}).json()
    assert [n["id"] for n in client.get(notes).json()] == [b["id"], a["id"]]          # newest first
    assert [n["id"] for n in client.get(notes, params={"shot_id": shot}).json()] == [b["id"]]
    closed = client.patch(f"{notes}/{a['id']}", json={"status": "dismissed", "resolution": "intended"}).json()
    assert closed["status"] == "dismissed" and closed["resolution"] == "intended" and closed["resolved_at"]
    assert client.post(f"{notes}/{a['id']}/redo", json={}).status_code == 409          # reopen it first
    reopened = client.patch(f"{notes}/{a['id']}", json={"status": "open"}).json()
    assert reopened["status"] == "open" and reopened["resolved_by"] == "" and reopened["resolved_at"] is None
    assert client.patch(f"/api/projects/{pid}/notes/999", json={"status": "open"}).status_code == 404
    other = project(client, "Other")["id"]
    assert client.patch(f"/api/projects/{other}/notes/{a['id']}", json={"status": "open"}).status_code == 404


def test_a_locked_spec_cannot_be_redone_with_changes(client: TestClient, fsettings: Settings, run_worker) -> None:  # noqa: ANN001
    pid, _, asset_id, _ = setup_take(client, fsettings, run_worker)
    notes = f"/api/projects/{pid}/notes"
    note = client.post(notes, json={"subject_type": "shot.video", "subject_id": "s1", "body": "change it"}).json()
    client.put(f"/api/projects/{pid}/specs/{SUBJ}/lock", json={})
    r = client.post(f"{notes}/{note['id']}/redo", json={"prompt": "new words"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "spec_locked"
    assert client.get(notes).json()[0]["status"] == "open"                          # nothing half-done
