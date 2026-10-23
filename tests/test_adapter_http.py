"""``provider = "http"``: any HTTP service as a model, from a ``[models.X.connector]`` table."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.generic_http import HttpAdapter
from storyagent.media.contracts import HealthState

from .adapter_kit import job, make_rig, make_settings, wav_bytes
from .fakes.http_server import FakeServer
from .test_adapter_mflux import png_bytes

SYNC = '''
[models."img-sync"]
kind = "image.generate"
provider = "http"
timeout_sec = 20
[models."img-sync".connector]
service = "mytool"
mode = "sync"
ext = "png"
headers = { Authorization = "Bearer {env.MYTOOL_KEY}" }
[models."img-sync".connector.submit]
path = "/v1/images"
json = { prompt = "{prompt}", seed = "{seed}", size = "{width}x{height}", steps = "{steps|20}", user = "{nobody}" }
[models."img-sync".connector.result]
b64_path = "data.0.b64_json"
'''
ASYNC = '''
[models."tts-async"]
kind = "audio.tts"
provider = "http"
timeout_sec = 20
[models."tts-async".connector]
service = "mytool"
ext = "wav"
required = ["text"]
[models."tts-async".connector.submit]
path = "/speak"
json = { text = "{text}" }
id_path = "job.id"
[models."tts-async".connector.poll]
path = "/jobs/{id}"
status_path = "state"
done = ["finished"]
failed = ["broken"]
progress_path = "pct"
progress_scale = 100
error_path = "why"
[models."tts-async".connector.result]
url_path = "result.url"
'''
BODY = '''
[models."raw"]
kind = "audio.sfx"
provider = "http"
timeout_sec = 20
[models."raw".connector]
service = "mytool"
mode = "sync"
ext = "wav"
[models."raw".connector.submit]
path = "/sfx"
query = { q = "{prompt}" }
[models."raw".connector.result]
content = "body"
'''


@pytest.fixture
def server() -> Any:
    with FakeServer() as srv:
        srv.route("GET", "/health", {"ok": True})
        yield srv


def rig(tmp_path: Path, server: FakeServer, models: str):  # noqa: ANN201
    s = make_settings(tmp_path, services=f'[services.mytool]\nurl = "{server.url}"\nhealth_path = "/health"\n',
                      models=models)
    adapter = HttpAdapter(s, poll_interval_sec=0.01)
    return make_rig(s, adapter), adapter


def test_a_sync_service_answers_with_the_picture_and_absent_values_are_left_out(
        tmp_path: Path, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYTOOL_KEY", "s3cret")
    server.route("POST", "/v1/images", {"data": [{"b64_json": base64.b64encode(png_bytes(64, 64)).decode()}]})
    r, _ = rig(tmp_path, server, SYNC)
    rec, assets = r.run(job(MediaKind.IMAGE_GENERATE, "img-sync", {"prompt": "a cat"}, seed=4,
                            params={"width": 64, "height": 64}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    req = next(q for q in server.requests if q.path == "/v1/images")
    assert req.json == {"prompt": "a cat", "seed": 4, "size": "64x64", "steps": 20}   # fallback used, {nobody} left out
    assert req.headers["authorization"] == "Bearer s3cret"                  # the key came from the environment
    [a] = assets
    assert (a.width, a.height) == (64, 64) and a.metadata["service"] == "mytool"


def test_an_async_service_is_polled_until_it_is_finished_and_the_file_is_downloaded(
        tmp_path: Path, server: FakeServer) -> None:
    server.route("POST", "/speak", {"job": {"id": 77}})
    server.route("GET", "/jobs/77", [{"state": "running", "pct": 30}, {"state": "finished",
                                                                       "result": {"url": "/files/out.wav"}}])
    server.route("GET", "/files/out.wav", wav_bytes())
    r, _ = rig(tmp_path, server, ASYNC)
    rec, assets = r.run(job(MediaKind.AUDIO_TTS, "tts-async", {"text": "hello"}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert next(q for q in server.requests if q.path == "/speak").json == {"text": "hello"}
    assert assets[0].has_audio and assets[0].kind == "audio.tts"


def test_a_failed_job_carries_the_services_reason(tmp_path: Path, server: FakeServer) -> None:
    server.route("POST", "/speak", {"job": {"id": 1}})
    server.route("GET", "/jobs/1", {"state": "broken", "why": "voice not found"})
    r, _ = rig(tmp_path, server, ASYNC)
    rec, assets = r.run(job(MediaKind.AUDIO_TTS, "tts-async", {"text": "x"}))
    assert rec.error_code == ErrorCode.TOOL_FAILED.value and "voice not found" in (rec.error_message or "")
    assert not assets


def test_the_answer_body_can_be_the_file_itself(tmp_path: Path, server: FakeServer) -> None:
    server.route("POST", "/sfx", wav_bytes())
    r, _ = rig(tmp_path, server, BODY)
    rec, assets = r.run(job(MediaKind.AUDIO_SFX, "raw", {"prompt": "door knock"}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert next(q for q in server.requests if q.path == "/sfx").query == {"q": ["door knock"]}
    assert assets[0].has_audio


def test_a_missing_required_value_or_a_service_error_is_reported(tmp_path: Path, server: FakeServer) -> None:
    r, _ = rig(tmp_path, server, ASYNC)
    rec, _ = r.run(job(MediaKind.AUDIO_TTS, "tts-async", {"prompt": "no text given"}))
    assert rec.status is JobStatus.FAILED_VALIDATION and rec.error_code == ErrorCode.INVALID_INPUT.value
    assert not any(q.path == "/speak" for q in server.requests)
    server.route("POST", "/speak", (500, {"error": "boom"}))
    rec, _ = r.run(job(MediaKind.AUDIO_TTS, "tts-async", {"text": "again"}))
    assert rec.error_code == ErrorCode.SERVICE_REJECTED.value


def test_a_bad_connector_table_is_refused_before_anything_is_sent(tmp_path: Path, server: FakeServer) -> None:
    models = ('[models."bad"]\nkind = "audio.tts"\nprovider = "http"\n[models."bad".connector]\n'
              'service = "mytool"\n[models."bad".connector.submit]\npath = "/x"\n')       # async without id_path / poll
    r, _ = rig(tmp_path, server, models)
    rec, _ = r.run(job(MediaKind.AUDIO_TTS, "bad", {"text": "x"}))
    assert rec.status is JobStatus.FAILED_VALIDATION and "async" in (rec.error_message or "")
    models = ('[models."nosvc"]\nkind = "audio.tts"\nprovider = "http"\n[models."nosvc".connector]\n'
              'service = "ghost"\nmode = "sync"\n[models."nosvc".connector.submit]\npath = "/x"\n')
    r, _ = rig(tmp_path / "b", server, models)
    rec, _ = r.run(job(MediaKind.AUDIO_TTS, "nosvc", {"text": "x"}))
    assert "services.toml" in (rec.error_message or "")


def test_health_probes_the_services_the_models_use(tmp_path: Path, server: FakeServer) -> None:
    _, adapter = rig(tmp_path, server, SYNC)
    assert adapter.health().state is HealthState.OK
    s = make_settings(tmp_path / "down", services='[services.mytool]\nurl = "http://127.0.0.1:9"\n', models=SYNC)
    report = HttpAdapter(s).health()
    assert report.state is HealthState.NOT_RUNNING and report.missing == ("mytool",)
