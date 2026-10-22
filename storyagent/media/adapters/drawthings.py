"""Draw Things image generation through its built-in HTTP API server (A1111-compatible ``/sdapi/v1`` routes).

Draw Things runs models in its own ``.ckpt`` format, so a model only Draw Things can run (for instance a Krea-2 based
checkpoint) is reached here, with the app open and its API Server switched on (Advanced settings -> API Server).
Anything a request leaves out is whatever the app currently has selected, so "the configuration in Draw Things" is the
default; the parameters below override it per model, plan, project or job.

Inputs:  ``prompt``, ``negative_prompt``, ``refs`` = image paths (the first one starts an img2img request).
Params:  width, height, steps, guidance (-> guidance_scale), sampler, shift, strength, clip_skip, batch_size is always 1
         (candidates are the ledger's job), loras = [{"file": ..., "weight": 0.8}],
         ``request`` = a table merged over the body as it is sent: the way to pass any key this adapter does not know
         about, without a code change.
The model file is ``api_model`` of the model entry (Draw Things' own file name, e.g. ``xxx_f16.ckpt``); leave it out to
use the model selected in the app. The reply carries base64 images; the first one is the artifact.
"""
from __future__ import annotations

import base64
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.util import new_id

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

_RENAME = {"guidance": "guidance_scale"}
_ALLOWED = {"width", "height", "steps", "guidance_scale", "sampler", "shift", "strength", "clip_skip", "loras"}
SCHEMA: dict[str, dict[str, Any]] = {
    "width": {"type": "int", "min": 64, "max": 4096},
    "height": {"type": "int", "min": 64, "max": 4096},
    "steps": {"type": "int", "min": 1, "max": 200},
    "guidance": {"type": "float", "min": 0, "max": 30},
    "sampler": {"type": "str"},
    "shift": {"type": "float", "min": 0, "max": 20},
    "strength": {"type": "float", "min": 0, "max": 1},
    "clip_skip": {"type": "int", "min": 1, "max": 12},
    "loras": {"type": "list"},
    "request": {"type": "dict"},
}


@dataclass
class _Run:
    thread: threading.Thread | None = None
    started: float = field(default_factory=time.monotonic)
    image: bytes | None = None
    error: AdapterError | None = None
    cancelled: bool = False


class DrawThingsAdapter(AdapterBase):
    provider = "drawthings"

    def __init__(self, client: ServiceClient, models: dict[str, str] | None = None,
                 poll_interval_sec: float = 2.0) -> None:
        self.client = client
        self.poll_interval_sec = poll_interval_sec
        self._api_models = models or {}  # model_ref -> Draw Things model file ("" = the app's selection)
        self._runs: dict[str, _Run] = {}

    def capabilities(self) -> Capabilities:
        # The app cannot be told to stop a render that has begun: a cancel is honoured when the image arrives.
        return Capabilities(kinds=(MediaKind.IMAGE_GENERATE,), heavy=True, supports_cancel=True,
                            supports_reattach=False, param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec)

    def health(self) -> HealthReport:
        try:
            self.client.request("GET", self.client.service.health_path or "/", timeout=5)
        except AdapterError:
            return HealthReport(HealthState.NOT_RUNNING, self.client.base)
        return HealthReport(HealthState.OK, self.client.base)

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not str(job.inputs.get("prompt", "")).strip():
            issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, "a prompt is required", {"detail": "prompt empty"}))
        if self.health().state is not HealthState.OK:
            issues.append(ValidationIssue(
                ErrorCode.SERVICE_UNREACHABLE, "Draw Things' API server does not answer",
                {"service": self.client.service.name}))
        return issues

    # -- the request ----------------------------------------------------------------------------
    def _body(self, job: JobRecord) -> tuple[str, dict[str, Any]]:
        body: dict[str, Any] = clean(job.params, _RENAME, _ALLOWED)
        body.update(
            prompt=str(job.inputs.get("prompt", "")), negative_prompt=str(job.inputs.get("negative_prompt", "")),
            seed=job.seed if job.seed is not None else -1, batch_size=1,
        )
        model = self._api_models.get(job.model_ref, "")
        if model:
            body["model"] = model
        route = "/sdapi/v1/txt2img"
        refs = [Path(r).expanduser() for r in job.inputs.get("refs", []) if isinstance(r, str)]
        if refs:
            route = "/sdapi/v1/img2img"
            body["init_images"] = [base64.b64encode(refs[0].read_bytes()).decode("ascii")]
        extra = job.params.get("request")
        if isinstance(extra, dict):
            body.update(extra)
        return route, body

    def _work(self, run: _Run, job: JobRecord, timeout: float) -> None:
        try:
            route, body = self._body(job)
            reply = self.client.json("POST", route, json=body, timeout=timeout)
            images = (reply or {}).get("images") if isinstance(reply, dict) else None
            if not images:
                raise AdapterError(ErrorCode.OUTPUT_MISSING, "the reply carries no image",
                                   service=self.client.service.name)
            raw = str(images[0])
            run.image = base64.b64decode(raw.split(",", 1)[-1], validate=False)
        except AdapterError as exc:
            run.error = exc
        except OSError as exc:  # a reference image that cannot be read
            run.error = AdapterError(ErrorCode.INVALID_INPUT, f"{type(exc).__name__}: {exc}", detail=str(exc)[:200])
        except Exception as exc:  # noqa: BLE001 - a crash in the thread must end the job, not hang it
            run.error = AdapterError(ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}")

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        ext = new_id()
        run = _Run()
        run.thread = threading.Thread(
            target=self._work, args=(run, job, float(ctx.settings.models[job.model_ref].timeout_sec)),
            daemon=True, name=f"drawthings-{job.id[:8]}")
        self._runs[ext] = run
        run.thread.start()
        return ext

    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress:
        run = self._runs.get(external_id)
        if run is None:
            return Progress("failed", error=AdapterError(ErrorCode.WORKER_LOST, "the request thread is gone",
                                                         retryable=True))
        if run.thread is not None and run.thread.is_alive():
            waited = time.monotonic() - run.started
            expected = float(job.est_seconds or 0)
            fraction = min(0.95, waited / expected) if expected else 0.0   # the app reports no steps over HTTP
            return Progress("running", fraction, f"{int(waited)}s")
        if run.cancelled:
            return Progress("failed", error=AdapterError(ErrorCode.CANCELLED))
        if run.error is not None:
            return Progress("failed", error=run.error)
        return Progress("succeeded", 1.0, "done")

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        run = self._runs.pop(external_id, None)
        if run is None or not run.image:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "no image came back")
        dest = ctx.work_dir / "image.png"
        dest.write_bytes(run.image)
        params = {k: v for k, v in job.params.items() if k != "request"}
        return [ArtifactSpec(dest, metadata={"api_model": self._api_models.get(job.model_ref, ""),
                                             "params_sent": sorted(params)})]

    def cancel(self, external_id: str, job: JobRecord, ctx: JobContext) -> None:
        run = self._runs.get(external_id)
        if run is not None:
            run.cancelled = True
