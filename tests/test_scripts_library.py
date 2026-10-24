"""The asset library's project scripts: every project's scripts in one list, readable as text, masked while locked."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings

ACTS = [{"number": 1, "scenes": [{
    "number": 1, "location": "Kitchen", "time_of_day": "night", "mood": "tense", "beat": "A quarrel starts.",
    "narration": "The kettle screams.",
    "shots": [{"shot_number": 1, "camera_angle": "close", "camera_movement": "push in", "duration_sec": 4,
               "action": "Mia grips the cup.", "narration": "",
               "dialogue": [{"character": "Mia", "line": "You never listen.", "emotion": "angry"}]},
              {"shot_number": 2, "action": "He looks away.", "dialogue": []}]}]}]


@pytest.fixture
def client(fsettings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(fsettings), raise_server_exceptions=False, headers={"Accept-Language": "en"}) as c:
        yield c


def project_with_script(client: TestClient, name: str, **extra: Any) -> int:
    pid = int(client.post("/api/projects", json={"name": name, **extra}).json()["id"])
    client.app.state.ctx.story.save_script(  # type: ignore[attr-defined]
        pid, 1, title=f"{name} pilot", synopsis="Two people, one kitchen.", acts=ACTS)
    return pid


def test_every_projects_scripts_are_listed_with_their_counts(client: TestClient) -> None:
    a = project_with_script(client, "Drama")
    b = project_with_script(client, "Advert", kind="advert")
    rows = client.get("/api/scripts").json()
    assert [(r["project_id"], r["episode_no"]) for r in rows] == [(a, 1), (b, 1)]
    first = rows[0]
    assert (first["scenes"], first["shots"], first["lines"]) == (1, 2, 1)
    assert first["title"] == "Drama pilot" and first["project_name"] == "Drama" and first["locked"] is False
    assert [r["project_id"] for r in client.get("/api/scripts", params={"project_id": b}).json()] == [b]


def test_a_script_reads_as_text_in_the_readers_language(client: TestClient) -> None:
    pid = project_with_script(client, "Drama")
    doc = client.get(f"/api/scripts/{pid}/1").json()
    text = doc["text"]
    assert text.startswith("# Drama\n") and "Drama pilot" in text and "> Two people, one kitchen." in text
    assert "## Act 1" in text and "### Scene 1 · Kitchen · night · tense" in text
    assert "**Shot 1 · close · push in · 4 s**" in text and "- **Mia** (angry): You never listen." in text
    assert "*Narration:* The kettle screams." in text
    zh = client.get(f"/api/scripts/{pid}/1", headers={"Accept-Language": "zh-CN"}).json()["text"]
    assert "## 第 1 幕" in zh and "镜头 1" in zh
    assert client.get(f"/api/scripts/{pid}/9").status_code == 404


def test_search_finds_words_in_the_text(client: TestClient) -> None:
    a = project_with_script(client, "Drama")
    project_with_script(client, "Advert")
    assert [r["project_id"] for r in client.get("/api/scripts", params={"q": "quarrel"}).json()] == [a, a + 1]
    assert client.get("/api/scripts", params={"q": "kettle screams"}).json() != []
    assert client.get("/api/scripts", params={"q": "submarine"}).json() == []


def test_a_locked_restricted_project_is_listed_but_masked_until_unlocked(client: TestClient) -> None:
    pid = project_with_script(client, "Private", rating="restricted")
    row = client.get("/api/scripts").json()[0]
    assert row["locked"] is True and row["title"] == "" and row["synopsis"] == "" and row["shots"] == 2
    assert client.get("/api/scripts", params={"q": "kettle"}).json() == []         # a locked text is not searched
    assert client.get(f"/api/scripts/{pid}/1").status_code == 403
    assert client.post("/api/content/unlock", json={"confirm_adult": True}).status_code == 200
    row = client.get("/api/scripts").json()[0]
    assert row["locked"] is False and row["title"] == "Private pilot"
    assert "kettle screams" in client.get(f"/api/scripts/{pid}/1").json()["text"]


# -- filing a script under a name -------------------------------------------------------------------------------------
def test_a_script_is_filed_as_a_named_snapshot_other_projects_can_call(client: TestClient) -> None:
    pid = project_with_script(client, "Drama")
    r = client.post(f"/api/scripts/{pid}/1/publish", json={"description": "pilot text", "tags": ["pilot"]})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["ref"] == "library:script/Drama-1" and item["asset"]["kind"] == "text.script"
    assert item["description"] == "pilot text" and item["source_project_id"] == pid
    body = client.get(f"/api/library/{item['id']}/file").text
    assert body.startswith("# Drama\n") and "- **Mia** (angry): You never listen." in body
    listed = client.get("/api/library", params={"role": "script"}).json()
    assert [i["ref"] for i in listed] == ["library:script/Drama-1"]
    ctx = client.app.state.ctx  # type: ignore[attr-defined]
    found = ctx.library.find("script", "Drama-1")
    assert found is not None and found.asset is not None and (ctx.settings.home / found.asset.path).is_file()


def test_filing_twice_needs_replace_and_a_name_may_be_chosen(client: TestClient) -> None:
    pid = project_with_script(client, "Drama")
    assert client.post(f"/api/scripts/{pid}/1/publish", json={"name": "pilot"}).status_code == 201
    again = client.post(f"/api/scripts/{pid}/1/publish", json={"name": "pilot"})
    assert again.status_code >= 400 and "already exists" in again.text
    ctx = client.app.state.ctx  # type: ignore[attr-defined]
    ctx.story.save_script(pid, 1, title="Second cut", synopsis="Changed.", acts=ACTS)
    assert client.post(f"/api/scripts/{pid}/1/publish", json={"name": "pilot", "replace": True}).status_code == 201
    [only] = client.get("/api/library", params={"role": "script"}).json()
    assert "Second cut" in client.get(f"/api/library/{only['id']}/file").text
    assert client.post(f"/api/scripts/{pid}/1/publish", json={"name": "a/b"}).status_code >= 400


def test_a_restricted_script_is_filed_into_the_vault_and_only_when_unlocked(client: TestClient) -> None:
    pid = project_with_script(client, "Private", rating="restricted")
    assert client.post(f"/api/scripts/{pid}/1/publish", json={}).status_code == 403
    assert client.post("/api/content/unlock", json={"confirm_adult": True}).status_code == 200
    item = client.post(f"/api/scripts/{pid}/1/publish", json={}).json()
    assert item["asset"]["rating"] == "restricted"
    ctx = client.app.state.ctx  # type: ignore[attr-defined]
    found = ctx.library.find("script", "Private-1")
    assert found is not None and found.asset is not None and "vault" in found.asset.path


def test_a_filed_script_is_a_reference_a_job_input_can_name(client: TestClient) -> None:
    from storyagent.media.assetrefs import Resolver

    pid = project_with_script(client, "Drama")
    client.post(f"/api/scripts/{pid}/1/publish", json={"name": "pilot"})
    ctx = client.app.state.ctx  # type: ignore[attr-defined]
    inputs, _params, _res = Resolver(ctx.settings, ctx.ledger).resolve({"story": "library:script/pilot"}, {})
    text = open(inputs["story"], encoding="utf-8").read()
    assert text.startswith("# Drama\n") and "You never listen." in text
