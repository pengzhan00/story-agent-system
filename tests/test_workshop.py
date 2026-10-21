"""Workshop: modules on their own, presets, and pipelines whose steps hand their output on."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings, load_settings
from storyagent.db import Database
from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.assetrefs import RefError, Resolver
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker
from storyagent.workshop import MODULES, WorkshopError, build_inputs, kind_spec, output_media

from .conftest import FAKE_MODELS, FAKE_SERVICES

EXTRA = """
[models."fake-image"]
kind = "image.generate"
provider = "fake"
timeout_sec = 30
[models."fake-image".params]
width = 64
[models."fake-avatar"]
kind = "video.avatar"
provider = "fake"
timeout_sec = 30
[models."fake-music"]
kind = "audio.music"
provider = "fake"
timeout_sec = 30
"""


@pytest.fixture
def wsettings(tmp_path: Path) -> Settings:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "models.toml").write_text(FAKE_MODELS + EXTRA)
    (home / "config" / "services.toml").write_text(FAKE_SERVICES)
    return load_settings({"STORY_AGENT_HOME": str(home)})


@pytest.fixture
def api(wsettings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(wsettings), raise_server_exceptions=False) as c:
        yield c


def drain(settings: Settings) -> None:
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    assert Worker(settings, Database(settings.db_path), reg, probes=[]).drain(20)


# ---- inputs ---------------------------------------------------------------------------------------
def test_inputs_follow_the_kind_description() -> None:
    values = {"text": " hi ", "voice": "asset:x", "voice_text": "ref", "consent": True}
    tts = build_inputs(kind_spec("audio.tts"), values)
    assert tts == {"text": "hi", "voice_ref": {"audio": "asset:x", "text": "ref", "consent": True}}
    lip = build_inputs(kind_spec("video.lipsync"), {"image": "a.png", "audio": "b.wav"})
    assert lip == {"refs": ["a.png", "b.wav"]}
    with pytest.raises(WorkshopError) as e:
        build_inputs(kind_spec("image.edit"), {"prompt": "x"})
    assert e.value.code == "workshop_missing_input"


def test_every_module_kind_has_a_default_model_and_an_output() -> None:
    s = load_settings({"STORY_AGENT_HOME": "/nonexistent-home"})
    for m in MODULES:
        for k in m.kinds:
            assert k.kind.value in s.default_models, k.kind
            assert output_media(k.kind) in ("image", "audio", "video", "text")


# ---- API ----------------------------------------------------------------------------------------
def test_modules_describe_models_params_and_inputs(api: TestClient) -> None:
    mods = {m["id"]: m for m in api.get("/api/workshop").json()}
    assert set(mods) == {"image", "song", "voice", "video", "transcript"}
    gen = next(k for k in mods["image"]["kinds"] if k["kind"] == "image.generate")
    assert any(m["ref"] == "fake-image" for m in gen["models"])
    assert [f["name"] for f in gen["inputs"]][:2] == ["prompt", "negative_prompt"] and gen["output"] == "image"
    assert isinstance(gen["schema"], dict)


def test_preset_decides_model_tier_and_params_but_a_run_can_override(api: TestClient, wsettings: Settings) -> None:
    r = api.put("/api/workshop/presets/video.generate",
                json={"model_ref": "fake-video", "tier": "draft", "params": {"width": 77, "fps": 12}})
    assert r.status_code == 200 and r.json()["params"]["width"] == 77
    jobs = api.post("/api/workshop/run", json={"kind": "video.generate", "inputs": {"prompt": "a cat"}}).json()
    assert len(jobs) == 1 and jobs[0]["project_id"] is None
    assert jobs[0]["model_ref"] == "fake-video" and jobs[0]["tier"] == "draft"
    assert jobs[0]["params"]["width"] == 77 and jobs[0]["params"]["fps"] == 12     # preset over the draft tier (50)
    once = api.post("/api/workshop/run", json={"kind": "video.generate", "inputs": {"prompt": "a dog"},
                                                "params": {"width": 33}}).json()[0]
    assert once["params"]["width"] == 33 and once["params"]["fps"] == 12
    desc = next(k for m in api.get("/api/workshop").json() for k in m["kinds"] if k["kind"] == "video.generate")
    assert desc["model_ref"] == "fake-video" and desc["tier"] == "draft" and desc["preset_params"]["width"] == 77
    assert api.delete("/api/workshop/presets/video.generate").status_code == 204
    assert api.get("/api/workshop").status_code == 200


def test_params_endpoint_shows_what_a_run_would_use(api: TestClient) -> None:
    q = "/api/workshop/params?kind=video.generate&model_ref=fake-video"
    p = api.get(q + "&tier=draft").json()
    assert p["base"]["width"] == 50 and p["preset"] == {} and p["effective"] == p["base"]
    api.put("/api/workshop/presets/video.generate", json={"model_ref": "fake-video", "params": {"width": 5}})
    p = api.get(q + "&tier=final").json()
    assert p["base"]["width"] == 200 and p["preset"] == {"width": 5} and p["effective"]["width"] == 5
    assert api.get("/api/workshop/params?kind=video.generate&model_ref=fake-tts").status_code == 422


def test_preset_rejects_a_model_of_another_kind(api: TestClient) -> None:
    r = api.put("/api/workshop/presets/video.generate", json={"model_ref": "fake-tts"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "workshop_wrong_model"


def test_run_several_candidates_and_read_them_back(api: TestClient, wsettings: Settings) -> None:
    jobs = api.post("/api/workshop/run", json={"kind": "image.generate", "inputs": {"prompt": "x"},
                                                "model_ref": "fake-image", "seed": 10, "count": 3}).json()
    assert sorted(j["seed"] for j in jobs) == [10, 11, 12]
    drain(wsettings)
    hist = api.get("/api/workshop/history?module=image").json()
    assert len(hist) == 3 and all(h["job"]["status"] == "completed" and h["assets"] for h in hist)
    assert api.get("/api/workshop/history?module=nope").status_code == 404


def test_standalone_jobs_stay_out_of_projects(api: TestClient) -> None:
    api.post("/api/projects", json={"name": "P"})
    api.post("/api/workshop/run", json={"kind": "image.generate", "inputs": {"prompt": "x"}})
    assert api.get("/api/jobs?project_id=1").json() == []
    assert len(api.get("/api/workshop/history").json()) == 1


# ---- pipelines ------------------------------------------------------------------------------------
STEPS = [
    {"kind": "image.generate", "model_ref": "fake-image", "inputs": {"prompt": "portrait"}},
    {"kind": "video.avatar", "model_ref": "fake-avatar", "inputs": {"image": "@prev", "audio": "/tmp/x.wav"}},
]


def test_pipeline_step_waits_for_the_one_before_and_receives_its_output(api: TestClient, wsettings: Settings) -> None:
    body = api.post("/api/workshop/pipelines", json={"steps": STEPS}).json()
    first, second = body["jobs"]
    assert first["pipeline_id"] == second["pipeline_id"] == body["pipeline_id"]
    assert [first["pipeline_step"], second["pipeline_step"]] == [1, 2]
    assert second["depends_on"] == first["id"] and second["inputs"]["image"] == f"job:{first['id']}"
    assert first["depends_on"] is None
    from storyagent.media.ledger import Ledger

    led = Ledger(Database(wsettings.db_path), wsettings)
    assert [j.id for j in led.queued()] == [first["id"]]          # the second is not offered yet
    with pytest.raises(RefError, match="has not completed"):
        Resolver(wsettings, led).resolve({"image": f"job:{first['id']}"}, {})
    drain(wsettings)
    statuses = {h["job"]["id"]: h["job"]["status"] for h in api.get("/api/workshop/history").json()}
    assert statuses == {first["id"]: "completed", second["id"]: "completed"}
    res = Resolver(wsettings, led)
    inputs, _, resolved = res.resolve({"image": f"job:{first['id']}"}, {})
    assert Path(inputs["image"]).is_file() and resolved
    with pytest.raises(RefError, match="unknown job"):
        Resolver(wsettings, led).resolve({"image": "job:" + "0" * 32}, {})


def test_a_failed_step_takes_the_waiting_steps_down_with_it(api: TestClient, wsettings: Settings) -> None:
    from storyagent.media.ledger import Ledger

    steps = [*STEPS, {"kind": "audio.music", "model_ref": "fake-music", "inputs": {"prompt": "song"}}]
    jobs = api.post("/api/workshop/pipelines", json={"steps": steps}).json()["jobs"]
    led = Ledger(Database(wsettings.db_path), wsettings)
    led.fail(jobs[0]["id"], ErrorCode.TOOL_FAILED, "boom")
    after = {j["pipeline_step"]: j for j in (api.get(f"/api/jobs/{x['id']}").json() for x in jobs)}
    assert after[1]["status"] == "workflow_error" and after[2]["error_code"] == "dependency_failed"
    assert after[3]["status"] == "queued"                         # the independent step is not affected
    assert MediaKind.AUDIO_MUSIC.value == "audio.music"


@pytest.mark.parametrize(("steps", "code"), [
    ([{"kind": "image.generate", "inputs": {"prompt": "@prev"}}], "pipeline_no_previous"),
    ([{"kind": "image.generate", "inputs": {"prompt": "x"}},
      {"kind": "image.edit", "inputs": {"prompt": "@prev", "image": "a.png"}}], "pipeline_incompatible"),
    ([{"kind": "audio.music", "inputs": {"prompt": "x"}},
      {"kind": "video.avatar", "inputs": {"image": "@prev", "audio": "a.wav"}}], "pipeline_incompatible"),
])
def test_a_handover_that_does_not_fit_is_refused_before_anything_is_queued(api: TestClient, steps: list[Any],
                                                                            code: str) -> None:
    r = api.post("/api/workshop/pipelines", json={"steps": steps})
    assert r.status_code == 422 and r.json()["error"]["code"] == code
    assert api.get("/api/workshop/history").json() == []


def test_upload_of_an_input_file_without_a_project(api: TestClient) -> None:
    r = api.post("/api/workshop/upload?filename=face.png", content=b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    assert r.status_code == 201
    a = r.json()
    assert a["project_id"] is None and a["kind"].startswith("upload.")
    job = api.post("/api/workshop/run", json={"kind": "image.edit",
                                                 "inputs": {"prompt": "x", "image": f"asset:{a['id']}"}}).json()[0]
    assert job["inputs"]["refs"] == [f"asset:{a['id']}"]
