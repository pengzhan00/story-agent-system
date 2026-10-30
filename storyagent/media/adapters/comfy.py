"""ComfyUI adapter: runs an API-format workflow template (``media/workflows/<name>.json``) for the model.

A template holds the node graph plus a ``bindings`` table (parameter name -> [node id, input name]), so nothing is
guessed from the graph; the adapter only writes the job's values into those inputs (a binding may name several
inputs: ``[[node, input], ...]``). Templates come from ``<config_dir>/workflows/<name>.json`` (yours, checked first) or
the packaged ``media/workflows``; ``storyagent tool import comfy`` writes one from a workflow you exported in ComfyUI's
API format. Any kind in ``KINDS`` can use it: pictures, sound effects, music, speech, video.

Protocol: ``POST /prompt`` -> poll ``GET /history/<id>`` -> ``GET /view`` for the first output of the template's
``output_type`` (a name or a list of names among ``images`` / ``audio`` / ``videos`` / ``gifs``; the others are tried
after it) of the template's ``output`` node (no ``output`` = any node).

Reference files (``inputs.refs``): uploaded to ComfyUI. Images feed the template's ``references`` block when it has
one - for each a LoadImage -> ImageScaleToTotalPixels -> VAEEncode -> ReferenceLatent chain is built
(``conditioning`` = text-encode node, ``vae`` = [node, slot], ``consumer`` = [node, input] that takes the chain's end)
- and/or fill the bindings ``ref_image``, ``ref_image_2``, ... (LoadImage nodes) and ``ref_audio`` (a LoadAudio node)
with the uploaded names. A template that binds ``ref_image`` refuses a job without an image.

Inputs:  ``prompt`` (text), optional ``negative_prompt``, ``refs``
Params:  duration (seconds), steps, cfg, sampler_name, scheduler, checkpoint, text_encoder, vae, width, height,
         ref_megapixels (size the references are scaled to; smaller = faster)
The template name is ``workflow`` of the model entry; the job seed becomes the sampler seed.
"""

from __future__ import annotations

import copy
import json
import random
import re
import urllib.parse
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

from ..contracts import (
    AdapterBase,
    AdapterError,
    ArtifactSpec,
    Capabilities,
    HealthReport,
    HealthState,
    JobContext,
    JobRecord,
    Progress,
    ValidationIssue,
)
from ._http import ServiceClient

KINDS = (
    MediaKind.IMAGE_GENERATE,
    MediaKind.IMAGE_EDIT,
    MediaKind.VIDEO_GENERATE,
    MediaKind.VIDEO_AV,
    MediaKind.VIDEO_ENHANCE,
    MediaKind.AUDIO_MUSIC,
    MediaKind.AUDIO_SFX,
    MediaKind.AUDIO_TTS,
    MediaKind.AUDIO_CONVERT,
    MediaKind.AUDIO_ENHANCE,
    MediaKind.VIDEO_LIPSYNC,
    MediaKind.VIDEO_AVATAR,
    MediaKind.AUDIO_SEPARATE,
)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".gif"}
OUTPUT_KEYS = ("images", "audio", "videos", "gifs")
WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"
_RENAME = {"duration": "seconds"}
SCHEMA: dict[str, dict[str, Any]] = {
    "duration": {"type": "float", "min": 0.5, "max": 47},
    "steps": {"type": "int", "min": 1, "max": 100},
    "cfg": {"type": "float", "min": 0, "max": 30},
    "sampler_name": {"type": "str"},
    "scheduler": {"type": "str"},
    "checkpoint": {"type": "str"},
    "text_encoder": {"type": "str"},
    "vae": {"type": "str"},
    "width": {"type": "int", "min": 64, "max": 4096},
    "height": {"type": "int", "min": 64, "max": 4096},
    "ref_megapixels": {"type": "float", "min": 0.1, "max": 8},
    "frames": {"type": "int", "min": 1, "max": 4096},
    "fps": {"type": "float", "min": 1, "max": 120},
    "batch": {"type": "int", "min": 1, "max": 64},
    "denoise": {"type": "float", "min": 0, "max": 1},
}
_FILE_NODES = {
    "CheckpointLoaderSimple": "ckpt_name",
    "CLIPLoader": "clip_name",
    "VAELoader": "vae_name",
    "UNETLoader": "unet_name",
    "LoraLoader": "lora_name",
    "CLIPVisionLoader": "clip_name",
}
_PLACEHOLDER = "reference-not-uploaded-yet.png"


def load_template(name: str, user_dir: Path | None = None) -> dict[str, Any]:
    """The template ``name``: the operator's ``user_dir`` first, then the packaged ones."""
    candidates = [d / f"{name}.json" for d in (user_dir, WORKFLOWS) if d is not None and name]
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        raise AdapterError(ErrorCode.MISSING_TOOL, f"workflow template {name!r} not found", tool=name)
    try:
        data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    except ValueError as exc:
        raise AdapterError(
            ErrorCode.INVALID_INPUT, f"workflow template {name!r} is not valid JSON", detail=str(exc)[:200]
        ) from exc
    if "outputs" in data:
        outputs = data["outputs"]
        if not isinstance(outputs, dict) or "main" not in outputs:
            raise AdapterError(ErrorCode.INVALID_INPUT, "Declared outputs require a main port")
        for port, spec in outputs.items():
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", port)
                or not isinstance(spec, dict)
                or "node" not in spec
                or spec.get("type", "images") not in OUTPUT_KEYS
                or not isinstance(spec.get("index", 0), int)
                or spec.get("index", 0) < 0
                or (spec.get("kind") and spec["kind"] not in {k.value for k in MediaKind})
            ):
                raise AdapterError(ErrorCode.INVALID_INPUT, "Invalid declared output port")
    return data


class ComfyAdapter(AdapterBase):
    provider = "comfy"

    def __init__(
        self,
        client: ServiceClient,
        workflows: dict[str, str] | None = None,
        poll_interval_sec: float = 2.0,
        user_dir: Path | None = None,
    ) -> None:
        self.client = client
        self.poll_interval_sec = poll_interval_sec
        self.user_dir = user_dir
        self._workflows = workflows or {}  # model_ref -> template name

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=KINDS,
            heavy=False,
            supports_cancel=False,
            supports_reattach=True,
            param_schema=SCHEMA,
            poll_interval_sec=self.poll_interval_sec,
        )

    def template(self, job: JobRecord) -> dict[str, Any]:
        return load_template(self._workflows.get(job.model_ref) or "", self.user_dir)

    def health(self) -> HealthReport:
        try:
            self.client.json("GET", "/system_stats", timeout=5)
        except AdapterError:
            return HealthReport(HealthState.NOT_RUNNING, self.client.base)
        return HealthReport(HealthState.OK)

    def _available(self, node_class: str, field: str) -> list[str] | None:
        """File names ComfyUI offers for a loader input (``None`` = the node class is unknown)."""
        info = self.client.request("GET", f"/object_info/{node_class}", timeout=10)
        data = info.json() if info.content else {}
        node = data.get(node_class)
        if not node:
            return None
        spec = (node.get("input", {}).get("required", {}) | node.get("input", {}).get("optional", {})).get(field)
        return [str(x) for x in spec[0]] if spec and isinstance(spec[0], list) else []

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        try:
            tpl = self.template(job)
        except AdapterError as exc:
            return [ValidationIssue(exc.code, exc.message, exc.params)]
        bindings: dict[str, Any] = tpl.get("bindings", {})
        if "prompt" in bindings and not str(job.inputs.get("prompt", "")).strip():
            return [ValidationIssue(ErrorCode.INVALID_INPUT, "needs a prompt", {"detail": "prompt empty"})]
        issues: list[ValidationIssue] = []
        images, audios, videos = self._refs(job, ctx), self._audio_refs(job, ctx), self._video_refs(job, ctx)
        for ref in (*images, *audios, *videos):
            if not ref.is_file():
                issues.append(
                    ValidationIssue(ErrorCode.INVALID_INPUT, f"reference file not found: {ref}", {"detail": str(ref)})
                )
        if (job.kind is MediaKind.IMAGE_EDIT or "ref_image" in bindings) and not images:
            issues.append(
                ValidationIssue(
                    ErrorCode.INVALID_INPUT,
                    f"{job.kind.value} needs at least one image in refs",
                    {"detail": "refs empty"},
                )
            )
        if "ref_audio" in bindings and not audios:
            issues.append(
                ValidationIssue(
                    ErrorCode.INVALID_INPUT, "this workflow needs an audio file in refs", {"detail": "audio refs empty"}
                )
            )
        if "ref_video" in bindings and not videos:
            issues.append(
                ValidationIssue(
                    ErrorCode.INVALID_INPUT, "this workflow needs a video reference", {"detail": "video refs empty"}
                )
            )
        for prefix, files in (("ref_image", images), ("ref_audio", audios), ("ref_video", videos)):
            for key in bindings:
                if (
                    key.startswith(prefix + "_")
                    and key[len(prefix) + 1 :].isdigit()
                    and int(key[len(prefix) + 1 :]) > len(files)
                ):
                    issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, f"Missing workflow input {key}"))
        for dim in ("width", "height"):
            if dim in job.params and int(job.params[dim]) % 16:
                issues.append(
                    ValidationIssue(
                        ErrorCode.INVALID_PARAMS,
                        f"{dim} must be a multiple of 16",
                        {"detail": f"{dim}={job.params[dim]}"},
                    )
                )
        if issues:
            return issues
        try:
            graph = self._graph(
                job, tpl, [_PLACEHOLDER] * len(images), [_PLACEHOLDER] * len(audios), [_PLACEHOLDER] * len(videos)
            )
            for node in graph.values():
                cls = node["class_type"]
                field = _FILE_NODES.get(cls, "")
                avail = self._available(cls, field)
                have = node["inputs"].get(field) if field else None
                if avail is None:
                    issues.append(ValidationIssue(ErrorCode.MISSING_TOOL, f"ComfyUI has no node {cls}", {"tool": cls}))
                elif isinstance(have, str) and have not in avail:
                    issues.append(
                        ValidationIssue(ErrorCode.MISSING_MODEL, f"{have} is not in ComfyUI", {"model": have})
                    )
            return issues
        except AdapterError as exc:
            return [ValidationIssue(exc.code, exc.message, exc.params)]

    @staticmethod
    def _files(job: JobRecord, ctx: JobContext, extensions: set[str]) -> list[Path]:
        out: list[Path] = []
        candidates = list(job.inputs.get("refs", []))
        candidates += [
            job.inputs[k]
            for k in ("image", "audio", "video", "source_audio", "parent_audio", "source_video")
            if job.inputs.get(k)
        ]
        for raw in candidates:
            if isinstance(raw, dict):
                raw = raw.get("path")
            if isinstance(raw, str) and Path(raw).suffix.lower() in extensions:
                p = Path(raw).expanduser()
                path = p if p.is_absolute() else ctx.settings.home / p
                if path not in out:
                    out.append(path)
        return out

    def _refs(self, job: JobRecord, ctx: JobContext) -> list[Path]:
        return self._files(job, ctx, IMAGE_EXT)

    def _audio_refs(self, job: JobRecord, ctx: JobContext) -> list[Path]:
        return self._files(job, ctx, AUDIO_EXT)

    def _video_refs(self, job: JobRecord, ctx: JobContext) -> list[Path]:
        return self._files(job, ctx, VIDEO_EXT)

    @staticmethod
    def _chain_references(graph: dict[str, Any], block: dict[str, Any], uploaded: list[str], megapixels: float) -> None:
        """Append one LoadImage -> scale -> VAEEncode -> ReferenceLatent link per reference to the conditioning."""
        prev: list[Any] = [block["conditioning"], 0]
        for i, name in enumerate(uploaded):
            graph[f"ref{i}_load"] = {"class_type": "LoadImage", "inputs": {"image": name}}
            graph[f"ref{i}_scale"] = {
                "class_type": "ImageScaleToTotalPixels",
                "inputs": {
                    "image": [f"ref{i}_load", 0],
                    "upscale_method": "lanczos",
                    "megapixels": megapixels,
                    "resolution_steps": 16,
                },
            }
            graph[f"ref{i}_vae"] = {
                "class_type": "VAEEncode",
                "inputs": {"pixels": [f"ref{i}_scale", 0], "vae": list(block["vae"])},
            }
            graph[f"ref{i}_ref"] = {
                "class_type": "ReferenceLatent",
                "inputs": {"conditioning": prev, "latent": [f"ref{i}_vae", 0]},
            }
            prev = [f"ref{i}_ref", 0]
        node, field = block["consumer"]
        graph[node]["inputs"][field] = prev

    def _graph(
        self,
        job: JobRecord,
        tpl: dict[str, Any],
        uploaded: list[str] | None = None,
        uploaded_audio: list[str] | None = None,
        uploaded_video: list[str] | None = None,
    ) -> dict[str, Any]:
        graph = copy.deepcopy(tpl["graph"])
        bindings: dict[str, list[str]] = tpl["bindings"]
        values: dict[str, Any] = {
            "prompt": str(job.inputs.get("prompt", "")),
            "seed": job.seed if job.seed is not None else random.randint(0, 2**32 - 1),
        }
        if str(job.inputs.get("negative_prompt", "")).strip():  # else the template's own default stays
            values["negative_prompt"] = str(job.inputs["negative_prompt"])
        if str(job.inputs.get("text", "")).strip():  # lyrics / the words to speak
            values["text"] = str(job.inputs["text"])
        for i, name in enumerate(uploaded or []):
            values["ref_image" if i == 0 else f"ref_image_{i + 1}"] = name
        for prefix, names in (("ref_audio", uploaded_audio), ("ref_video", uploaded_video)):
            for i, name in enumerate(names or []):
                values[prefix if i == 0 else f"{prefix}_{i + 1}"] = name
        for key, value in job.params.items():
            values[_RENAME.get(key, key)] = value
        for name, value in values.items():
            if name in bindings:
                targets = bindings[name]
                for node, field in targets if isinstance(targets[0], list) else [targets]:
                    graph[node]["inputs"][field] = value
        if uploaded and tpl.get("references"):
            self._chain_references(graph, tpl["references"], uploaded, float(values.get("ref_megapixels", 1.0)))
        return graph

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        uploaded = [self._upload(job, i, path) for i, path in enumerate(self._refs(job, ctx))]
        uploaded_audio = [self._upload(job, i, path, "a") for i, path in enumerate(self._audio_refs(job, ctx))]
        uploaded_video = [self._upload(job, i, path, "v") for i, path in enumerate(self._video_refs(job, ctx))]
        body = self.client.json(
            "POST",
            "/prompt",
            json={"prompt": self._graph(job, self.template(job), uploaded, uploaded_audio, uploaded_video)},
        )
        prompt_id = (body or {}).get("prompt_id")
        if not prompt_id:
            raise AdapterError(
                ErrorCode.SERVICE_REJECTED,
                f"no prompt_id in {str(body)[:200]}",
                service=self.client.service.name,
                detail="no prompt_id",
            )
        return str(prompt_id)

    def _upload(self, job: JobRecord, index: int, path: Path, tag: str = "") -> str:
        """Put a reference file into ComfyUI's input folder (a name unique to the job) and return its name."""
        name = f"storyagent_{job.id[:12]}_{tag}{index}{path.suffix.lower()}"
        with path.open("rb") as fh:
            body = self.client.json(
                "POST",
                "/upload/image",
                files={"image": (name, fh)},
                data={"type": "input", "overwrite": "true"},
                timeout=60,
            )
        return str((body or {}).get("name") or name)

    def _history(self, external_id: str) -> dict[str, Any] | None:
        body = self.client.json("GET", f"/history/{external_id}", timeout=15)
        entry = (body or {}).get(external_id)
        return entry if isinstance(entry, dict) else None

    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress:
        entry = self._history(external_id)
        if entry is None:
            return Progress("running", 0.1, "queued")
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            msgs = [m for m in status.get("messages", []) if isinstance(m, list) and m and m[0] == "execution_error"]
            err = str(msgs[-1][1].get("exception_message", "")) if msgs else "workflow failed"
            return Progress("failed", error=AdapterError(ErrorCode.WORKFLOW_FAILED, err, detail=err[:300]))
        if status.get("completed"):
            return Progress("succeeded", 1.0, "done")
        return Progress("running", 0.5, "running")

    def reattach(self, external_id: str, job: JobRecord, ctx: JobContext) -> bool:
        try:
            if self._history(external_id) is not None:
                return True
            queue = self.client.json("GET", "/queue", timeout=10)
        except AdapterError:
            return False
        return external_id in json.dumps(queue)

    def _release(self) -> None:
        """Ask ComfyUI to drop its cached models once the clip is made: it keeps them in memory otherwise."""
        try:
            self.client.request("POST", "/free", json={"unload_models": True, "free_memory": True}, timeout=15)
        except AdapterError:
            pass  # best effort: the clip is made; a server without /free just keeps its cache

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        entry = self._history(external_id) or {}
        tpl = self.template(job)
        if isinstance(tpl.get("outputs"), dict):
            artifacts: list[ArtifactSpec] = []
            for port, spec in tpl["outputs"].items():
                key = str(spec.get("type", "images"))
                items = entry.get("outputs", {}).get(str(spec["node"]), {}).get(key, [])
                index = int(spec.get("index", 0))
                if key not in OUTPUT_KEYS or index < 0 or index >= len(items):
                    raise AdapterError(ErrorCode.OUTPUT_MISSING, f"Missing declared output {port}")
                item = items[index]
                suffix = Path(item["filename"]).suffix.lower()
                query = urllib.parse.urlencode(
                    {
                        "filename": item["filename"],
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    }
                )
                dest = self.client.download(f"/view?{query}", ctx.work_dir / f"output-{len(artifacts)}{suffix}")
                artifacts.append(
                    ArtifactSpec(
                        dest,
                        slot=None if port == "main" else port,
                        kind=MediaKind(spec["kind"]) if spec.get("kind") else None,
                        metadata={"prompt_id": external_id, "port": port},
                    )
                )
            if not artifacts:
                raise AdapterError(ErrorCode.OUTPUT_MISSING, "No declared workflow outputs")
            self._release()
            return artifacts
        out_node = str(tpl.get("output", ""))
        wanted = tpl.get("output_type", "audio")
        keys = [wanted] if isinstance(wanted, str) else [str(k) for k in wanted]
        keys += [k for k in OUTPUT_KEYS if k not in keys]
        outputs = entry.get("outputs", {})
        nodes = [outputs.get(out_node) or {}] if out_node else list(outputs.values())
        files: list[dict[str, Any]] = []
        for key in keys:
            files = [f for node in nodes for f in (node.get(key) or []) if f.get("filename")]
            if files:
                break
        if not files:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, f"no {keys[0]} output in the workflow history")
        first = files[0]
        suffix = Path(first["filename"]).suffix.lower()
        if suffix in AUDIO_EXT:
            stem = "sfx" if job.kind is MediaKind.AUDIO_SFX else "audio"
        else:
            stem = "video" if suffix in VIDEO_EXT else "image"
        query = urllib.parse.urlencode(
            {
                "filename": first["filename"],
                "subfolder": first.get("subfolder", ""),
                "type": first.get("type", "output"),
            }
        )
        dest = self.client.download(f"/view?{query}", ctx.work_dir / f"{stem}{suffix}")
        self._release()
        return [
            ArtifactSpec(
                dest,
                metadata={
                    "prompt_id": external_id,
                    "workflow": self._workflows.get(job.model_ref),
                    "prompt": str(job.inputs.get("prompt", ""))[:500],
                },
            )
        ]
