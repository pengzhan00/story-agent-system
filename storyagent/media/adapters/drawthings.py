"""Draw Things image and silent video generation through its built-in HTTP API server (``/sdapi/v1`` routes).

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
use the model selected in the app. Image jobs take the first encoded image; video jobs require the requested
8n+1 frames and encode all of them at the explicit fps. This HTTP transport does not return audio.
"""
from __future__ import annotations

import base64
import binascii
import subprocess
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
from ..probe import find_tool
from ._http import ServiceClient, clean

_RENAME = {"guidance": "guidance_scale"}
_ALLOWED = {"width", "height", "steps", "guidance_scale", "sampler", "shift", "strength", "clip_skip", "loras"}
_VIDEO = MediaKind.VIDEO_GENERATE
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
    "frames": {"type": "int", "min": 9, "max": 361},
    "fps": {"type": "int", "min": 1, "max": 60},
}


@dataclass
class _Run:
    thread: threading.Thread | None = None
    started: float = field(default_factory=time.monotonic)
    image: bytes | None = None
    frames: list[bytes] = field(default_factory=list)
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
        return Capabilities(kinds=(MediaKind.IMAGE_GENERATE, MediaKind.IMAGE_EDIT, _VIDEO),
                            heavy=True, supports_cancel=True,
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
        refs = job.inputs.get("refs", [])
        if len(refs) > 1 or (job.kind is MediaKind.IMAGE_EDIT and len(refs) != 1):
            issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, "the HTTP API supports one reference image",
                                          {"detail": "refs: one image only; multi-reference needs another transport"}))
        for ref in refs:
            if not isinstance(ref, str) or not Path(ref).is_file():
                issues.append(ValidationIssue(ErrorCode.INVALID_INPUT, "reference image is missing",
                                              {"detail": "refs"}))
        if job.kind is _VIDEO:
            frames, fps = job.params.get("frames", 121), job.params.get("fps", 24)
            if not isinstance(frames, int) or not 9 <= frames <= 361 or frames % 8 != 1:
                issues.append(ValidationIssue(ErrorCode.INVALID_PARAMS, "frames must be 8n+1, from 9 to 361",
                                              {"detail": "frames"}))
            if not isinstance(fps, int) or not 1 <= fps <= 60:
                issues.append(ValidationIssue(ErrorCode.INVALID_PARAMS, "fps must be from 1 to 60",
                                              {"detail": "fps"}))
            if not self._api_models.get(job.model_ref):
                issues.append(ValidationIssue(ErrorCode.INVALID_PARAMS, "video needs an explicit api_model",
                                              {"detail": "api_model"}))
            if find_tool(ctx.settings, "ffmpeg") is None:
                issues.append(ValidationIssue(ErrorCode.MISSING_TOOL, "ffmpeg is required", {"tool": "ffmpeg"}))
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
        # The frame array has no timing or audio. Always freeze video timing and model in the request,
        # even when the app's saved fps is 5 or a request table contains stale settings.
        if job.kind is _VIDEO:
            body.update(num_frames=job.params.get("frames", 121), fps=job.params.get("fps", 24),
                        batch_size=1, model=self._api_models[job.model_ref])
        return route, body

    def _work(self, run: _Run, job: JobRecord, timeout: float) -> None:
        try:
            route, body = self._body(job)
            reply = self.client.json("POST", route, json=body, timeout=timeout)
            images = (reply or {}).get("images") if isinstance(reply, dict) else None
            if not images:
                raise AdapterError(ErrorCode.OUTPUT_MISSING, "the reply carries no image",
                                   service=self.client.service.name)
            if not isinstance(images, list) or any(not isinstance(raw, str) for raw in images):
                raise AdapterError(ErrorCode.OUTPUT_INVALID, "images must be an array of encoded strings")
            if run.cancelled:
                return
            if job.kind is _VIDEO and len(images) != int(job.params.get("frames", 121)):
                raise AdapterError(ErrorCode.OUTPUT_INVALID, "video reply has the wrong frame count")
            decoded: list[bytes] = []
            total = 0
            for raw in images if job.kind is _VIDEO else images[:1]:
                raw = str(raw).split(",", 1)[-1]
                if len(raw) > 48 * 1024 * 1024:
                    raise AdapterError(ErrorCode.OUTPUT_INVALID, "image exceeds the reply limit")
                data = base64.b64decode(raw, validate=True)
                total += len(data)
                if total > 512 * 1024 * 1024:
                    raise AdapterError(ErrorCode.OUTPUT_INVALID, "frame array exceeds the reply limit")
                decoded.append(data)
            if job.kind is _VIDEO:
                run.frames = decoded
            else:
                run.image = decoded[0]
        except AdapterError as exc:
            run.error = exc
        except (binascii.Error, ValueError) as exc:
            run.error = AdapterError(ErrorCode.OUTPUT_INVALID, f"invalid image encoding: {type(exc).__name__}")
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
        if run is None or not (run.image or run.frames):
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "no image came back")
        if job.kind is _VIDEO:
            return self._video(run, job, ctx)
        dest = ctx.work_dir / "image.png"
        if run.image is None:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "no image data came back")
        dest.write_bytes(run.image)
        params = {k: v for k, v in job.params.items() if k != "request"}
        return [ArtifactSpec(dest, metadata={"api_model": self._api_models.get(job.model_ref, ""),
                                             "params_sent": sorted(params)})]

    def _video(self, run: _Run, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        binary = find_tool(ctx.settings, "ffmpeg")
        if binary is None:
            raise AdapterError(ErrorCode.MISSING_TOOL, "ffmpeg is required", tool="ffmpeg")
        folder = ctx.work_dir / "frames"
        folder.mkdir(parents=True, exist_ok=True)
        for i, frame in enumerate(run.frames):
            (folder / f"frame-{i:05d}.png").write_bytes(frame)
        dest = ctx.work_dir / "video.mp4"
        fps = int(job.params.get("fps", 24))
        try:
            proc = subprocess.run(
                [str(binary), "-y", "-nostdin", "-loglevel", "error", "-framerate", str(fps),
                 "-i", str(folder / "frame-%05d.png"), "-frames:v", str(len(run.frames)), "-an",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)],
                capture_output=True, timeout=120, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdapterError(ErrorCode.TOOL_FAILED, f"frame encoding: {type(exc).__name__}") from exc
        if proc.returncode:
            raise AdapterError(ErrorCode.OUTPUT_INVALID, proc.stderr.decode(errors="replace")[-400:])
        return [ArtifactSpec(dest, metadata={"api_model": self._api_models.get(job.model_ref, ""),
                                             "frames": len(run.frames), "fps": fps, "audio_from_api": False})]

    def cancel(self, external_id: str, job: JobRecord, ctx: JobContext) -> None:
        run = self._runs.get(external_id)
        if run is not None:
            run.cancelled = True
