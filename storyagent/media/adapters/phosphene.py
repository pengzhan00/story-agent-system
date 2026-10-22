"""Phosphene panel adapter: LTX-2.5 text/image-to-video with synchronized sound, and audio-driven lip-sync.

The panel (a Pinokio app, default port 8198) owns the model and its own queue; this adapter submits to it and
follows the job by id, so a restarted worker can re-attach to a render that is still running.
Panel protocol (form-encoded): ``POST /queue/add`` -> ``GET /status`` (``current`` / ``queue`` / ``history``)
-> the finished file on local disk (``output_path``; a ``*_1080p`` sibling exists when upscaling was on).
Cancel: ``POST /stop?id=`` for the running job, ``POST /queue/remove`` for a queued one.

Kinds: ``video.av`` (picture + sound), ``video.generate`` (picture only: the sound is stripped), ``video.lipsync``
(portrait image + speech audio -> the mouth follows the audio).

Parameters (defaults in models.toml; the best recipe from the operator's notes is the starting point):
    width, height (multiples of 32)   frames (must satisfy frames % 8 == 1; 121 = 5 s at 24 fps)   frame_rate
    quality (balanced|high)   stage1_steps, stage2_steps   cfg_scale   upscale (off|fit_720p|fit_1080p)
    upscale_method   enhance (bool; keep false to preserve the dialogue text verbatim)   mode (override)
Inputs: ``prompt``, ``negative_prompt``, ``refs`` = files: a str (kind guessed from the extension) or
{"role": image|audio|start_image|end_image, "path": ...}.
Known panel behaviours handled here: it silently clamps oversized resolutions (reported in metadata and as
a warning event) and above 121 frames only ``quality=high`` keeps a character's identity.
"""
from __future__ import annotations

import subprocess
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
from ..probe import find_tool, probe
from ._http import ServiceClient

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}
_FORM = (
    "width", "height", "frames", "frame_rate", "quality", "stage1_steps", "stage2_steps", "cfg_scale", "upscale",
    "upscale_method", "enhance", "audio_start_time", "audio_conditioning_scale",
)
SCHEMA: dict[str, dict[str, Any]] = {
    "width": {"type": "int", "min": 64, "max": 4096},
    "height": {"type": "int", "min": 64, "max": 4096},
    "frames": {"type": "int", "min": 9, "max": 1000},
    "frame_rate": {"type": "int", "min": 8, "max": 60},
    "quality": {"type": "str"},
    "stage1_steps": {"type": "int", "min": 1, "max": 100},
    "stage2_steps": {"type": "int", "min": 0, "max": 100},
    "cfg_scale": {"type": "float", "min": 0, "max": 20},
    "upscale": {"type": "str"},
    "upscale_method": {"type": "str"},
    "enhance": {"type": "bool"},
    "audio_start_time": {"type": "float", "min": 0},
    "audio_conditioning_scale": {"type": "float", "min": 0, "max": 5},
    "mode": {"type": "str"},
    "negative_prompt": {"type": "str"},
    "keep_audio": {"type": "bool"},
}
_DONE = {"done", "completed", "complete", "success", "succeeded"}
_CANCELLED = {"cancelled", "canceled", "stopped", "aborted"}


def _role(ref: Any) -> tuple[str, str] | None:
    if isinstance(ref, dict) and ref.get("path"):
        path = str(ref["path"])
        role = str(ref.get("role") or "")
        if not role:
            role = "audio" if Path(path).suffix.lower() in AUDIO_EXT else "image"
        return role, path
    if isinstance(ref, str):
        ext = Path(ref).suffix.lower()
        if ext in AUDIO_EXT:
            return "audio", ref
        if ext in IMAGE_EXT:
            return "image", ref
    return None


def _negatives(*parts: Any) -> str:
    """The project's own avoid-list (from the style) plus the model's (motion artefacts ...), each term once: the
    creator's list never replaces the model's, otherwise a style list written for pictures drops the video terms."""
    seen: dict[str, None] = {}
    for part in parts:
        for term in str(part or "").split(","):
            if term.strip():
                seen.setdefault(term.strip(), None)
    return ", ".join(seen)


class PhospheneAdapter(AdapterBase):
    provider = "phosphene"

    def __init__(self, client: ServiceClient, poll_interval_sec: float = 5.0) -> None:
        self.client = client
        self.poll_interval_sec = poll_interval_sec

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=(MediaKind.VIDEO_AV, MediaKind.VIDEO_GENERATE, MediaKind.VIDEO_LIPSYNC), heavy=True,
            supports_cancel=True, supports_reattach=True, param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    # -- health -------------------------------------------------------------------------------
    def health(self) -> HealthReport:
        try:
            st = self.client.json("GET", "/status", timeout=8)
        except AdapterError:
            return HealthReport(HealthState.NOT_RUNNING, self.client.base)
        detail = f"ltx={st.get('ltx_version') or '?'}; queue={len(st.get('queue') or [])}"
        if st.get("paused"):
            return HealthReport(HealthState.DEGRADED, f"{detail}; paused: {st.get('paused_reason') or '?'}")
        return HealthReport(HealthState.OK, detail)

    # -- inputs -------------------------------------------------------------------------------
    @staticmethod
    def mode_for(job: JobRecord) -> str:
        explicit = job.params.get("mode")
        if explicit:
            return str(explicit)
        if job.kind is MediaKind.VIDEO_LIPSYNC:
            return "a2v"
        has_image = any((r := _role(x)) and r[0] == "image" for x in job.inputs.get("refs", []))
        return "i2v" if has_image else "t2v"

    def _files(self, job: JobRecord, ctx: JobContext) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for ref in job.inputs.get("refs", []):
            parsed = _role(ref)
            if parsed:
                role, raw = parsed
                p = Path(raw).expanduser()
                out.setdefault(role, p if p.is_absolute() else ctx.settings.home / p)
        return out

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        def bad(code: ErrorCode, msg: str, **params: Any) -> None:
            issues.append(ValidationIssue(code, msg, {"detail": msg, **params}))

        p = job.params
        if not (job.inputs.get("prompt") or job.kind is MediaKind.VIDEO_LIPSYNC):
            bad(ErrorCode.INVALID_INPUT, "a prompt is required")
        frames = int(p.get("frames", 121))
        if frames % 8 != 1:
            bad(ErrorCode.INVALID_PARAMS, f"frames must satisfy frames % 8 == 1 (121 = 5 s), got {frames}")
        for dim in ("width", "height"):
            if int(p.get(dim, 32)) % 32:
                bad(ErrorCode.INVALID_PARAMS, f"{dim} must be a multiple of 32, got {p[dim]}")
        if frames > 121 and str(p.get("quality", "")) != "high":
            ctx.log("identity_risk", "warning",
                    message="above 121 frames only quality=high keeps a character's identity")
        mode = self.mode_for(job)
        files = self._files(job, ctx)
        need = {"i2v": ["image"], "a2v": ["image", "audio"], "keyframe": ["start_image"]}.get(mode, [])
        for role in need:
            if role not in files:
                bad(ErrorCode.INVALID_INPUT, f"mode {mode} needs a {role} reference")
            elif not files[role].is_file():
                bad(ErrorCode.INVALID_INPUT, f"{role} reference not found: {files[role]}", path=str(files[role]))
        return issues

    # -- submit / poll ------------------------------------------------------------------------
    def _form(self, job: JobRecord, ctx: JobContext) -> dict[str, str]:
        form: dict[str, Any] = {k: job.params[k] for k in _FORM if k in job.params}
        form.update(
            mode=self.mode_for(job), prompt=str(job.inputs.get("prompt", "")),
            negative_prompt=_negatives(job.inputs.get("negative_prompt"), job.params.get("negative_prompt")),
            seed=job.seed if job.seed is not None else -1, label=f"storyagent-{job.id[:8]}",
        )
        for role, path in self._files(job, ctx).items():
            form[role] = str(path)
        if job.kind is MediaKind.VIDEO_LIPSYNC:
            form.setdefault("enhance", False)
        return {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in form.items() if v not in (None, "")}

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        body = self.client.json("POST", "/queue/add", data=self._form(job, ctx))
        if not isinstance(body, dict) or not body.get("ok") or not body.get("id"):
            raise AdapterError(ErrorCode.SERVICE_REJECTED, f"panel refused the job: {str(body)[:200]}",
                               service=self.client.service.name, detail=str((body or {}).get("error", body))[:200])
        return str(body["id"])

    def _find(self, external_id: str) -> tuple[str, dict[str, Any]]:
        st = self.client.json("GET", "/status", timeout=15)
        cur = st.get("current") or {}
        if cur.get("id") == external_id:
            return "running", cur
        for q in st.get("queue") or []:
            if q.get("id") == external_id:
                return "queued", q
        for h in st.get("history") or []:
            if h.get("id") == external_id:
                return "history", h
        return "unknown", {}

    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress:
        where, rec = self._find(external_id)
        if where == "running":
            pr = rec.get("progress") or {}
            pct = float(pr.get("pct") or 0) / 100.0
            return Progress("running", min(pct, 0.99), str(pr.get("phase_label") or "")[:80])
        if where == "queued":
            return Progress("running", 0.0, "queued in the panel")
        if where == "history":
            status = str(rec.get("status", "")).lower()
            if status in _DONE:
                return Progress("succeeded", 1.0, "done")
            if status in _CANCELLED:
                return Progress("failed", error=AdapterError(ErrorCode.CANCELLED))
            err = str(rec.get("error") or status or "failed")
            return Progress("failed", error=AdapterError(ErrorCode.WORKFLOW_FAILED, err, detail=err[:300]))
        raise AdapterError(ErrorCode.SERVICE_REJECTED, f"panel does not know job {external_id}",
                           retryable=True, service=self.client.service.name, detail="unknown job")

    def reattach(self, external_id: str, job: JobRecord, ctx: JobContext) -> bool:
        try:
            return self._find(external_id)[0] != "unknown"
        except AdapterError:
            return False

    def cancel(self, external_id: str, job: JobRecord, ctx: JobContext) -> None:
        try:
            where, _ = self._find(external_id)
            if where == "running":
                self.client.request("POST", f"/stop?id={external_id}")
            elif where == "queued":
                self.client.request("POST", "/queue/remove", data={"id": external_id})
        except AdapterError as exc:
            if exc.code is ErrorCode.SERVICE_REJECTED and "409" in exc.message:
                return  # already finished: nothing to stop
            ctx.log("cancel_failed", "warning", message=exc.message)

    # -- fetch --------------------------------------------------------------------------------
    @staticmethod
    def _output(rec: dict[str, Any]) -> Path | None:
        raw = rec.get("output_path") or rec.get("raw_path")
        if not raw:
            return None
        path = Path(str(raw))
        upscaled = path.with_name(path.stem + "_1080p" + path.suffix)
        return upscaled if upscaled.is_file() else path

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        where, rec = self._find(external_id)
        src = self._output(rec) if where == "history" else None
        if src is None or not src.is_file():
            raise AdapterError(ErrorCode.OUTPUT_MISSING, f"panel reports done but no file: {rec.get('output_path')}",
                               path=str(rec.get("output_path")))
        meta: dict[str, Any] = {"panel_job_id": external_id, "panel_file": str(src), "mode": self.mode_for(job)}
        info = probe(ctx.settings, src)
        want_w, want_h = job.params.get("width"), job.params.get("height")
        upscaled = str(job.params.get("upscale", "off")) not in ("", "off")
        if info and info.width and info.height:
            meta["actual_size"] = [info.width, info.height]
            if want_w and want_h and not upscaled and (info.width, info.height) != (int(want_w), int(want_h)):
                meta["requested_size"] = [int(want_w), int(want_h)]
                ctx.log("resolution_clamped", "warning", requested=[want_w, want_h], actual=[info.width, info.height])
        keep_audio = job.params.get("keep_audio", job.kind is not MediaKind.VIDEO_GENERATE)
        if not keep_audio:
            src = self._strip_audio(src, ctx)
            meta["audio_stripped"] = True
        return [ArtifactSpec(src, metadata=meta)]

    @staticmethod
    def _strip_audio(src: Path, ctx: JobContext) -> Path:
        ffmpeg = find_tool(ctx.settings, "ffmpeg")
        if ffmpeg is None:
            raise AdapterError(ErrorCode.MISSING_TOOL, "ffmpeg is needed to strip the audio track", tool="ffmpeg")
        dest = ctx.work_dir / f"silent{src.suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [str(ffmpeg), "-y", "-loglevel", "error", "-i", str(src), "-an", "-c:v", "copy", str(dest)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0 or not dest.is_file():
            raise AdapterError(ErrorCode.TOOL_FAILED, proc.stderr[-200:], exit_code=proc.returncode)
        return dest
