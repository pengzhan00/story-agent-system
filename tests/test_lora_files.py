"""A trained LoRA reaches ComfyUI: key naming, the chained loader nodes, and the character's LoRA in its shots."""
from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from storyagent.media import lora_files
from storyagent.media.adapters.comfy import ComfyAdapter
from storyagent.media.contracts import AdapterError
from storyagent.media.ledger import LedgerError
from storyagent.workflow.seed import _character_loras


def write_safetensors(path: Path, names: list[str], payload: bytes) -> None:
    header: dict[str, Any] = {"__metadata__": {"model": "krea-2-raw"}}
    for i, name in enumerate(names):
        header[name] = {"dtype": "F32", "shape": [1], "data_offsets": [i * 4, i * 4 + 4]}
    body = json.dumps(header).encode()
    body += b" " * (-len(body) % 8)
    path.write_bytes(struct.pack("<Q", len(body)) + body + payload)


def read_safetensors(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    (size,) = struct.unpack("<Q", raw[:8])
    return json.loads(raw[8:8 + size]), raw[8 + size:]


def test_the_trainer_key_prefix_becomes_comfyuis_and_the_tensor_bytes_are_untouched(tmp_path: Path) -> None:
    src = tmp_path / "job1.safetensors"
    names = ["transformer.blocks.0.attn.wq.lora_A.weight", "transformer.blocks.0.attn.wq.lora_B.weight"]
    write_safetensors(src, names, b"\x01\x02\x03\x04\x05\x06\x07\x08")
    folder = tmp_path / "loras"
    name = lora_files.export_for_comfy(src, folder)
    header, data = read_safetensors(folder / name)
    assert name == "storyagent_job1.safetensors"
    assert sorted(k for k in header if k != "__metadata__") == [
        "diffusion_model.blocks.0.attn.wq.lora_A.weight", "diffusion_model.blocks.0.attn.wq.lora_B.weight"]
    assert header["__metadata__"] == {"model": "krea-2-raw"} and data == b"\x01\x02\x03\x04\x05\x06\x07\x08"
    assert header["diffusion_model.blocks.0.attn.wq.lora_B.weight"]["data_offsets"] == [4, 8]


def test_an_export_is_reused_and_a_file_already_in_comfy_naming_is_kept_as_it_is(tmp_path: Path) -> None:
    src = tmp_path / "already.safetensors"
    write_safetensors(src, ["diffusion_model.blocks.0.attn.wk.lora_A.weight"], b"\x00\x00\x00\x00")
    folder = tmp_path / "loras"
    name = lora_files.export_for_comfy(src, folder)
    first = (folder / name).stat().st_mtime_ns
    assert lora_files.export_for_comfy(src, folder) == name and (folder / name).stat().st_mtime_ns == first
    assert "diffusion_model.blocks.0.attn.wk.lora_A.weight" in read_safetensors(folder / name)[0]


def test_a_file_that_is_not_safetensors_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "bad.safetensors"
    src.write_bytes(b"not a model")
    with pytest.raises(lora_files.LoraFileError):
        lora_files.export_for_comfy(src, tmp_path / "loras")


def _template() -> dict[str, Any]:
    return {"bindings": {"lora": ["10", "lora_name"], "checkpoint": ["1", "unet_name"]},
            "graph": {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "u"}},
                      "10": {"class_type": "LoraLoaderModelOnly",
                             "inputs": {"model": ["1", 0], "lora_name": "base", "strength_model": 0.4}},
                      "7": {"class_type": "KSampler", "inputs": {"model": ["10", 0]}}}}


def test_loras_are_chained_after_the_templates_own_lora_and_the_sampler_takes_the_end_of_the_chain() -> None:
    tpl = _template()
    graph = tpl["graph"]
    ComfyAdapter._attach_loras(graph, tpl, [("a.safetensors", 1.0), ("b.safetensors", 0.5)])
    assert graph["storyagent_lora_0"]["inputs"] == {"model": ["10", 0], "lora_name": "a.safetensors",
                                                    "strength_model": 1.0}
    assert graph["storyagent_lora_1"]["inputs"]["model"] == ["storyagent_lora_0", 0]
    assert graph["7"]["inputs"]["model"] == ["storyagent_lora_1", 0]
    assert graph["10"]["inputs"]["model"] == ["1", 0]            # the template's own chain is left alone


def test_a_template_without_a_model_node_cannot_take_a_lora() -> None:
    with pytest.raises(AdapterError):
        ComfyAdapter._attach_loras({}, {"bindings": {}}, [("a.safetensors", 1.0)])


class _Ledger:
    def get_asset(self, asset_id: str) -> Any:
        if asset_id != "abc":
            raise LedgerError("unknown asset")
        return SimpleNamespace(metadata={"trigger": "moriel woman"})


def test_a_characters_lora_and_trigger_go_with_every_shot_she_is_in() -> None:
    ctx = SimpleNamespace(settings=SimpleNamespace(workflow={"character_lora_scale": 1.2}))
    chars = {"Lin": {"lora_ref": "asset:abc"}, "Man": {"lora_ref": ""}, "Ghost": {"lora_ref": "asset:gone"}}
    loras, triggers = _character_loras(ctx, _Ledger(), {"characters": ["Lin", "Man", "Ghost"]}, chars)  # type: ignore[arg-type]
    assert loras == [{"path": "asset:abc", "scale": 1.2}, {"path": "asset:gone", "scale": 1.2}]
    assert triggers == ["moriel woman"]                           # an asset that is gone adds no trigger
