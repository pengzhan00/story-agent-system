"""Turn a ComfyUI workflow exported in API format into a template for the ``comfy`` provider.

Pure functions over the JSON, no I/O: ``import_workflow`` reads the node graph, finds the inputs a job has to fill (the
prompt, the seed, the picture size, the sampler settings, the model files, reference pictures / audio, the node whose
output is the result) and returns the template plus everything it found and everything it could not bind, so the person
importing can check it and edit by hand. Nothing is guessed silently: a binding is only made from a node class and an
input name that are known, and what was left out is reported.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from storyagent.domain import MediaKind

# -- what we know about node classes --------------------------------------------------------------------------------
SEED_FIELDS = {"KSampler": "seed", "KSamplerAdvanced": "noise_seed", "SamplerCustom": "noise_seed",
               "RandomNoise": "noise_seed"}
STEPS_FIELDS = {"KSampler": "steps", "KSamplerAdvanced": "steps", "BasicScheduler": "steps", "Flux2Scheduler": "steps",
                "KarrasScheduler": "steps", "SDTurboScheduler": "steps", "AlignYourStepsScheduler": "steps",
                "LTXVScheduler": "steps", "BetaSamplingScheduler": "steps", "PolyexponentialScheduler": "steps"}
CFG_FIELDS = {"KSampler": "cfg", "KSamplerAdvanced": "cfg", "SamplerCustom": "cfg", "CFGGuider": "cfg"}
SAMPLER_FIELDS = {"KSampler": "sampler_name", "KSamplerAdvanced": "sampler_name", "KSamplerSelect": "sampler_name"}
SCHEDULER_FIELDS = {"KSampler": "scheduler", "KSamplerAdvanced": "scheduler", "BasicScheduler": "scheduler"}
# classes that decide what the model is conditioned on: (input name -> role)
CONDITION_INPUTS = {"positive": "positive", "negative": "negative", "conditioning": "positive"}
# a negative path stops at these: they derive the negative from the positive text, so there is no text of its own
ZERO_NODES = {"ConditioningZeroOut"}
TEXT_FIELDS = ("text", "prompt", "text_g", "text_l", "clip_l", "t5xxl", "tags")
LATENT_NAME = re.compile(r"Latent|Scheduler|Resolution", re.I)
LOADERS = {  # class -> [(input, binding name)]
    "CheckpointLoaderSimple": [("ckpt_name", "checkpoint")],
    "UNETLoader": [("unet_name", "checkpoint")],
    "CLIPLoader": [("clip_name", "text_encoder")],
    "DualCLIPLoader": [("clip_name1", "text_encoder"), ("clip_name2", "text_encoder_2")],
    "VAELoader": [("vae_name", "vae")],
    "LoraLoader": [("lora_name", "lora"), ("strength_model", "lora_strength")],
    "LoraLoaderModelOnly": [("lora_name", "lora"), ("strength_model", "lora_strength")],
}
# output node class -> (history key, preference: higher wins)
OUTPUTS: dict[str, tuple[str, int]] = {
    "SaveImage": ("images", 2), "PreviewImage": ("images", 1), "SaveAnimatedWEBP": ("images", 2),
    "SaveAnimatedPNG": ("images", 2), "SaveWEBM": ("images", 2), "SaveVideo": ("images", 3),
    "VHS_VideoCombine": ("gifs", 3), "SaveAudio": ("audio", 3), "SaveAudioMP3": ("audio", 3),
    "SaveAudioOpus": ("audio", 3), "PreviewAudio": ("audio", 1),
}
DOMAIN_KEYS = {"image": ("images",), "video": ("images", "gifs", "videos"), "audio": ("audio",)}
VIDEO_OUTPUT_ORDER = ["gifs", "videos", "images"]


class ImportFailed(Exception):
    """The file cannot be used at all. ``code`` is an i18n key suffix (``connect.error.<code>``)."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class Detected:
    name: str                              # the binding name a job fills (prompt, seed, width, ...)
    targets: tuple[tuple[str, str], ...]   # (node id, input) pairs
    classes: tuple[str, ...]               # node class of each target
    value: Any = None                      # what the workflow had there (becomes the model's default)


@dataclass(frozen=True)
class Warning_:
    code: str                              # i18n key suffix: connect.warn.<code>
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ImportResult:
    template: dict[str, Any]
    detected: list[Detected]
    warnings: list[Warning_]
    defaults: dict[str, Any]               # binding name -> the workflow's own value, for [models.X.params]

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "detected": [{"name": d.name, "targets": [list(t) for t in d.targets], "classes": list(d.classes),
                          "value": d.value} for d in self.detected],
            "warnings": [{"code": w.code, "params": dict(w.params)} for w in self.warnings],
            "defaults": self.defaults,
        }


# -- reading the graph ------------------------------------------------------------------------------------------------
def _graph_of(data: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(data, Mapping) or not data:
        raise ImportFailed("not_a_graph")
    if "nodes" in data and "links" in data:
        raise ImportFailed("not_api_format")                     # the editor's own save, not "Save (API Format)"
    wrapped = data.get("prompt")
    graph: Mapping[str, Any] = wrapped if isinstance(wrapped, Mapping) else data
    out: dict[str, dict[str, Any]] = {}
    for node_id, node in graph.items():
        if not isinstance(node, Mapping) or "class_type" not in node:
            raise ImportFailed("not_a_graph")
        out[str(node_id)] = {"class_type": str(node["class_type"]),
                             "inputs": dict(node.get("inputs") or {}), **({"_meta": node["_meta"]} if "_meta" in node
                                                                         else {})}
    if not out:
        raise ImportFailed("not_a_graph")
    return out


def _is_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[1], int) and not isinstance(value[0], bool)


def _sorted_ids(graph: Mapping[str, Any]) -> list[str]:
    return sorted(graph, key=lambda i: (0, int(i)) if i.isdigit() else (1, i))


def _text_fields(node: Mapping[str, Any]) -> list[str]:
    inputs = node["inputs"]
    return [f for f in TEXT_FIELDS if isinstance(inputs.get(f), str)]


def _is_text_encoder(node: Mapping[str, Any]) -> bool:
    return "TextEncode" in node["class_type"] and bool(_text_fields(node))


def _conditioning_roles(graph: Mapping[str, dict[str, Any]]) -> dict[str, str]:
    """Text-encode node id -> "positive" | "negative", by following the conditioning links back from the samplers /
    guiders. A text that feeds both is positive; a negative that is derived (ConditioningZeroOut) has no text node."""
    roots: dict[str, list[str]] = {"positive": [], "negative": []}
    for node in graph.values():
        for name, role in CONDITION_INPUTS.items():
            link = node["inputs"].get(name)
            if _is_link(link) and (name != "conditioning" or "Guider" in node["class_type"]):
                roots[role].append(str(link[0]))
    found: dict[str, str] = {}
    for role in ("positive", "negative"):          # positive first: it wins a shared text node
        seen: set[str] = set()
        stack = list(roots[role])
        while stack:
            nid = stack.pop()
            reached = graph.get(nid)
            if reached is None or nid in seen:
                continue
            seen.add(nid)
            if reached["class_type"] in ZERO_NODES and role == "negative":
                continue
            if _is_text_encoder(reached):
                found.setdefault(nid, role)
                continue
            for value in reached["inputs"].values():
                if _is_link(value):
                    stack.append(str(value[0]))
    return found


def import_workflow(data: Any, kind: str) -> ImportResult:
    """``data``: the parsed JSON file. ``kind``: the MediaKind value the model will serve (picks the output node)."""
    graph = _graph_of(data)
    try:
        mkind = MediaKind(kind)
    except ValueError:
        raise ImportFailed("unknown_kind", kind=kind) from None
    warnings: list[Warning_] = []
    bindings: dict[str, list[tuple[str, str]]] = {}
    classes: dict[str, list[str]] = {}
    values: dict[str, Any] = {}

    def bind(name: str, node_id: str, input_name: str) -> None:
        bindings.setdefault(name, []).append((node_id, input_name))
        classes.setdefault(name, []).append(graph[node_id]["class_type"])
        current = graph[node_id]["inputs"].get(input_name)
        if name not in values and not _is_link(current) and current is not None:
            values[name] = current

    ids = _sorted_ids(graph)

    # prompts
    roles = _conditioning_roles(graph)
    encoders = [i for i in ids if _is_text_encoder(graph[i])]
    for nid in ids:
        node = graph[nid]
        if node["class_type"] == "TextEncodeAceStepAudio":
            if isinstance(node["inputs"].get("tags"), str):
                bind("prompt", nid, "tags")
            if isinstance(node["inputs"].get("lyrics"), str):
                bind("text", nid, "lyrics")
    if not roles and encoders and "prompt" not in bindings:           # no sampler to trace from: go by order
        roles = {encoders[0]: "positive", **({encoders[1]: "negative"} if len(encoders) > 1 else {})}
        warnings.append(Warning_("prompt_by_order"))
    for nid in encoders:
        role = roles.get(nid)
        if role is None or graph[nid]["class_type"] == "TextEncodeAceStepAudio":
            continue
        name = "prompt" if role == "positive" else "negative_prompt"
        for f in _text_fields(graph[nid]):
            bind(name, nid, f)
    if "prompt" not in bindings:
        warnings.append(Warning_("no_prompt"))

    # sampler settings, model files
    for nid in ids:
        node = graph[nid]
        cls, inputs = node["class_type"], node["inputs"]
        for table, name in ((SEED_FIELDS, "seed"), (STEPS_FIELDS, "steps"), (CFG_FIELDS, "cfg"),
                            (SAMPLER_FIELDS, "sampler_name"), (SCHEDULER_FIELDS, "scheduler")):
            wanted = table.get(cls)
            if wanted and wanted in inputs and not _is_link(inputs[wanted]):
                bind(name, nid, wanted)
        for f, name in LOADERS.get(cls, []):
            if f in inputs and not _is_link(inputs[f]) and (name not in bindings or name == "lora_strength"):
                bind(name, nid, f)
        # sizes, length, frame rate, audio length
        if LATENT_NAME.search(cls):
            for f, name in (("width", "width"), ("height", "height"), ("length", "frames"), ("frames", "frames"),
                            ("seconds", "duration"), ("frame_rate", "fps")):
                if f in inputs and isinstance(inputs[f], (int, float)) and not isinstance(inputs[f], bool):
                    bind(name, nid, f)
        elif cls in ("LTXVConditioning", "CreateVideo") and isinstance(inputs.get("frame_rate"), (int, float)):
            bind("fps", nid, "frame_rate")
        elif cls in ("LTXVConditioning", "CreateVideo") and isinstance(inputs.get("fps"), (int, float)):
            bind("fps", nid, "fps")
    if "seed" not in bindings:
        warnings.append(Warning_("no_seed"))
    if "checkpoint" not in bindings:
        warnings.append(Warning_("no_model_loader"))
    for name in ("checkpoint", "text_encoder", "vae"):
        if len(bindings.get(name, [])) > 1:
            warnings.append(Warning_("several_loaders", {"name": name}))
            bindings[name] = bindings[name][:1]
            classes[name] = classes[name][:1]

    # reference pictures / audio
    pictures = [i for i in ids if graph[i]["class_type"] in ("LoadImage", "LoadImageMask")
                and isinstance(graph[i]["inputs"].get("image"), str)]
    for n, nid in enumerate(pictures):
        bind("ref_image" if n == 0 else f"ref_image_{n + 1}", nid, "image")
    sounds = [i for i in ids if graph[i]["class_type"] == "LoadAudio"
              and isinstance(graph[i]["inputs"].get("audio"), str)]
    for n, nid in enumerate(sounds):
        bind("ref_audio" if n == 0 else f"ref_audio_{n + 1}", nid, "audio")
    videos = [(nid, "file" if graph[nid]["class_type"] == "LoadVideo" else "video")
              for nid in ids if graph[nid]["class_type"] in ("LoadVideo", "VHS_LoadVideo")]
    for n, (nid, input_name) in enumerate(videos):
        if isinstance(graph[nid]["inputs"].get(input_name), str):
            bind("ref_video" if n == 0 else f"ref_video_{n + 1}", nid, input_name)
    if mkind is MediaKind.IMAGE_EDIT and not pictures:
        warnings.append(Warning_("edit_without_image"))
    if mkind is MediaKind.AUDIO_CONVERT and not sounds:
        warnings.append(Warning_("convert_without_audio"))

    # the node whose output is the result
    domain = mkind.value.split(".")[0]
    candidates = [(i, *OUTPUTS[graph[i]["class_type"]]) for i in ids if graph[i]["class_type"] in OUTPUTS]
    fitting = [c for c in candidates if c[1] in DOMAIN_KEYS.get(domain, ())] or candidates
    output_node, output_type = "", "images"
    if fitting:
        best = max(fitting, key=lambda c: (c[2], int(c[0]) if c[0].isdigit() else 0))
        output_node, output_type = best[0], best[1]
        if best[1] not in DOMAIN_KEYS.get(domain, ()):
            warnings.append(Warning_("output_kind_mismatch", {"class": graph[best[0]]["class_type"], "kind": kind}))
    else:
        warnings.append(Warning_("no_output"))
    out_types: Any = output_type
    if domain == "video":
        out_types = [output_type, *[k for k in VIDEO_OUTPUT_ORDER if k != output_type]]

    template: dict[str, Any] = {
        "bindings": {k: ([list(t) for t in v] if len(v) > 1 else list(v[0])) for k, v in bindings.items()},
        "graph": {i: {"class_type": n["class_type"], "inputs": n["inputs"]} for i, n in graph.items()},
        "output": output_node,
        "output_type": out_types,
    }
    detected = [Detected(k, tuple(v), tuple(classes[k]), values.get(k)) for k, v in bindings.items()]
    defaults = {k: v for k, v in values.items() if k not in ("prompt", "negative_prompt", "seed", "text")
                and not k.startswith("ref_") and k not in ("checkpoint", "text_encoder", "text_encoder_2", "vae",
                                                           "lora")}
    return ImportResult(template, detected, warnings, defaults)


# -- the models.toml entry ---------------------------------------------------------------------------------------------
def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


def model_entry(ref: str, kind: str, workflow: str, result: ImportResult, *, family: str = "",
                heavy: bool = False, timeout_sec: int = 1800) -> str:
    """The text to add to ``<config_dir>/models.toml`` for the imported workflow (licence fields to be filled in)."""
    name = _toml_value(ref)
    lines = [
        f"[models.{name}]",
        f"kind = {_toml_value(kind)}",
        'provider = "comfy"',
        f"family = {_toml_value(family or ref)}",
        f"heavy = {_toml_value(heavy)}",
        'license_tag = ""                   # fill in after reading the licence of the model files this workflow loads',
        "commercial_ok = false             # not verified: keep false until you have",
        'prompt_profile = "natural_en"',
        f"timeout_sec = {timeout_sec}",
        f"workflow = {_toml_value(workflow)}",
    ]
    if result.defaults:
        lines += ["", f"[models.{name}.params]"]
        lines += [f"{k} = {_toml_value(v)}" for k, v in result.defaults.items()]
    return "\n".join(lines) + "\n"
