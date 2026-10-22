from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters._http import ServiceClient
from storyagent.media.adapters.comfy import ComfyAdapter
from storyagent.media.contracts import HealthState

from .adapter_kit import job, make_rig, make_settings, wav_bytes
from .fakes.http_server import FakeServer
from .test_adapter_mflux import png_bytes

MODELS = """
[models."sa-test"]
kind = "audio.sfx"
provider = "comfy"
workflow = "stable_audio_3"
timeout_sec = 20
[models."sa-test".params]
duration = 4
steps = 8
"""
CKPT, ENCODER = "stable_audio_3_medium.safetensors", "t5gemma_b_b_ul2.safetensors"


def history(done: bool = True, status: str = "success", error: str = "") -> dict[str, Any]:
    messages = [["execution_error", {"exception_message": error}]] if error else []
    entry: dict[str, Any] = {"status": {"status_str": status, "completed": done, "messages": messages},
                             "outputs": {"8": {"audio": [{"filename": "sfx_00001.flac", "subfolder": "s",
                                                          "type": "output"}]}} if done and not error else {}}
    return {"p-1": entry}


def object_info(node: str) -> dict[str, Any]:
    names = {"CheckpointLoaderSimple": ("ckpt_name", [CKPT]), "CLIPLoader": ("clip_name", [ENCODER])}
    field, options = names.get(node, ("x", []))
    return {node: {"input": {"required": {field: [options]}}}}


@pytest.fixture
def server() -> Any:
    with FakeServer() as srv:
        srv.route("GET", "/system_stats", {"system": {}})
        for node in ("CheckpointLoaderSimple", "CLIPLoader", "CLIPTextEncode", "EmptyLatentAudio", "KSampler",
                     "VAEDecodeAudio", "SaveAudio"):
            srv.route("GET", f"/object_info/{node}", object_info(node))
        srv.route("POST", "/prompt", {"prompt_id": "p-1"})
        srv.route("GET", "/history/p-1", [{}, history(done=False, status=""), history()])
        srv.route("GET", "/view", wav_bytes())
        srv.route("POST", "/free", {})
        yield srv


def rig(tmp_path: Path, server: FakeServer):  # noqa: ANN201
    s = make_settings(tmp_path, services=f'[services.comfy]\nurl = "{server.url}"\n', models=MODELS)
    adapter = ComfyAdapter(ServiceClient(s.service("comfy")), {"sa-test": "stable_audio_3"}, poll_interval_sec=0.01)
    return make_rig(s, adapter), adapter


def test_makes_a_sound_effect_from_the_template_and_registers_it(tmp_path: Path, server: FakeServer) -> None:
    r, _ = rig(tmp_path, server)
    rec, assets = r.run(job(MediaKind.AUDIO_SFX, "sa-test", {"prompt": "door knock, dry foley"}, seed=11,
                            params={"duration": 3.0}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    [a] = assets
    assert a.kind == "audio.sfx" and a.has_audio and a.duration_sec and a.metadata["prompt_id"] == "p-1"
    graph = next(q.json for q in server.requests if q.path == "/prompt")["prompt"]
    assert graph["3"]["inputs"]["text"] == "door knock, dry foley"       # prompt lands in the bound node input
    assert graph["5"]["inputs"]["seconds"] == 3.0 and graph["6"]["inputs"]["seed"] == 11
    assert graph["6"]["inputs"]["steps"] == 8 and graph["6"]["inputs"]["sampler_name"] == "lcm"   # model param
    assert "dry" in graph["4"]["inputs"]["text"] or "muffled" in graph["4"]["inputs"]["text"]       # template default
    freed = next(q.json for q in server.requests if q.path == "/free")
    assert freed == {"unload_models": True, "free_memory": True}                 # memory is handed back after the clip


def test_a_missing_checkpoint_fails_validation_before_anything_is_queued(tmp_path: Path, server: FakeServer) -> None:
    server.route("GET", "/object_info/CheckpointLoaderSimple", object_info("CheckpointLoaderSimple") | {
        "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["other.safetensors"]]}}}})
    r, _ = rig(tmp_path, server)
    rec, assets = r.run(job(MediaKind.AUDIO_SFX, "sa-test", {"prompt": "x"}))
    assert rec.status is JobStatus.FAILED_VALIDATION and rec.error_code == ErrorCode.MISSING_MODEL.value
    assert not assets and not any(q.path == "/prompt" for q in server.requests)


def test_a_node_the_server_does_not_know_is_a_missing_tool(tmp_path: Path, server: FakeServer) -> None:
    server.route("GET", "/object_info/EmptyLatentAudio", {})
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.AUDIO_SFX, "sa-test", {"prompt": "x"}))
    assert rec.error_code == ErrorCode.MISSING_TOOL.value


def test_an_empty_prompt_is_invalid_input(tmp_path: Path, server: FakeServer) -> None:
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.AUDIO_SFX, "sa-test", {}))
    assert rec.error_code == ErrorCode.INVALID_INPUT.value


def test_a_workflow_error_is_reported(tmp_path: Path, server: FakeServer) -> None:
    server.route("GET", "/history/p-1", history(done=True, status="error", error="out of memory"))
    r, _ = rig(tmp_path, server)
    rec, _ = r.run(job(MediaKind.AUDIO_SFX, "sa-test", {"prompt": "x"}))
    assert rec.status is JobStatus.WORKFLOW_ERROR and "out of memory" in (rec.error_message or "")


def test_success_without_an_audio_output_is_not_completed(tmp_path: Path, server: FakeServer) -> None:
    server.route("GET", "/history/p-1", {"p-1": {"status": {"status_str": "success", "completed": True},
                                                  "outputs": {}}})
    r, _ = rig(tmp_path, server)
    rec, assets = r.run(job(MediaKind.AUDIO_SFX, "sa-test", {"prompt": "x"}))
    assert rec.error_code == ErrorCode.OUTPUT_MISSING.value and not assets


def test_service_down_is_a_transport_error_and_health_says_so(tmp_path: Path) -> None:
    s = make_settings(tmp_path, services='[services.comfy]\nurl = "http://127.0.0.1:9"\n', models=MODELS)
    adapter = ComfyAdapter(ServiceClient(s.service("comfy"), timeout=1), {"sa-test": "stable_audio_3"})
    assert adapter.health().state is HealthState.NOT_RUNNING
    rec, _ = make_rig(s, adapter).run(job(MediaKind.AUDIO_SFX, "sa-test", {"prompt": "x"}))
    assert rec.status is JobStatus.TRANSPORT_ERROR and rec.error_code == ErrorCode.SERVICE_UNREACHABLE.value


EDIT_MODELS = """
[models."klein-comfy"]
kind = "image.edit"
provider = "comfy"
workflow = "flux2_klein_edit"
timeout_sec = 20
[models."klein-comfy".params]
width = 576
height = 1024
steps = 4
ref_megapixels = 0.5
"""
EDIT_NODES = ("CheckpointLoaderSimple", "CLIPLoader", "VAELoader", "CLIPTextEncode", "EmptyFlux2LatentImage",
              "BasicGuider", "RandomNoise", "KSamplerSelect", "Flux2Scheduler", "SamplerCustomAdvanced", "VAEDecode",
              "SaveImage", "LoadImage", "ImageScaleToTotalPixels", "VAEEncode", "ReferenceLatent")


@pytest.fixture
def edit_server() -> Any:
    with FakeServer() as srv:
        srv.route("GET", "/system_stats", {"system": {}})
        files = {"CheckpointLoaderSimple": ("ckpt_name", ["flux-2-klein-4b.safetensors"]),
                 "CLIPLoader": ("clip_name", ["qwen_3_4b.safetensors"]),
                 "VAELoader": ("vae_name", ["flux2-vae.safetensors"])}
        for node in EDIT_NODES:
            field_, options = files.get(node, ("x", []))
            srv.route("GET", f"/object_info/{node}", {node: {"input": {"required": {field_: [options]}}}})
        srv.route("POST", "/upload/image", lambda req: {"name": "uploaded.png", "subfolder": "", "type": "input"})
        srv.route("POST", "/prompt", {"prompt_id": "p-1"})
        shot = {"filename": "k_00001_.png", "subfolder": "s", "type": "output"}
        srv.route("GET", "/history/p-1", {"p-1": {"status": {"status_str": "success", "completed": True},
                                                  "outputs": {"11": {"images": [shot]}}}})
        srv.route("GET", "/view", png_bytes(576, 1024))
        srv.route("POST", "/free", {})
        yield srv


def test_a_keyframe_is_composed_from_reference_images_through_the_chained_template(
        tmp_path: Path, edit_server: FakeServer) -> None:
    s = make_settings(tmp_path, services=f'[services.comfy]\nurl = "{edit_server.url}"\n', models=EDIT_MODELS)
    adapter = ComfyAdapter(ServiceClient(s.service("comfy")), {"klein-comfy": "flux2_klein_edit"},
                           poll_interval_sec=0.01)
    refs = []
    for name in ("hero.png", "scene.png"):
        p = tmp_path / name
        p.write_bytes(png_bytes())
        refs.append(str(p))
    rec, assets = make_rig(s, adapter).run(job(MediaKind.IMAGE_EDIT, "klein-comfy",
                                               {"prompt": "the hero in the alley", "refs": refs}, seed=7))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    [a] = assets
    assert a.kind == "image.edit" and (a.width, a.height) == (576, 1024)
    assert sum(1 for q in edit_server.requests if q.path == "/upload/image") == 2
    graph = next(q.json for q in edit_server.requests if q.path == "/prompt")["prompt"]
    assert graph["3"]["inputs"]["text"] == "the hero in the alley" and graph["6"]["inputs"]["noise_seed"] == 7
    assert graph["4"]["inputs"]["width"] == graph["8"]["inputs"]["width"] == 576          # one param, two nodes
    assert graph["8"]["inputs"]["steps"] == 4 and graph["ref0_scale"]["inputs"]["megapixels"] == 0.5
    assert graph["ref0_ref"]["inputs"]["conditioning"] == ["3", 0]                       # the chain starts at the text
    assert graph["ref1_ref"]["inputs"]["conditioning"] == ["ref0_ref", 0]               # ... and links one per image
    assert graph["5"]["inputs"]["conditioning"] == ["ref1_ref", 0]                       # the guider takes its end
    assert graph["ref1_load"]["inputs"]["image"] == "uploaded.png"


def test_an_edit_without_a_reference_image_is_refused_before_anything_is_queued(
        tmp_path: Path, edit_server: FakeServer) -> None:
    s = make_settings(tmp_path, services=f'[services.comfy]\nurl = "{edit_server.url}"\n', models=EDIT_MODELS)
    adapter = ComfyAdapter(ServiceClient(s.service("comfy")), {"klein-comfy": "flux2_klein_edit"})
    rec, _ = make_rig(s, adapter).run(job(MediaKind.IMAGE_EDIT, "klein-comfy", {"prompt": "x"}))
    assert rec.error_code == ErrorCode.INVALID_INPUT.value
    assert not any(q.path == "/prompt" for q in edit_server.requests)
