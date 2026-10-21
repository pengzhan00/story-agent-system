"""Cloud video generation through a configurable REST job API (submit -> poll -> download).

Nothing about a vendor is in the code: the model entry in ``models.toml`` says where the service is
(``service`` = a ``[services.<name>]`` entry), which environment variable holds the key (``api_key_env``; the key
itself is never written to a file, the database or a log) and how the API looks (``[models.<ref>.api]``: paths, the
request body with ``${placeholders}``, which response fields hold the task id / status / progress / result URL). A
vendor with a different shape is a new TOML block, or a small subclass overriding ``build_body`` / ``read_status``.

Placeholders in ``api.submit_body``: ``${prompt} ${negative_prompt} ${image} ${duration} ${width} ${height} ${fps}
${seed}`` and every job parameter by name. A value that is not available drops its key from the body.
The task id of the service is the job's ``external_id``: after a restart the worker simply keeps polling it
(``reattach``), so a paid cloud render is never submitted twice.

Data leaves this machine, so a remote model refuses to run until it is allowed (``[runtime] allow_remote`` or the
project setting ``allow_remote``; see ``compliance.check_remote``).
"""
from __future__ import annotations

import base64
import mimetypes
import os
import re
import string
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from storyagent.config import ModelSpec, Settings
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

PROVIDER = "remote_video"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
SCHEMA: dict[str, dict[str, Any]] = {
    "duration": {"type": "float", "min": 0}, "width": {"type": "int", "min": 16}, "height": {"type": "int", "min": 16},
    "fps": {"type": "int", "min": 1}, "aspect_ratio": {"type": "str"}, "resolution": {"type": "str"},
}
_WHOLE = re.compile(r"^\$\{(\w+)\}$")
_NAME = re.compile(r"\$\{(\w+)\}")
_DEFAULT_API: dict[str, Any] = {
    "submit_method": "POST", "task_id_path": "id", "status_field": "status", "done_values": ["succeeded", "completed"],
    "failed_values": ["failed", "error", "cancelled"], "progress_field": "", "progress_scale": 100.0,
    "result_url_field": "video_url", "error_field": "error", "image_mode": "data_url", "cancel_path": "",
}
_MISSING = object()


def dig(obj: Any, path: str) -> Any:
    """``a.b.0.c`` -> ``obj["a"]["b"][0]["c"]``; ``None`` when any step is missing."""
    cur = obj
    for part in path.split(".") if path else []:
        if isinstance(cur, Mapping):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def fill(template: Any, values: Mapping[str, Any]) -> Any:
    """Substitute ``${name}`` placeholders; a missing value removes the key (or list item) that used it."""
    if isinstance(template, str):
        whole = _WHOLE.match(template)
        if whole:
            return values.get(whole.group(1), _MISSING)
        names = _NAME.findall(template)
        if any(n not in values for n in names):
            return _MISSING
        return _NAME.sub(lambda m: str(values[m.group(1)]), template)
    if isinstance(template, Mapping):
        out = {k: fill(v, values) for k, v in template.items()}
        return {k: v for k, v in out.items() if v is not _MISSING}
    if isinstance(template, list):
        return [v for v in (fill(i, values) for i in template) if v is not _MISSING]
    return template


class RemoteVideoAdapter(AdapterBase):
    provider = PROVIDER

    def __init__(self, settings: Settings, session: requests.Session | None = None, poll_interval_sec: float = 5.0):
        self.settings, self.session, self.poll_interval_sec = settings, session or requests.Session(), poll_interval_sec

    # -- configuration -------------------------------------------------------------------------
    def _models(self) -> list[ModelSpec]:
        return [m for m in self.settings.models.values() if m.provider == PROVIDER]

    def _model(self, job: JobRecord) -> ModelSpec:
        return self.settings.model(job.model_ref)

    @staticmethod
    def _api(model: ModelSpec) -> dict[str, Any]:
        return {**_DEFAULT_API, **dict(model.extra.get("api", {}))}

    def _client(self, model: ModelSpec) -> ServiceClient:
        return ServiceClient(self.settings.service(str(model.extra.get("service", ""))), self.session, timeout=60.0)

    @staticmethod
    def _key(model: ModelSpec) -> str:
        return os.environ.get(str(model.extra.get("api_key_env", "")), "")

    def _headers(self, model: ModelSpec) -> dict[str, str]:
        key = self._key(model)
        name = str(model.extra.get("auth_header", "Authorization"))
        return {name: f"{model.extra.get('auth_prefix', 'Bearer ')}{key}"} if key else {}

    def capabilities(self) -> Capabilities:
        kinds = tuple(dict.fromkeys(MediaKind(m.kind) for m in self._models())) or (MediaKind.VIDEO_GENERATE,)
        return Capabilities(kinds=kinds, heavy=False, supports_cancel=True, supports_reattach=True,
                            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec)

    def health(self) -> HealthReport:
        if not self._models():
            return HealthReport(HealthState.OK, "no remote model configured")
        missing = tuple(m.ref for m in self._models() if not self._key(m))
        if missing:
            return HealthReport(HealthState.DEGRADED, "credentials missing", missing)
        return HealthReport(HealthState.OK, "credentials present")

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        model = self._model(job)
        name = str(model.extra.get("service", ""))
        if name not in self.settings.services or not self.settings.service(name).enabled:
            return [ValidationIssue(ErrorCode.SERVICE_UNREACHABLE, f"service {name!r} is not enabled",
                                    {"service": name, "detail": "disabled"})]
        if not self._key(model):
            env = str(model.extra.get("api_key_env", ""))
            return [ValidationIssue(ErrorCode.MISSING_CREDENTIALS, f"environment variable {env} is not set",
                                    {"env": env})]
        if not self._api(model).get("submit_path"):
            return [ValidationIssue(ErrorCode.INVALID_PARAMS, "api.submit_path is not configured", {"detail": "api"})]
        return []

    # -- the request ---------------------------------------------------------------------------
    @staticmethod
    def _image_value(job: JobRecord, mode: str) -> Any:
        refs = [Path(str(r)) for r in job.inputs.get("refs", []) if isinstance(r, str)]
        pics = [p for p in refs if p.suffix.lower() in IMAGE_EXT and p.is_file()]
        if not pics or mode == "none":
            return _MISSING
        data = base64.b64encode(pics[0].read_bytes()).decode()
        mime = mimetypes.guess_type(pics[0].name)[0] or "image/png"
        return data if mode == "base64" else f"data:{mime};base64,{data}"

    def build_body(self, job: JobRecord, model: ModelSpec) -> dict[str, Any]:
        api, p = self._api(model), job.params
        duration = p.get("duration")
        rate = p.get("fps") or p.get("frame_rate")
        if duration is None and p.get("frames") and rate:
            duration = round(float(p["frames"]) / float(rate), 2)
        values: dict[str, Any] = {**p, "prompt": job.inputs.get("prompt", ""), "seed": job.seed}
        if job.inputs.get("negative_prompt"):
            values["negative_prompt"] = job.inputs["negative_prompt"]
        if duration is not None:
            values["duration"] = duration
        image = self._image_value(job, str(api["image_mode"]))
        if image is not _MISSING:
            values["image"] = image
        values = {k: v for k, v in values.items() if v is not None}
        body = fill(api.get("submit_body", {"prompt": "${prompt}"}), values)
        return dict(body) if isinstance(body, Mapping) else {}

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        model = self._model(job)
        api = self._api(model)
        resp = self._client(model).json(str(api["submit_method"]), str(api["submit_path"]),
                                        json=self.build_body(job, model), headers=self._headers(model))
        task = dig(resp, str(api["task_id_path"]))
        if not task:
            raise AdapterError(ErrorCode.SERVICE_REJECTED, f"no task id at {api['task_id_path']!r}",
                               service=str(model.extra.get("service", "")), detail="no task id")
        ctx.log("remote_submitted", service=str(model.extra.get("service", "")), task=str(task))
        return str(task)

    def _status(self, external_id: str, model: ModelSpec) -> Any:
        api = self._api(model)
        path = string.Template(str(api.get("status_path", "/tasks/${task_id}"))).safe_substitute(task_id=external_id)
        return self._client(model).json("GET", path, headers=self._headers(model))

    def read_status(self, body: Any, model: ModelSpec) -> Progress:
        api = self._api(model)
        state = str(dig(body, str(api["status_field"])) or "").lower()
        if state in [str(v).lower() for v in api["done_values"]]:
            return Progress("succeeded", 1.0)
        if state in [str(v).lower() for v in api["failed_values"]]:
            detail = dig(body, str(api["error_field"])) if api["error_field"] else ""
            code = ErrorCode.CANCELLED if state == "cancelled" else ErrorCode.WORKFLOW_FAILED
            reason = f"{state}: {detail}".strip(": ")
            return Progress("failed", error=AdapterError(code, reason, retryable=False))
        raw = dig(body, str(api["progress_field"])) if api["progress_field"] else None
        fraction = 0.0
        if isinstance(raw, int | float):
            fraction = min(max(float(raw) / float(api["progress_scale"]), 0.0), 0.99)
        return Progress("running", fraction, state)

    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress:
        return self.read_status(self._status(external_id, self._model(job)), self._model(job))

    def reattach(self, external_id: str, job: JobRecord, ctx: JobContext) -> bool:
        return True            # the task lives at the vendor; keep polling it, never submit (and pay) twice

    def cancel(self, external_id: str, job: JobRecord, ctx: JobContext) -> None:
        model = self._model(job)
        api = self._api(model)
        path = str(api.get("cancel_path") or "")
        if path:
            try:
                self._client(model).request("POST", string.Template(path).safe_substitute(task_id=external_id),
                                            headers=self._headers(model))
            except AdapterError:
                pass           # best effort: the local job is cancelled either way

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        model = self._model(job)
        api = self._api(model)
        client = self._client(model)
        url = dig(self._status(external_id, model), str(api["result_url_field"]))
        if not url:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "the service reported success without a result URL")
        full = client.url(str(url))
        same_host = urlparse(full).netloc == urlparse(client.base).netloc
        dest = ctx.work_dir / f"video{Path(urlparse(full).path).suffix or '.mp4'}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:        # the key goes only to the vendor's own host, never to wherever a result link points
            with requests.get(full, headers=self._headers(model) if same_host else {}, stream=True,
                              timeout=300) as resp:
                resp.raise_for_status()
                with dest.open("wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
        except requests.RequestException as exc:
            raise AdapterError(ErrorCode.SERVICE_UNREACHABLE, f"download failed: {type(exc).__name__}",
                               retryable=True) from exc
        meta: dict[str, Any] = {"remote": True, "service": str(model.extra.get("service", "")), "task_id": external_id}
        price = float(model.extra.get("price_per_second", 0) or 0)
        seconds = float(job.params.get("duration") or 0)
        if price and seconds:
            meta.update(estimated_cost=round(price * seconds, 4), currency=str(model.extra.get("currency", "")))
        return [ArtifactSpec(dest, metadata=meta)]
