from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters._http import ServiceClient
from storyagent.media.adapters.acestep import AceStepAdapter
from storyagent.media.contracts import HealthState

from .adapter_kit import job, make_rig, make_settings, wav_bytes
from .fakes.http_server import FakeServer

MODELS = """
[models."ace-test"]
kind = "audio.music"
provider = "acestep"
api_model = "acestep-test"
timeout_sec = 20
[models."ace-test".params]
duration = 12
steps = 8
vocal_language = "zh"
audio_format = "wav"
"""


def inventory(models: list[str] | None = None, lm: bool = True) -> dict[str, Any]:
    names = ["acestep-test"] if models is None else models
    return {"data": {"models": [{"name": n, "is_loaded": True} for n in names], "llm_initialized": lm}}


def result(stage: str, progress: float, file: str = "", error: str = "") -> dict[str, Any]:
    item = {"stage": stage, "progress": progress, "file": file, "error": error, "seed": 7}
    return {"data": [{"status": 1 if stage == "succeeded" else 0, "result": json.dumps([item])}]}


def result_v2(status: int, file: str = "") -> dict[str, Any]:
    """The format of the ACE-Step server on the dev machine: outer status only, `metas` per result."""
    item = {"file": file, "status": str(status), "prompt": "expanded caption", "metas": {"bpm": 52, "genres": "N/A"}}
    return {"data": [{"task_id": "t-1", "status": status, "progress_text": "", "result": json.dumps([item, item])}]}


@pytest.fixture
def server() -> Any:
    with FakeServer() as srv:
        srv.route("GET", "/v1/model_inventory", inventory())
        srv.route("POST", "/release_task", {"data": {"task_id": "t-1"}})
        srv.route("POST", "/query_result",
                  [result("running", 0.3), result("running", 0.8),
                   result("succeeded", 1.0, "/v1/audio?path=%2Ftmp%2Fout.wav")])
        srv.route("GET", "/v1/audio", wav_bytes())
        yield srv


def rig(tmp_path: Path, server: FakeServer):  # noqa: ANN201
    s = make_settings(tmp_path, services=f'[services.acestep]\nurl = "{server.url}"\n', models=MODELS)
    adapter = AceStepAdapter(ServiceClient(s.service("acestep")), {"ace-test": "acestep-test"}, poll_interval_sec=0.01)
    return make_rig(s, adapter), adapter


def test_generates_music_and_registers_the_asset(tmp_path: Path, server: FakeServer) -> None:
    r, _ = rig(tmp_path, server)
    rec, assets = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "guzheng, slow", "text": "[verse]\nla la"},
                            seed=7, params={"duration": 20}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    [a] = assets
    assert a.kind == "audio.music" and a.path.endswith(".wav") and a.has_audio and a.duration_sec
    assert a.metadata["task_id"] == "t-1" and a.metadata["api_model"] == "acestep-test"

    body = next(q.json for q in server.requests if q.path == "/release_task")
    assert body["prompt"] == "guzheng, slow" and body["lyrics"] == "[verse]\nla la"
    assert body["audio_duration"] == 20 and body["inference_steps"] == 8  # job param beats model param
    assert body["use_random_seed"] is False and body["seed"] == 7 and body["model"] == "acestep-test"
    assert body["vocal_language"] == "zh" and body["audio_format"] == "wav"
    assert "duration" not in body and "steps" not in body  # renamed, not duplicated


def test_progress_is_reported(tmp_path: Path, server: FakeServer) -> None:
    r, adapter = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}))
    events = [e["event"] for e in r.ledger.events(rec.id)]
    assert events[:3] == ["enqueued", "claimed", "submitted"] and events[-1] == "completed"
    assert sum(1 for q in server.requests if q.path == "/query_result") >= 3


def test_missing_model_on_server_fails_validation(tmp_path: Path, server: FakeServer) -> None:
    server.route("GET", "/v1/model_inventory", inventory(models=["something-else"]))
    r, _ = rig(tmp_path, server)
    rec, assets = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}))
    assert rec.status is JobStatus.FAILED_VALIDATION and rec.error_code == ErrorCode.MISSING_MODEL.value
    assert not assets and not any(q.path == "/release_task" for q in server.requests)


def test_vocals_without_lm_fail_validation(tmp_path: Path, server: FakeServer) -> None:
    server.route("GET", "/v1/model_inventory", inventory(lm=False))
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x", "text": "lyrics"}))
    assert rec.error_code == ErrorCode.MISSING_MODEL.value and "LM" in (rec.error_message or "")
    rec2, _ = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "instrumental only"}, seed=2))
    assert rec2.status is JobStatus.COMPLETED  # instrumentals do not need the LM


def test_empty_request_is_invalid_input(tmp_path: Path, server: FakeServer) -> None:
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {}))
    assert rec.error_code == ErrorCode.INVALID_INPUT.value


def test_service_down_is_a_transport_error(tmp_path: Path) -> None:
    s = make_settings(tmp_path, services='[services.acestep]\nurl = "http://127.0.0.1:9"\n', models=MODELS)
    r = make_rig(s, AceStepAdapter(ServiceClient(s.service("acestep"), timeout=1), {"ace-test": "acestep-test"}))
    rec, _ = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}))
    assert rec.status is JobStatus.TRANSPORT_ERROR and rec.error_code == ErrorCode.SERVICE_UNREACHABLE.value


def test_server_reported_failure_is_a_workflow_error(tmp_path: Path, server: FakeServer) -> None:
    server.route("POST", "/query_result", [result("running", 0.2), result("failed", 0.2, error="CUDA oops")])
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}))
    assert rec.status is JobStatus.WORKFLOW_ERROR and "CUDA oops" in (rec.error_message or "")


def test_success_without_a_file_is_not_completed(tmp_path: Path, server: FakeServer) -> None:
    server.route("POST", "/query_result", result("succeeded", 1.0, file=""))
    r, _ = rig(tmp_path, server)
    rec, assets = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}))
    assert rec.status is JobStatus.FAILED_VALIDATION and rec.error_code == ErrorCode.OUTPUT_MISSING.value
    assert not assets


def test_unreadable_audio_is_rejected(tmp_path: Path, server: FakeServer) -> None:
    server.route("GET", "/v1/audio", b"this is not audio")
    r, _ = rig(tmp_path, server)
    rec, assets = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}))
    assert rec.error_code == ErrorCode.OUTPUT_INVALID.value and not assets


def test_unknown_param_warns_but_runs(tmp_path: Path, server: FakeServer) -> None:
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}, params={"warp_drive": 9}))
    assert rec.status is JobStatus.COMPLETED
    assert any(e["event"] == "param_warning" for e in r.ledger.events(rec.id))
    body = next(q.json for q in server.requests if q.path == "/release_task")
    assert "warp_drive" not in body  # never forwarded blindly


def test_bad_param_value_is_rejected_before_submit(tmp_path: Path, server: FakeServer) -> None:
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}, params={"steps": 9999}))
    assert rec.error_code == ErrorCode.INVALID_PARAMS.value
    assert not any(q.path == "/release_task" for q in server.requests)


def test_reattach_after_restart_polls_the_same_task(tmp_path: Path, server: FakeServer) -> None:
    r, adapter = rig(tmp_path, server)
    rec = r.submit(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}, seed=1))
    # simulate a worker that crashed after submitting: the ledger remembers the external id
    claimed = r.ledger.claim(rec.id, "dead-worker", 0.0)
    assert claimed is not None
    r.ledger.set_external_id(rec.id, "dead-worker", "t-1")
    r.ledger.requeue_expired()
    assert r.worker.drain(20)
    done = r.ledger.get_job(rec.id)
    assert done.status is JobStatus.COMPLETED
    assert not any(q.path == "/release_task" for q in server.requests)  # no duplicate generation
    assert "reattached" in [e["event"] for e in r.ledger.events(rec.id)]


def test_lost_task_is_resubmitted(tmp_path: Path, server: FakeServer) -> None:
    calls = {"n": 0}

    def query(req: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:  # the restarted server has forgotten the old task
            return {"data": []}
        return result("succeeded", 1.0, "/v1/audio?path=%2Ftmp%2Fout.wav")

    server.route("POST", "/query_result", query)
    r, _ = rig(tmp_path, server)
    rec = r.submit(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}, seed=1))
    r.ledger.claim(rec.id, "dead-worker", 0.0)
    r.ledger.set_external_id(rec.id, "dead-worker", "old-task")
    r.ledger.requeue_expired()
    assert r.worker.drain(20)
    assert r.ledger.get_job(rec.id).status is JobStatus.COMPLETED
    assert any(q.path == "/release_task" for q in server.requests)
    assert "resubmitting" in [e["event"] for e in r.ledger.events(rec.id)]


def test_health_states(tmp_path: Path, server: FakeServer) -> None:
    _, adapter = rig(tmp_path, server)
    assert adapter.health().state is HealthState.OK
    server.route("GET", "/v1/model_inventory", inventory(lm=False))
    assert adapter.health().state is HealthState.DEGRADED
    server.route("GET", "/v1/model_inventory", inventory(models=[]))
    assert adapter.health().state is HealthState.MISSING_MODEL
    s = make_settings(tmp_path / "x", services='[services.acestep]\nurl = "http://127.0.0.1:9"\n')
    down = AceStepAdapter(ServiceClient(s.service("acestep"), timeout=1))
    assert down.health().state is HealthState.NOT_RUNNING


def test_registered_by_default(tmp_path: Path) -> None:
    from storyagent.media.registry import default_registry

    s = make_settings(tmp_path)
    reg = default_registry(s)
    assert "acestep" in reg
    adapter = reg.get("acestep")
    assert isinstance(adapter, AceStepAdapter)
    assert adapter.api_model(type("J", (), {"model_ref": "acestep-1.5-turbo"})()) == "acestep-v15-turbo"  # type: ignore[arg-type]


def test_newer_server_format_without_stage(tmp_path: Path, server: FakeServer) -> None:
    server.route("POST", "/query_result", [result_v2(0), result_v2(1, "/v1/audio?path=%2Ftmp%2Fa.wav")])
    r, _ = rig(tmp_path, server)
    rec, [a] = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}, seed=3))
    assert rec.status is JobStatus.COMPLETED
    assert a.metadata["bpm"] == 52 and "genres" not in a.metadata  # "N/A" placeholders are dropped
    assert a.metadata["server_caption"] == "expanded caption"
    body = next(q.json for q in server.requests if q.path == "/release_task")
    assert body["batch_size"] == 1  # candidates are separate jobs, never server-side batches


def test_newer_server_failure(tmp_path: Path, server: FakeServer) -> None:
    server.route("POST", "/query_result", result_v2(2))
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.AUDIO_MUSIC, "ace-test", {"prompt": "x"}))
    assert rec.status is JobStatus.WORKFLOW_ERROR
