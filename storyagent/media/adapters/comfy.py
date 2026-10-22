"""ComfyUI adapter: runs an API-format workflow template (``media/workflows/<name>.json``) for the model.

A template holds the node graph plus a ``bindings`` table (parameter name -> [node id, input name]), so nothing is
guessed from the graph; the adapter only writes the job's values into those inputs (a binding may name several
inputs: ``[[node, input], ...]``). It serves ``audio.sfx`` (Stable Audio 3) and ``image.edit`` / ``image.generate``
(FLUX.2 klein 4B distilled); another kind needs a template and an entry in ``KINDS``.

Protocol: ``POST /prompt`` -> poll ``GET /history/<id>`` -> ``GET /view`` for the first output of the template's
``output_type`` (``audio`` by default, ``images``).

Reference images (``inputs.refs``, image files): uploaded to ComfyUI, then for each one a LoadImage ->
ImageScaleToTotalPixels -> VAEEncode -> ReferenceLatent chain is built on the template's ``references`` block
(``conditioning`` = text-encode node, ``vae`` = [node, slot], ``consumer`` = [node, input] that takes the chain's end).

Inputs:  ``prompt`` (text), optional ``negative_prompt``, ``refs``
Params:  duration (seconds), steps, cfg, sampler_name, scheduler, checkpoint, text_encoder, vae, width, height,
         ref_megapixels (size the references are scaled to; smaller = faster)
The template name is ``workflow`` of the model entry; the job seed becomes the sampler seed.
"""
from __future__ import annotations

import copy
import json
import random
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

KINDS = (MediaKind.AUDIO_SFX, MediaKind.IMAGE_EDIT, MediaKind.IMAGE_GENERATE)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
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
}
_FILE_NODES = {"CheckpointLoaderSimple": "ckpt_name", "CLIPLoader": "clip_name", "VAELoader": "vae_name"}
_PLACEHOLDER = "reference-not-uploaded-yet.png"


def load_template(name: str) -> dict[str, Any]:
    path = WORKFLOWS / f"{name}.json"
    if not path.is_file():
        raise AdapterError(ErrorCode.MISSING_TOOL, f"workflow template {name!r} not found", tool=name)
    data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    return data


class ComfyAdapter(AdapterBase):
    provider = "comfy"

    def __init__(self, client: ServiceClient, workflows: dict[str, str] | None = None,
                 poll_interval_sec: float = 2.0) -> None:
        self.client = client
        self.poll_interval_sec = poll_interval_sec
        self._workflows = workflows or {}        # model_ref -> template name

    def capabilities(self) -> Capabilities:
        return Capabilities(kinds=KINDS, heavy=False, supports_cancel=False, supports_reattach=True,
                            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec)

    def template(self, job: JobRecord) -> dict[str, Any]:
        return load_template(self._workflows.get(job.model_ref) or "")

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
        if not str(job.inputs.get("prompt", "")).strip():
            return [ValidationIssue(ErrorCode.INVALID_INPUT, "needs a prompt", {"detail": "prompt empty"})]
        issues: list[ValidationIssue] = []
        refs = self._refs(job, ctx)
        for ref in refs:
            if not ref.is_file():
                issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, f"reference image not found: {ref}",
                                              {"detail": str(ref)}))
        if job.kind is MediaKind.IMAGE_EDIT and not refs:
            issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, "image.edit needs at least one image in refs",
                                          {"detail": "refs empty"}))
        for dim in ("width", "height"):
            if dim in job.params and int(job.params[dim]) % 16:
                issues.append(ValidationIssue(ErrorCode.INVALID_PARAMS, f"{dim} must be a multiple of 16",
                                              {"detail": f"{dim}={job.params[dim]}"}))
        if issues:
            return issues
        try:
            tpl = self.template(job)
            graph = self._graph(job, tpl, [_PLACEHOLDER] * len(refs))
            for node in graph.values():
                cls = node["class_type"]
                avail = self._available(cls, _FILE_NODES.get(cls, ""))
                if avail is None:
                    issues.append(ValidationIssue(ErrorCode.MISSING_TOOL, f"ComfyUI has no node {cls}",
                                                  {"tool": cls}))
                elif cls in _FILE_NODES and node["inputs"][_FILE_NODES[cls]] not in avail:
                    have = node["inputs"][_FILE_NODES[cls]]
                    issues.append(ValidationIssue(ErrorCode.MISSING_MODEL, f"{have} is not in ComfyUI",
                                                  {"model": have}))
            return issues
        except AdapterError as exc:
            return [ValidationIssue(exc.code, exc.message, exc.params)]

    @staticmethod
    def _refs(job: JobRecord, ctx: JobContext) -> list[Path]:
        out: list[Path] = []
        for raw in job.inputs.get("refs", []):
            if isinstance(raw, str) and Path(raw).suffix.lower() in IMAGE_EXT:
                p = Path(raw).expanduser()
                out.append(p if p.is_absolute() else ctx.settings.home / p)
        return out

    @staticmethod
    def _chain_references(graph: dict[str, Any], block: dict[str, Any], uploaded: list[str], megapixels: float) -> None:
        """Append one LoadImage -> scale -> VAEEncode -> ReferenceLatent link per reference to the conditioning."""
        prev: list[Any] = [block["conditioning"], 0]
        for i, name in enumerate(uploaded):
            graph[f"ref{i}_load"] = {"class_type": "LoadImage", "inputs": {"image": name}}
            graph[f"ref{i}_scale"] = {"class_type": "ImageScaleToTotalPixels",
                                      "inputs": {"image": [f"ref{i}_load", 0], "upscale_method": "lanczos",
                                                 "megapixels": megapixels, "resolution_steps": 16}}
            graph[f"ref{i}_vae"] = {"class_type": "VAEEncode",
                                    "inputs": {"pixels": [f"ref{i}_scale", 0], "vae": list(block["vae"])}}
            graph[f"ref{i}_ref"] = {"class_type": "ReferenceLatent",
                                    "inputs": {"conditioning": prev, "latent": [f"ref{i}_vae", 0]}}
            prev = [f"ref{i}_ref", 0]
        node, field = block["consumer"]
        graph[node]["inputs"][field] = prev

    def _graph(self, job: JobRecord, tpl: dict[str, Any], uploaded: list[str] | None = None) -> dict[str, Any]:
        graph = copy.deepcopy(tpl["graph"])
        bindings: dict[str, list[str]] = tpl["bindings"]
        values: dict[str, Any] = {"prompt": str(job.inputs.get("prompt", "")),
                                  "seed": job.seed if job.seed is not None else random.randint(0, 2**32 - 1)}
        if str(job.inputs.get("negative_prompt", "")).strip():       # else the template's own default stays
            values["negative_prompt"] = str(job.inputs["negative_prompt"])
        for key, value in job.params.items():
            values[_RENAME.get(key, key)] = value
        for name, value in values.items():
            if name in bindings:
                targets = bindings[name]
                for node, field in (targets if isinstance(targets[0], list) else [targets]):
                    graph[node]["inputs"][field] = value
        if uploaded and tpl.get("references"):
            self._chain_references(graph, tpl["references"], uploaded, float(values.get("ref_megapixels", 1.0)))
        return graph

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        uploaded = [self._upload(job, i, path) for i, path in enumerate(self._refs(job, ctx))]
        body = self.client.json("POST", "/prompt", json={"prompt": self._graph(job, self.template(job), uploaded)})
        prompt_id = (body or {}).get("prompt_id")
        if not prompt_id:
            raise AdapterError(ErrorCode.SERVICE_REJECTED, f"no prompt_id in {str(body)[:200]}",
                               service=self.client.service.name, detail="no prompt_id")
        return str(prompt_id)

    def _upload(self, job: JobRecord, index: int, path: Path) -> str:
        """Put a reference image into ComfyUI's input folder (a name unique to the job) and return its name."""
        name = f"storyagent_{job.id[:12]}_{index}{path.suffix.lower()}"
        with path.open("rb") as fh:
            body = self.client.json("POST", "/upload/image", files={"image": (name, fh)},
                                    data={"type": "input", "overwrite": "true"}, timeout=60)
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
            pass          # best effort: the clip is made; a server without /free just keeps its cache

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        entry = self._history(external_id) or {}
        out_node = str(self.template(job).get("output", ""))
        output_type = str(self.template(job).get("output_type", "audio"))
        files = [f for f in (entry.get("outputs", {}).get(out_node, {}).get(output_type) or []) if f.get("filename")]
        if not files:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, f"no {output_type} output in the workflow history")
        first = files[0]
        stem = "image" if output_type == "images" else "sfx"
        query = urllib.parse.urlencode({"filename": first["filename"], "subfolder": first.get("subfolder", ""),
                                        "type": first.get("type", "output")})
        dest = self.client.download(f"/view?{query}", ctx.work_dir / f"{stem}{Path(first['filename']).suffix}")
        self._release()
        return [ArtifactSpec(dest, metadata={"prompt_id": external_id, "workflow": self._workflows.get(job.model_ref),
                                             "prompt": str(job.inputs.get("prompt", ""))[:500]})]
