"""The workshop AI assistant: standing requirements, wiki passages, and a proposal checked against the form."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings, load_settings

from .fakes.http_server import FakeServer

PROPOSAL: dict[str, Any] = {
    "inputs": [
        {"name": "prompt", "value": "calm lullaby, soft female vocal, felt piano, 60 bpm"},
        {"name": "lyrics", "value": "[verse]\nThe sea is asleep"},
        {"name": "reference", "value": "asset:abc"},          # an upload: the assistant must not fill it
        {"name": "nonexistent", "value": "x"},
    ],
    "params": [
        {"name": "steps", "value": "12"},
        {"name": "bpm", "value": "60.0"},
        {"name": "infer_method", "value": "sde"},
        {"name": "thinking", "value": "true"},
        {"name": "duration", "value": "9999"},              # above the maximum
        {"name": "audio_format", "value": "ogg"},           # not a choice
        {"name": "fake_param", "value": "1"},
    ],
    "notes": "ACE-Step likes tags and 8-12 steps [1].",
    "cited": [1, 9],
}


@pytest.fixture
def server() -> Iterator[FakeServer]:
    with FakeServer() as srv:
        srv.route("GET", "/v1/models", {"data": [{"id": "m1"}, {"id": "m2"}]})
        srv.route("POST", "/v1/chat/completions",
                  {"choices": [{"message": {"content": json.dumps(PROPOSAL)}, "finish_reason": "stop"}]})
        yield srv


@pytest.fixture
def client(tmp_path: Path, server: FakeServer) -> Iterator[TestClient]:
    vault = tmp_path / "vault"
    (vault / "Skills").mkdir(parents=True)
    (vault / "Skills" / "ACE-Step notes.md").write_text(
        "# ACE-Step notes\n\nacestep-1.5-turbo makes songs from tags plus lyrics. Steps 8 to 12 work.\n",
        "utf-8")
    (vault / "Skills" / "Adult acestep.md").write_text(
        "# Adult acestep\n\nrestricted recipe for acestep-1.5-turbo\n", "utf-8")
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "wiki.toml").write_text(
        f'[wiki]\ndocs = false\n[[wiki.sources]]\nname = "vault"\npath = "{vault}"\n'
        'include = ["Skills/*.md"]\nrestricted = ["Skills/Adult*"]\n')
    (cfg / "llm.toml").write_text(
        f'[llm.backends.fake]\nkind = "openai_compat"\nurl = "{server.url}"\n'
        '[llm.roles.prompter]\nbackend = "fake"\nmodel = "m1"\n'
        '[llm.ratings.restricted]\nbackend = "fake"\nmodel = "m2"\n')
    settings: Settings = load_settings({"STORY_AGENT_HOME": str(tmp_path / "home"), "STORY_AGENT_LOCALE": "en"})
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        c.post("/api/wiki/sync")
        yield c


def sent(server: FakeServer) -> str:
    msgs = [r for r in server.requests if r.path == "/v1/chat/completions"][-1].json["messages"]
    return "\n".join(m["content"] for m in msgs)


BODY: dict[str, Any] = {"module": "song", "kind": "audio.music", "model_ref": "acestep-1.5-turbo", "tier": "final",
                        "inputs": {"prompt": "a song"}, "params": {}, "request": "a calm lullaby about the sea",
                        "language": "en"}


def test_standing_requirements_are_kept_per_module_and_can_be_cleared(client: TestClient) -> None:
    assert client.get("/api/workshop/assist/song").json()["requirements"] == ""
    r = client.put("/api/workshop/assist/song", json={"requirements": " Always Mandarin, no electric guitar. "})
    assert r.status_code == 200 and r.json()["requirements"] == "Always Mandarin, no electric guitar."
    assert r.json()["updated_by"] == "local" or r.json()["updated_by"]
    assert client.get("/api/workshop/assist/image").json()["requirements"] == ""          # another module is untouched
    client.put("/api/workshop/assist/song", json={"requirements": ""})
    assert client.get("/api/workshop/assist/song").json()["requirements"] == ""
    assert client.get("/api/workshop/assist/nope").status_code == 422
    assert client.put("/api/workshop/assist/song", json={"requirements": "x" * 4001}).status_code == 422


def test_the_proposal_is_checked_against_the_form_and_the_model(client: TestClient, server: FakeServer) -> None:
    client.put("/api/workshop/assist/song", json={"requirements": "Always Mandarin, no electric guitar."})
    r = client.post("/api/workshop/assist", json=BODY)
    assert r.status_code == 200, r.text
    out = r.json()
    assert {i["name"] for i in out["inputs"]} == {"prompt", "lyrics"}                    # text fields only
    assert {p["name"]: p["value"] for p in out["params"]} == {
        "steps": 12, "bpm": 60, "infer_method": "sde", "thinking": True}                  # typed, in range
    dropped = {(d["name"], d["reason"]) for d in out["dropped"]}
    assert dropped == {("reference", "not_fillable"), ("nonexistent", "not_fillable"), ("duration", "bad_value"),
                       ("audio_format", "bad_value"), ("fake_param", "unknown_param")}
    prompt = next(i for i in out["inputs"] if i["name"] == "prompt")
    assert prompt["current"] == "a song"                                                  # the diff has its "before"
    assert out["model"] == "m1" and out["notes"].startswith("ACE-Step likes")


def test_requirements_request_wiki_and_parameter_limits_reach_the_model(client: TestClient, server: FakeServer) -> None:
    client.put("/api/workshop/assist/song", json={"requirements": "Always Mandarin, no electric guitar."})
    out = client.post("/api/workshop/assist", json=BODY).json()
    text = sent(server)
    assert "Always Mandarin, no electric guitar." in text and "a calm lullaby about the sea" in text
    assert "[1] ACE-Step notes" in text and "Steps 8 to 12 work" in text
    assert "- steps: int, 1..200" in text and "infer_method: str, one of ode|sde" in text
    assert "reference" not in text.split("Fields:")[1].split("Parameters:")[0]            # uploads are not offered
    assert [s["slug"] for s in out["sources"]] == ["vault/Skills/ACE-Step-notes"] and out["sources"][0]["cited"]
    assert all(s["n"] != 9 for s in out["sources"])                                      # a made-up citation is dropped


def test_without_the_wiki_nothing_is_retrieved(client: TestClient, server: FakeServer) -> None:
    out = client.post("/api/workshop/assist", json={**BODY, "use_wiki": False}).json()
    assert out["sources"] == [] and "no wiki excerpt matched" in sent(server)


def test_a_restricted_note_is_read_only_when_unlocked_and_then_by_its_own_model(
        client: TestClient, server: FakeServer) -> None:
    assert all(s["rating"] == "general" for s in client.post("/api/workshop/assist", json=BODY).json()["sources"])
    assert client.post("/api/content/unlock", json={"confirm_adult": True}).status_code == 200
    out = client.post("/api/workshop/assist", json=BODY).json()
    assert any(s["rating"] == "restricted" for s in out["sources"]) and out["model"] == "m2"


def test_a_model_of_another_kind_or_module_is_refused(client: TestClient) -> None:
    assert client.post("/api/workshop/assist", json={**BODY, "model_ref": "flux2-klein-base-4b"}).status_code == 422
    assert client.post("/api/workshop/assist", json={**BODY, "module": "image"}).status_code == 422
    assert client.post("/api/workshop/assist", json={**BODY, "module": "nope"}).status_code == 422
