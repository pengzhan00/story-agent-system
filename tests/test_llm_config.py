"""Which model each role uses (chosen on the screen, live) and the AI suggest / translate buttons beside prompts."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings, load_settings
from storyagent.db import Database
from storyagent.llm import LLMRouter

from .conftest import FAKE_MODELS, FAKE_SERVICES
from .fakes.http_server import FakeServer


def chat_reply(req: Any) -> dict[str, Any]:
    body = req.json
    user = body["messages"][-1]["content"]
    text = "TRANSLATED" if "Translate from" in user else "IMPROVED"
    return {"choices": [{"message": {"content": json.dumps({"text": text})}, "finish_reason": "stop"}]}


@pytest.fixture
def server() -> Iterator[FakeServer]:
    with FakeServer() as srv:
        srv.route("GET", "/v1/models", {"data": [{"id": "m1"}, {"id": "m2"}]})
        srv.route("POST", "/v1/chat/completions", chat_reply)
        yield srv


@pytest.fixture
def settings(tmp_path: Path, server: FakeServer) -> Settings:
    home = tmp_path / "home"
    cfg = home / "config"
    cfg.mkdir(parents=True)
    (cfg / "models.toml").write_text(FAKE_MODELS)
    (cfg / "services.toml").write_text(FAKE_SERVICES)
    (cfg / "llm.toml").write_text(
        f'[llm.backends.fake]\nkind = "openai_compat"\nurl = "{server.url}"\n'
        '[llm.roles.prompter]\nbackend = "fake"\nmodel = "m1"\n')
    return load_settings({"STORY_AGENT_HOME": str(home)})


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        yield c


def role(state: dict[str, Any], name: str) -> dict[str, Any]:
    return next(r for r in state["roles"] if r["name"] == name)


def test_the_screen_lists_backends_with_their_models_and_every_role(client: TestClient) -> None:
    state = client.get("/api/llm").json()
    fake = next(b for b in state["backends"] if b["name"] == "fake")
    assert fake["state"] == "ok" and fake["models"] == ["m1", "m2"]
    names = {r["name"] for r in state["roles"]}
    assert {"default", "director", "writer", "reviewer", "prompter", "translator", "composer", "sound"} <= names
    p = role(state, "prompter")
    assert (p["backend"], p["model"], p["source"], p["group"]) == ("fake", "m1", "config", "prompts")


def test_a_choice_is_global_or_for_one_project_and_can_be_reset(client: TestClient, settings: Settings) -> None:
    pid = client.post("/api/projects", json={"name": "Mine"}).json()["id"]
    state = client.put("/api/llm/roles/prompter", json={"backend": "fake", "model": "m2"}).json()
    assert (role(state, "prompter")["model"], role(state, "prompter")["source"]) == ("m2", "global")
    assert role(state, "prompter")["config_model"] == "m1"

    state = client.put("/api/llm/roles/prompter", json={"backend": "fake", "model": "m1", "project_id": pid}).json()
    assert state["project_id"] == pid and role(state, "prompter")["source"] == "project"
    assert role(client.get("/api/llm").json(), "prompter")["model"] == "m2"            # other projects: the global one

    router = LLMRouter(settings, Database(settings.db_path))
    assert router.resolve("prompter", project_id=pid)[:2] == ("fake", "m1")
    assert router.resolve("prompter")[:2] == ("fake", "m2")
    assert router.resolve("prompter", "pinned", project_id=pid)[1] == "pinned"         # a model named by the call wins

    state = client.delete(f"/api/llm/roles/prompter?project_id={pid}").json()
    assert role(state, "prompter")["model"] == "m2"
    state = client.delete("/api/llm/roles/prompter").json()
    assert (role(state, "prompter")["model"], role(state, "prompter")["source"]) == ("m1", "config")


def test_a_bad_choice_is_refused_with_a_reason(client: TestClient) -> None:
    r = client.put("/api/llm/roles/prompter", json={"backend": "fake", "model": "nope"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "llm_model_not_installed"
    assert client.put("/api/llm/roles/prompter", json={"backend": "x", "model": "m1"}).status_code == 422
    assert client.put("/api/llm/roles/nobody", json={"backend": "fake", "model": "m1"}).status_code == 404
    assert client.put("/api/llm/roles/prompter", json={"backend": "fake", "model": " "}).status_code == 422


def test_the_model_test_asks_one_short_question(client: TestClient, server: FakeServer) -> None:
    r = client.post("/api/llm/test", json={"backend": "fake", "model": "m2"})
    assert r.status_code == 200 and r.json()["ok"] is True
    sent = next(q.json for q in server.requests if q.path == "/v1/chat/completions")
    assert sent["model"] == "m2" and sent["max_tokens"] == 16


def test_ai_suggest_and_translate_use_the_prompter_model_of_the_project(client: TestClient, server: FakeServer) -> None:
    pid = client.post("/api/projects", json={"name": "Mine"}).json()["id"]
    client.put("/api/llm/roles/prompter", json={"backend": "fake", "model": "m2", "project_id": pid})

    r = client.post("/api/ai/prompt", json={"action": "suggest", "text": "a quiet street at night", "purpose": "image",
                                            "instruction": "more rain", "project_id": pid})
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "IMPROVED", "action": "suggest", "backend": "fake", "model": "m2"}
    sent = [q.json for q in server.requests if q.path == "/v1/chat/completions"][-1]
    user = sent["messages"][-1]["content"]
    assert sent["model"] == "m2" and "a quiet street at night" in user and "more rain" in user

    r = client.post("/api/ai/prompt", json={"action": "translate", "text": "雨夜的街道", "target": "en",
                                            "source": "zh-CN", "purpose": "image"})
    assert r.status_code == 200 and r.json()["text"] == "TRANSLATED" and r.json()["model"] == "m1"
    sent = [q.json for q in server.requests if q.path == "/v1/chat/completions"][-1]
    assert "雨夜的街道" in sent["messages"][-1]["content"] and "English" in sent["messages"][-1]["content"]
    assert "Simplified Chinese" in sent["messages"][-1]["content"]


def test_an_unreachable_backend_is_a_clear_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "models.toml").write_text(FAKE_MODELS)
    (home / "config" / "services.toml").write_text(FAKE_SERVICES)
    (home / "config" / "llm.toml").write_text(
        '[llm.backends.gone]\nkind = "openai_compat"\nurl = "http://127.0.0.1:9"\n'
        '[llm.roles.prompter]\nbackend = "gone"\nmodel = "m1"\n')
    with TestClient(create_app(load_settings({"STORY_AGENT_HOME": str(home)})), raise_server_exceptions=False) as c:
        gone = next(b for b in c.get("/api/llm").json()["backends"] if b["name"] == "gone")
        assert gone["state"] == "unreachable" and gone["models"] == []
        r = c.post("/api/ai/prompt", json={"action": "suggest", "text": "x"})
        assert r.status_code == 502 and r.json()["error"]["code"] == "llm_unreachable"
        # a backend that is down still accepts the choice (the name cannot be checked); the run will say
        assert c.put("/api/llm/roles/writer", json={"backend": "gone", "model": "m9"}).status_code == 200


def test_an_edited_text_asset_is_a_new_version_and_the_old_one_stays_a_candidate(client: TestClient,
                                                                                 settings: Settings) -> None:
    from storyagent.media.ledger import Ledger, NewAsset

    db = Database(settings.db_path)
    ledger = Ledger(db, settings)
    folder = settings.home / "texts"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "story.md").write_text("first draft", "utf-8")
    old = ledger.register_asset(NewAsset(kind="text.story", slot="main", path="texts/story.md"), project_id=None)

    r = client.put(f"/api/assets/{old.id}/text", json={"text": "second draft"})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["id"] != old.id and new["is_current"] and new["kind"] == "text.story" and new["version"] == 2
    assert client.get(f"/api/assets/{new['id']}/file").text == "second draft"
    assert client.get(f"/api/assets/{old.id}/file").text == "first draft"
    assert client.get(f"/api/assets/{old.id}").json()["is_current"] is False
    img = ledger.register_asset(NewAsset(kind="image.generate", slot="x", path="texts/story.md"), project_id=None)
    assert client.put(f"/api/assets/{img.id}/text", json={"text": "x"}).status_code == 422
