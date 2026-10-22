from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from storyagent.config import load_settings
from storyagent.db import Database, migrate
from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters._http import ServiceClient
from storyagent.media.adapters.drawthings import DrawThingsAdapter
from storyagent.media.contracts import HealthState
from storyagent.projects import ProjectStateError, ProjectStore

from .adapter_kit import job, make_rig, make_settings
from .fakes.http_server import FakeServer
from .test_adapter_mflux import png_bytes

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples" / "drawthings-lustify"
MODELS = """
[models."dt-test"]
kind = "image.generate"
provider = "drawthings"
api_model = "test_f16.ckpt"
size_multiple = 64
timeout_sec = 20
[models."dt-test".params]
width = 704
height = 1280
"""


def reply(width: int = 32, height: int = 48) -> dict[str, Any]:
    return {"images": [base64.b64encode(png_bytes(width, height)).decode()]}


@pytest.fixture
def server() -> Any:
    with FakeServer() as srv:
        srv.route("GET", "/", {"model": "test_f16.ckpt"})
        srv.route("POST", "/sdapi/v1/txt2img", reply())
        srv.route("POST", "/sdapi/v1/img2img", reply())
        yield srv


def rig(tmp_path: Path, server: FakeServer):  # noqa: ANN201
    s = make_settings(tmp_path, services=f'[services.drawthings]\nurl = "{server.url}"\n', models=MODELS)
    adapter = DrawThingsAdapter(ServiceClient(s.service("drawthings")), {"dt-test": "test_f16.ckpt"},
                                poll_interval_sec=0.01)
    return make_rig(s, adapter), adapter


def test_generates_an_image_and_sends_only_what_is_set(tmp_path: Path, server: FakeServer) -> None:
    r, _ = rig(tmp_path, server)
    rec, assets = r.run(job(MediaKind.IMAGE_GENERATE, "dt-test",
                            {"prompt": "a quiet street", "negative_prompt": "blur"},
                            seed=7, params={"steps": 12, "guidance": 5.5}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    [a] = assets
    assert a.kind == "image.generate" and a.path.endswith(".png") and a.width == 32 and a.height == 48
    assert a.metadata["api_model"] == "test_f16.ckpt"

    body = next(q.json for q in server.requests if q.path == "/sdapi/v1/txt2img")
    assert body["prompt"] == "a quiet street" and body["negative_prompt"] == "blur" and body["seed"] == 7
    assert body["model"] == "test_f16.ckpt" and body["width"] == 704 and body["height"] == 1280
    assert body["steps"] == 12 and body["guidance_scale"] == 5.5 and "guidance" not in body and body["batch_size"] == 1
    assert "sampler" not in body and "shift" not in body   # unset: the app's own settings apply


def test_the_request_table_is_passed_through_and_a_reference_makes_img2img(tmp_path: Path, server: FakeServer) -> None:
    r, _ = rig(tmp_path, server)
    ref = tmp_path / "ref.png"
    ref.write_bytes(png_bytes())
    rec, _ = r.run(job(MediaKind.IMAGE_GENERATE, "dt-test", {"prompt": "x", "refs": [str(ref)]},
                       params={"strength": 0.6, "request": {"hires_fix": True}}))
    assert rec.status is JobStatus.COMPLETED
    body = next(q.json for q in server.requests if q.path == "/sdapi/v1/img2img")
    assert body["strength"] == 0.6 and body["hires_fix"] is True and len(body["init_images"]) == 1
    assert not any(q.path == "/sdapi/v1/txt2img" for q in server.requests)


def test_the_lustify_sampling_values_reach_the_request(tmp_path: Path, server: FakeServer) -> None:
    from storyagent.media.params import resolve_params

    lustify = lustify_settings(tmp_path / "l")
    params = resolve_params(lustify, "lustify-krea2-drawthings", "preview", frame=(720, 1280))
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.IMAGE_GENERATE, "dt-test", {"prompt": "x"}, params=params))
    assert rec.status is JobStatus.COMPLETED
    body = next(q.json for q in server.requests if q.path == "/sdapi/v1/txt2img")
    assert (body["width"], body["height"], body["steps"], body["shift"]) == (704, 1280, 28, 3)
    assert body["sampler"] == "Euler A Trailing" and body["guidance_embed"] == 3.5 and "guidance_scale" not in body


def test_health_and_a_stopped_app(tmp_path: Path, server: FakeServer) -> None:
    _, adapter = rig(tmp_path, server)
    assert adapter.health().state is HealthState.OK
    s = make_settings(tmp_path / "x", services='[services.drawthings]\nurl = "http://127.0.0.1:9"\n', models=MODELS)
    down = DrawThingsAdapter(ServiceClient(s.service("drawthings"), timeout=1))
    assert down.health().state is HealthState.NOT_RUNNING
    r = make_rig(s, down)
    rec, assets = r.run(job(MediaKind.IMAGE_GENERATE, "dt-test", {"prompt": "x"}))
    assert rec.error_code == ErrorCode.SERVICE_UNREACHABLE.value and not assets


def test_empty_prompt_is_invalid_input(tmp_path: Path, server: FakeServer) -> None:
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.IMAGE_GENERATE, "dt-test", {}))
    assert rec.error_code == ErrorCode.INVALID_INPUT.value
    assert not any(q.path.startswith("/sdapi") for q in server.requests)


def test_a_rejected_request_and_an_empty_reply_do_not_complete(tmp_path: Path, server: FakeServer) -> None:
    server.route("POST", "/sdapi/v1/txt2img", (400, {"error": "bad model"}))
    r, _ = rig(tmp_path, server)
    rec, assets = r.run(job(MediaKind.IMAGE_GENERATE, "dt-test", {"prompt": "x"}))
    assert rec.error_code == ErrorCode.SERVICE_REJECTED.value and not assets
    server.route("POST", "/sdapi/v1/txt2img", {"images": []})
    rec2, assets2 = r.run(job(MediaKind.IMAGE_GENERATE, "dt-test", {"prompt": "x"}, seed=2))
    assert rec2.error_code == ErrorCode.OUTPUT_MISSING.value and not assets2


def test_not_an_image_is_rejected(tmp_path: Path, server: FakeServer) -> None:
    server.route("POST", "/sdapi/v1/txt2img", {"images": [base64.b64encode(b"no image").decode()]})
    r, _ = rig(tmp_path, server)
    rec, assets = r.run(job(MediaKind.IMAGE_GENERATE, "dt-test", {"prompt": "x"}))
    assert rec.error_code == ErrorCode.OUTPUT_INVALID.value and not assets


# -- the Lustify example (docs/examples/drawthings-lustify) --------------------------------------------------------
def lustify_settings(tmp_path: Path):  # noqa: ANN201
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    for name in ("models", "pipelines"):
        (home / "config" / f"{name}.toml").write_text((EXAMPLES / f"{name}.toml").read_text())
    return load_settings({"STORY_AGENT_HOME": str(home), "STORY_AGENT_LOCALE": "en"})


def test_the_lustify_example_loads_as_a_restricted_plan(tmp_path: Path) -> None:
    s = lustify_settings(tmp_path)
    plan = s.pipeline("vertical_drama_lustify")
    assert plan.rating == "restricted" and plan.models["image.generate"] == "lustify-krea2-drawthings"
    assert "drawthings" in plan.requires and not plan.workflow["keyframe_from_references"]
    model = s.models["lustify-krea2-drawthings"]
    assert model.provider == "drawthings" and model.extra["rating"] == "restricted" and model.heavy
    assert s.service("drawthings").url.endswith(":7860")
    params = model.params
    assert params["steps"] == 28 and params["sampler"] == "Euler A Trailing" and "guidance" not in params
    assert params["request"] == {"guidance_embed": 3.5}


def test_a_project_of_a_restricted_plan_starts_restricted_and_stays_so(tmp_path: Path) -> None:
    s = lustify_settings(tmp_path)
    db = Database(s.db_path)
    migrate(db)
    store = ProjectStore(db, s)
    p = store.create("Private", pipeline="vertical_drama_lustify")
    assert p.rating == "restricted" and p.pipeline == "vertical_drama_lustify"
    assert store.create("Private 2", pipeline="vertical_drama_lustify", rating="general").rating == "restricted"
    general = store.create("Open")
    assert general.rating == "general"
    assert store.update(general.id, pipeline="vertical_drama_lustify").rating == "restricted"  # no files yet
    db.conn.execute("INSERT INTO media_assets(id, project_id, kind, slot, path, sha256, bytes, version, is_current)"
                    " VALUES ('a1', ?, 'image.generate', 'main', 'x.png', 'h', 1, 1, 1)", (store.create("Used").id,))
    used = db.conn.execute("SELECT id FROM projects WHERE name='Used'").fetchone()["id"]
    with pytest.raises(ProjectStateError):
        store.update(used, pipeline="vertical_drama_lustify")   # files exist: the rating cannot rise afterwards
