from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings

from .test_comfy_import import sd_graph


@pytest.fixture
def client(fsettings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(fsettings), raise_server_exceptions=False, headers={"Accept-Language": "en"}) as c:
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
    assert r.status_code == 201 and r.json()["ref"] == "my-sd" and r.json()["restart_needed"] is True
    assert (fsettings.config_dir / "workflows" / "my-sd.json").is_file()
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
