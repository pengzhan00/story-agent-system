from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from storyagent.connect.comfy_import import ImportFailed, import_workflow, model_entry
from storyagent.domain import JobStatus, MediaKind
from storyagent.media.adapters._http import ServiceClient
from storyagent.media.adapters.comfy import WORKFLOWS, ComfyAdapter

from .adapter_kit import job, make_rig, make_settings
from .fakes.http_server import FakeServer
from .test_adapter_mflux import png_bytes


def sd_graph() -> dict[str, Any]:
    """A plain text-to-image workflow as ComfyUI's "Save (API Format)" writes it."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd15.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {
            "seed": 1, "steps": 20, "cfg": 7.5, "sampler_name": "euler", "scheduler": "normal", "denoise": 1,
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "x"}},
    }


def video_graph() -> dict[str, Any]:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "ltx.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5.safetensors", "type": "ltxv"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "walk", "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad", "clip": ["2", 0]}},
        "5": {"class_type": "LoadImage", "inputs": {"image": "start.png"}},
        "6": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": 704, "height": 480, "length": 97,
                                                              "batch_size": 1}},
        "7": {"class_type": "LTXVConditioning", "inputs": {"positive": ["3", 0], "negative": ["4", 0],
                                                          "frame_rate": 24}},
        "8": {"class_type": "KSampler", "inputs": {
            "seed": 5, "steps": 30, "cfg": 3.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1,
            "model": ["1", 0], "positive": ["7", 0], "negative": ["7", 1], "latent_image": ["6", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["9", 0], "frame_rate": 24}},
    }


def binding(result: Any, name: str) -> Any:
    return result.template["bindings"].get(name)


def test_a_plain_image_workflow_is_bound_input_by_input() -> None:
    r = import_workflow(sd_graph(), "image.generate")
    b = r.template["bindings"]
    assert b["prompt"] == ["2", "text"] and b["negative_prompt"] == ["3", "text"]      # traced from the sampler
    assert b["seed"] == ["5", "seed"] and b["steps"] == ["5", "steps"] and b["cfg"] == ["5", "cfg"]
    assert b["sampler_name"] == ["5", "sampler_name"] and b["scheduler"] == ["5", "scheduler"]
    assert b["width"] == ["4", "width"] and b["height"] == ["4", "height"]
    assert b["checkpoint"] == ["1", "ckpt_name"]
    assert (r.template["output"], r.template["output_type"]) == ("7", "images")
    assert r.defaults == {"steps": 20, "cfg": 7.5, "sampler_name": "euler", "scheduler": "normal", "width": 512,
                          "height": 768}
    assert r.warnings == []


def test_the_negative_is_found_even_when_the_node_ids_are_the_other_way_round() -> None:
    g = sd_graph()
    g["2"]["inputs"]["text"], g["3"]["inputs"]["text"] = "blurry", "a cat"
    g["5"]["inputs"]["positive"], g["5"]["inputs"]["negative"] = ["3", 0], ["2", 0]
    b = import_workflow(g, "image.generate").template["bindings"]
    assert b["prompt"] == ["3", "text"] and b["negative_prompt"] == ["2", "text"]


def test_a_video_workflow_gets_its_picture_slot_length_rate_and_output() -> None:
    r = import_workflow(video_graph(), "video.generate")
    b = r.template["bindings"]
    assert b["ref_image"] == ["5", "image"] and b["frames"] == ["6", "length"]
    assert b["fps"] == ["7", "frame_rate"] and b["checkpoint"] == ["1", "unet_name"]
    assert b["text_encoder"] == ["2", "clip_name"]
    assert r.template["output"] == "10" and r.template["output_type"][0] == "gifs"      # VHS writes under "gifs"
    assert {w.code for w in r.warnings} == set()


def test_the_packaged_templates_import_back_to_what_they_were_written_as() -> None:
    audio = json.loads((WORKFLOWS / "stable_audio_3.json").read_text())
    r = import_workflow(audio["graph"], "audio.sfx")
    for name in ("prompt", "negative_prompt", "seed", "steps", "cfg", "sampler_name", "scheduler", "checkpoint",
                 "text_encoder"):
        assert r.template["bindings"][name] == audio["bindings"][name], name
    assert r.template["bindings"]["duration"] == audio["bindings"]["seconds"]
    assert r.template["output"] == audio["output"] and r.template["output_type"] == "audio"
    edit = json.loads((WORKFLOWS / "flux2_klein_edit.json").read_text())
    e = import_workflow(edit["graph"], "image.edit")
    assert e.template["bindings"]["prompt"] == edit["bindings"]["prompt"]
    assert e.template["bindings"]["width"] == edit["bindings"]["width"]               # one name, two nodes
    assert e.template["bindings"]["seed"] == edit["bindings"]["seed"]
    assert "negative_prompt" not in e.template["bindings"]                             # the guider has none
    assert "edit_without_image" in {w.code for w in e.warnings}                       # no LoadImage in the graph


def test_what_cannot_be_bound_is_reported() -> None:
    g = sd_graph()
    for nid in ("2", "3"):
        g[nid]["class_type"] = "SomeCustomEncode"
    g.pop("7")
    codes = {w.code for w in import_workflow(g, "image.generate").warnings}
    assert {"no_prompt", "no_output"} <= codes


@pytest.mark.parametrize("data, code", [
    ({"nodes": [], "links": []}, "not_api_format"),
    ({}, "not_a_graph"),
    ({"1": {"inputs": {}}}, "not_a_graph"),
    ([], "not_a_graph"),
])
def test_files_that_are_not_an_api_export_are_refused_with_a_reason(data: Any, code: str) -> None:
    with pytest.raises(ImportFailed) as exc:
        import_workflow(data, "image.generate")
    assert exc.value.code == code


def test_the_prompt_wrapper_and_unknown_kinds() -> None:
    assert import_workflow({"prompt": sd_graph()}, "image.generate").template["output"] == "7"
    with pytest.raises(ImportFailed) as exc:
        import_workflow(sd_graph(), "image.paint")
    assert exc.value.code == "unknown_kind"


def test_the_model_entry_is_valid_toml_with_the_workflow_values_as_defaults() -> None:
    r = import_workflow(sd_graph(), "image.generate")
    entry = tomllib.loads(model_entry("my-sd", "image.generate", "my_sd", r, family="sd15"))["models"]["my-sd"]
    assert entry["provider"] == "comfy" and entry["workflow"] == "my_sd" and entry["family"] == "sd15"
    assert entry["commercial_ok"] is False and entry["params"]["steps"] == 20 and entry["params"]["width"] == 512


def test_an_imported_workflow_runs_from_the_operators_own_folder(tmp_path: Path) -> None:
    models = ('[models."my-sd"]\nkind = "image.generate"\nprovider = "comfy"\nworkflow = "my_sd"\ntimeout_sec = 20\n'
              '[models."my-sd".params]\nsteps = 20\nwidth = 512\nheight = 768\n')
    with FakeServer() as srv:
        srv.route("GET", "/system_stats", {"system": {}})
        files = {"CheckpointLoaderSimple": ("ckpt_name", ["sd15.safetensors"])}
        for node in sd_graph().values():
            cls = node["class_type"]
            field_, options = files.get(cls, ("x", []))
            srv.route("GET", f"/object_info/{cls}", {cls: {"input": {"required": {field_: [options]}}}})
        srv.route("POST", "/prompt", {"prompt_id": "p-1"})
        srv.route("GET", "/history/p-1", {"p-1": {"status": {"status_str": "success", "completed": True},
                                                  "outputs": {"7": {"images": [{"filename": "o.png", "subfolder": "",
                                                                                 "type": "output"}]}}}})
        srv.route("GET", "/view", png_bytes(512, 768))
        srv.route("POST", "/free", {})
        s = make_settings(tmp_path, services=f'[services.comfy]\nurl = "{srv.url}"\n', models=models)
        user_dir = s.config_dir / "workflows"
        user_dir.mkdir()
        (user_dir / "my_sd.json").write_text(json.dumps(import_workflow(sd_graph(), "image.generate").template))
        adapter = ComfyAdapter(ServiceClient(s.service("comfy")), {"my-sd": "my_sd"}, poll_interval_sec=0.01,
                               user_dir=user_dir)
        rec, assets = make_rig(s, adapter).run(job(MediaKind.IMAGE_GENERATE, "my-sd",
                                                   {"prompt": "a dog", "negative_prompt": "lowres"}, seed=9))
        assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
        graph = next(q.json for q in srv.requests if q.path == "/prompt")["prompt"]
        assert graph["2"]["inputs"]["text"] == "a dog" and graph["3"]["inputs"]["text"] == "lowres"
        assert graph["5"]["inputs"]["seed"] == 9
        assert graph["4"]["inputs"]["width"] == rec.params["width"]       # whatever the job resolved lands in the node
        assert graph["5"]["inputs"]["steps"] == 20
        [a] = assets
        assert a.kind == "image.generate" and (a.width, a.height) == (512, 768)
