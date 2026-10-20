"""ACE-Step 1.5 standalone API adapter (music, with or without vocals).

Vocals must come from this service, not from ComfyUI's native nodes (those lack the 5Hz LM and render
silence). Protocol: ``POST /release_task`` -> poll ``POST /query_result`` -> ``GET /v1/audio?path=``.

Parameters (all optional; defaults live in ``models.toml``):
    duration        seconds of audio                     -> audio_duration
    steps           diffusion steps                      -> inference_steps
    guidance_scale, shift, infer_method (ode|sde), bpm, key_scale, time_signature, vocal_language,
    audio_format (mp3|wav|flac), thinking, use_cot_caption, use_cot_language, lm_temperature, lm_cfg_scale ...
Inputs: ``prompt`` = style tags / caption, ``text`` = lyrics, ``refs`` = optional audio paths
(first one becomes the reference-audio for style transfer).
The server model name is ``extra.api_model`` of the model entry (falls back to ``acestep-<version>``).
"""
from __future__ import annotations

import json
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
from ._http import ServiceClient, clean

_RENAME = {"duration": "audio_duration", "steps": "inference_steps"}
_ALLOWED = {
    "audio_duration", "inference_steps", "guidance_scale", "shift", "infer_method", "bpm", "key_scale",
    "time_signature", "vocal_language", "audio_format", "thinking", "use_cot_caption", "use_cot_language",
    "constrained_decoding", "lm_temperature", "lm_cfg_scale", "lm_top_k", "lm_top_p", "lm_repetition_penalty",
    "lm_backend", "lm_model_path", "batch_size", "task_type", "sample_mode",
}
SCHEMA: dict[str, dict[str, Any]] = {
    "duration": {"type": "float", "min": 1, "max": 600},
    "steps": {"type": "int", "min": 1, "max": 200},
    "guidance_scale": {"type": "float", "min": 0, "max": 30},
    "shift": {"type": "float", "min": 0, "max": 20},
    "infer_method": {"type": "str", "choices": ["ode", "sde"]},
    "bpm": {"type": "int", "min": 20, "max": 300},
    "key_scale": {"type": "str"},
    "time_signature": {"type": "str"},
    "vocal_language": {"type": "str"},
    "audio_format": {"type": "str", "choices": ["mp3", "wav", "flac"]},
    "thinking": {"type": "bool"},
    "use_cot_caption": {"type": "bool"},
    "use_cot_language": {"type": "bool"},
    "constrained_decoding": {"type": "bool"},
    "lm_temperature": {"type": "float", "min": 0, "max": 2},
    "lm_cfg_scale": {"type": "float", "min": 0, "max": 20},
    "lm_top_k": {"type": "int", "min": 0},
    "lm_top_p": {"type": "float", "min": 0, "max": 1},
    "lm_repetition_penalty": {"type": "float", "min": 0, "max": 5},
    "lm_backend": {"type": "str", "choices": ["vllm", "pt", "mlx"]},
    "lm_model_path": {"type": "str"},
    "batch_size": {"type": "int", "min": 1, "max": 8},
    "task_type": {"type": "str"},
    "sample_mode": {"type": "bool"},
}


class AceStepAdapter(AdapterBase):
    provider = "acestep"

    def __init__(
        self, client: ServiceClient, models: dict[str, str] | None = None, poll_interval_sec: float = 3.0
    ) -> None:
        self.client = client
        self.poll_interval_sec = poll_interval_sec
        self._api_models = models or {}  # model_ref -> server-side model name

    # -- contract -----------------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.AUDIO_MUSIC,), heavy=False, supports_cancel=False, supports_reattach=True,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    def api_model(self, job: JobRecord) -> str:
        return self._api_models.get(job.model_ref) or ""

    def health(self) -> HealthReport:
        try:
            body = self.client.json("GET", "/v1/model_inventory", timeout=5)
        except AdapterError:
            return HealthReport(HealthState.NOT_RUNNING, self.client.base)
        data = body.get("data") or {}
        loaded = [m["name"] for m in data.get("models", []) if m.get("is_loaded")]
        lm_ok = bool(data.get("llm_initialized"))
        detail = f"models={','.join(loaded) or '-'}; lm={'yes' if lm_ok else 'no'}"
        if not loaded:
            return HealthReport(HealthState.MISSING_MODEL, detail, ("loaded ACE-Step DiT",))
        if not lm_ok:
            return HealthReport(HealthState.DEGRADED, detail + " (vocals need the 5Hz LM)")
        return HealthReport(HealthState.OK, detail)

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not (job.inputs.get("prompt") or job.inputs.get("text")):
            issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, "needs style tags or lyrics",
                                          {"detail": "prompt/text empty"}))
        want = self.api_model(job)
        try:
            data = (self.client.json("GET", "/v1/model_inventory", timeout=10).get("data")) or {}
        except AdapterError as exc:
            return issues + [ValidationIssue(exc.code, exc.message, exc.params)]
        have = {m["name"] for m in data.get("models", [])}
        if want and want not in have:
            issues.append(ValidationIssue(ErrorCode.MISSING_MODEL, f"{want} is not served", {"model": want}))
        if job.inputs.get("text") and not data.get("llm_initialized"):
            issues.append(ValidationIssue(
                ErrorCode.MISSING_MODEL, "vocals need the 5Hz LM, which the server has not loaded",
                {"model": "acestep-5Hz-lm"}))
        return issues

    def _body(self, job: JobRecord) -> dict[str, Any]:
        body: dict[str, Any] = clean(job.params, _RENAME, _ALLOWED)
        body.setdefault("audio_format", "mp3")
        body.setdefault("batch_size", 1)  # the server's default renders several variants; candidates are our job
        body.update(
            prompt=str(job.inputs.get("prompt", "")), lyrics=str(job.inputs.get("text", "")),
            use_random_seed=job.seed is None, seed=job.seed if job.seed is not None else -1,
        )
        model = self.api_model(job)
        if model:
            body["model"] = model
        refs = [r for r in job.inputs.get("refs", []) if isinstance(r, str)]
        if refs:
            body["reference_audio_path"] = refs[0]
        return body

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        body = self.client.json("POST", "/release_task", json=self._body(job))
        task_id = ((body or {}).get("data") or {}).get("task_id")
        if not task_id:
            raise AdapterError(ErrorCode.SERVICE_REJECTED, f"no task_id in {str(body)[:200]}",
                               service=self.client.service.name, detail="no task_id")
        return str(task_id)

    # -- polling ------------------------------------------------------------------------------
    def _result(self, task_id: str) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
        body = self.client.json("POST", "/query_result", json={"task_id_list": json.dumps([task_id])}, timeout=15)
        items = (body or {}).get("data") or []
        if not items:
            raise AdapterError(ErrorCode.SERVICE_REJECTED, f"server does not know task {task_id}",
                               retryable=True, service=self.client.service.name, detail="unknown task")
        item = items[0]
        raw = item.get("result", "[]")
        try:
            results = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except ValueError:
            results = []
        return int(item.get("status", -1)), [r for r in results if isinstance(r, dict)], item

    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress:
        status, results, item = self._result(external_id)
        first = results[0] if results else {}
        # Server versions differ: newer ones report only the outer `status` (0 running, 1 done, 2 failed);
        # older ones also put `stage`/`progress` in each result.
        stage = str(first.get("stage", ""))
        progress = float(first.get("progress", 0) or 0)
        has_file = any(r.get("file") for r in results)
        if stage in ("failed", "error") or status == 2:
            err = first.get("error") or item.get("progress_text") or "generation failed"
            return Progress("failed", error=AdapterError(ErrorCode.WORKFLOW_FAILED, str(err), detail=str(err)[:300]))
        if stage == "succeeded" or (status == 1 and has_file and stage in ("", "succeeded")):
            if not has_file:
                return Progress("failed", error=AdapterError(ErrorCode.OUTPUT_MISSING, "no audio file in result"))
            return Progress("succeeded", 1.0, stage)
        return Progress("running", min(progress, 0.99), stage or str(item.get("progress_text", ""))[-60:])

    def reattach(self, external_id: str, job: JobRecord, ctx: JobContext) -> bool:
        try:
            self._result(external_id)
        except AdapterError:
            return False
        return True

    # -- fetch --------------------------------------------------------------------------------
    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        _, results, _ = self._result(external_id)
        files = [r for r in results if r.get("file")]
        if not files:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "no audio file in result")
        ref = str(files[0]["file"])
        if ref.startswith("/v1/audio") or ref.startswith("http"):
            url = ref
        else:
            url = "/v1/audio?path=" + urllib.parse.quote(ref, safe="")
        ext = Path(urllib.parse.unquote(ref.split("path=")[-1])).suffix or f".{job.params.get('audio_format', 'mp3')}"
        dest = self.client.download(url, ctx.work_dir / f"music{ext}")
        metas = files[0].get("metas")
        meta: dict[str, Any] = {k: v for k, v in (metas if isinstance(metas, dict) else {}).items() if v != "N/A"}
        if files[0].get("prompt"):
            meta["server_caption"] = str(files[0]["prompt"])[:500]  # the LM-expanded caption actually used
        meta.update(task_id=external_id, api_model=self.api_model(job))
        return [ArtifactSpec(dest, metadata=meta)]
