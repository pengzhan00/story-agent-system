from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings, load_settings

from .test_comfy_import import sd_graph


@pytest.fixture
def client(fsettings: Settings) -> Iterator[TestClient]:
    again = lambda: load_settings({"STORY_AGENT_HOME": str(fsettings.home)})  # noqa: E731
    app = create_app(fsettings, loader=again)
    with TestClient(app, raise_server_exceptions=False, headers={"Accept-Language": "en"}) as c:
        yield c


def test_preview_lists_bindings_and_warnings_and_writes_nothing(client: TestClient, fsettings: Settings) -> None:
    r = client.post("/api/tools/import/comfy", json={"workflow": sd_graph(), "kind": "image.generate"})
    assert r.status_code == 200
    body = r.json()
    rows = {b["name"]: b for b in body["bindings"]}
    assert rows["prompt"]["targets"] == ["2.text"] and rows["width"]["value"] == 512
    assert body["output_node"] == "7" and body["output_type"] == ["images"] and body["warnings"] == []
    assert not (fsettings.config_dir / "workflows").exists()

    g = sd_graph()
    g.pop("7")
    warned = client.post("/api/tools/import/comfy", json={"workflow": g, "kind": "image.generate"}).json()["warnings"]
    assert [w["code"] for w in warned] == ["no_output"] and "saves the result" in warned[0]["message"]


def test_save_writes_the_template_and_the_model_entry_and_lists_it(client: TestClient, fsettings: Settings) -> None:
    body = {"workflow": sd_graph(), "kind": "image.generate", "name": "my-sd", "family": "sd15"}
    r = client.post("/api/tools/comfy", json=body)
    assert r.status_code == 201 and r.json()["ref"] == "my-sd" and r.json()["reloaded"] is True
    assert (fsettings.config_dir / "workflows" / "my-sd.json").is_file()
    assert "my-sd" in {m["ref"] for m in client.get("/api/tools").json()}          # the server uses it at once
    assert 'workflow = "my-sd"' in (fsettings.config_dir / "models.toml").read_text()
    again = client.post("/api/tools/comfy", json=body)
    assert again.status_code == 409 and again.json()["error"]["code"] == "connect_failed"
    assert client.post("/api/tools/comfy", json=body | {"force": True}).status_code == 201


def test_refusals_carry_a_localised_reason(client: TestClient) -> None:
    r = client.post("/api/tools/import/comfy", json={"workflow": {"nodes": [], "links": []}, "kind": "image.generate"})
    assert r.status_code == 422 and "API format" in r.json()["error"]["detail"]
    r = client.post("/api/tools/import/comfy", json={"workflow": sd_graph(), "kind": "nope"})
    assert r.status_code == 422
    r = client.post("/api/tools/comfy", json={"workflow": sd_graph(), "kind": "image.generate", "name": "Bad Name"})
    assert r.status_code == 422


def test_the_list_marks_templates_that_are_the_operators_own(client: TestClient, fsettings: Settings) -> None:
    rows = client.get("/api/tools").json()
    assert rows and all(not r["mine"] for r in rows)
    assert {r["provider"] for r in rows} <= {"comfy", "cli", "http"} and "comfy" in {r["provider"] for r in rows}


def test_a_model_added_to_models_toml_by_hand_is_loaded_by_the_reload_call(
        client: TestClient, fsettings: Settings) -> None:
    before = client.post("/api/config/reload").json()
    assert before["reloaded"] is True and before["error"] == ""
    models = fsettings.config_dir / "models.toml"
    models.write_text(models.read_text() + '\n[models."by-hand"]\nkind = "image.generate"\nprovider = "comfy"\n')
    after = client.post("/api/config/reload").json()
    assert after["models"] == before["models"] + 1
    assert "by-hand" in {m["ref"] for m in client.get("/api/tools").json()}


def test_a_broken_config_file_keeps_the_old_configuration_and_says_why(
        client: TestClient, fsettings: Settings) -> None:
    n = client.post("/api/config/reload").json()["models"]
    (fsettings.config_dir / "models.toml").write_text("this is [not toml\n")
    r = client.post("/api/config/reload").json()
    assert r["reloaded"] is False and r["error"] and r["models"] == n
    assert client.get("/api/tools").status_code == 200                      # the old configuration still serves


def test_an_app_given_settings_but_no_loader_cannot_reload(fsettings: Settings) -> None:
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as c:
        r = c.post("/api/config/reload").json()
        assert r["reloaded"] is False and "no way to read" in r["error"]


def test_templates_are_offered_and_check_without_writing(client: TestClient, fsettings: Settings) -> None:
    rows = client.get("/api/tools/templates").json()
    assert [r["name"] for r in rows] == ["cli", "http_sync", "http_async"] and rows[1]["services"]
    r = client.post("/api/tools/connector/check", json={"models": rows[0]["models"]}).json()
    assert r["ok"] is True and r["provider"] == "cli" and r["ref"] == "my-cli-tool"
    assert [i["code"] for i in r["issues"]] == ["license_empty"] and "licence" in r["issues"][0]["message"]
    bad = client.post("/api/tools/connector/check", json={"models": "[models.x"}).json()
    assert bad["ok"] is False and bad["issues"][0]["code"] == "toml_syntax"
    assert "my-cli-tool" not in (fsettings.config_dir / "models.toml").read_text()


def test_saving_a_connector_writes_it_and_the_server_uses_it_at_once(client: TestClient, fsettings: Settings) -> None:
    models, services = (lambda r: (r["models"], r["services"]))(client.get("/api/tools/templates").json()[1])
    r = client.post("/api/tools/connector", json={"models": models, "services": services})
    assert r.status_code == 201 and r.json()["ref"] == "my-http-tool" and r.json()["reloaded"] is True
    assert "my-http-tool" in {m["ref"] for m in client.get("/api/tools").json()}
    again = client.post("/api/tools/connector", json={"models": models, "services": services})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "connect_failed"
    assert client.post("/api/tools/connector", json={"models": models, "services": services, "force": True}
                       ).status_code == 201


def test_a_connector_with_errors_is_refused_with_a_reason(client: TestClient, fsettings: Settings) -> None:
    before = (fsettings.config_dir / "models.toml").read_text()
    r = client.post("/api/tools/connector", json={"models": "[models.x]\nkind = \"audio.tts\"\nprovider = \"nope\"\n"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "connect_failed"
    assert (fsettings.config_dir / "models.toml").read_text() == before
