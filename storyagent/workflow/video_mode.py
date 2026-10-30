"""One explicit mode policy shared by production and individual regeneration."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from storyagent.config import Settings
from storyagent.domain import MediaKind
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import LedgerError


def shot_mode(payload: dict[str, Any], params: dict[str, Any]) -> str:
    return str(params.get("mode") or payload.get("mode") or "auto")


def video_params(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    mode = payload.get("mode")
    if mode in {"t2v", "i2v", "a2v"}:
        result["mode"] = mode
    if mode == "a2v":
        result["audio_start_time"] = float(payload.get("audio_start") or 0)
        source = payload.get("audio_asset") or (payload.get("audio") or {}).get("source_audio")
        if source:
            result["source_audio"] = source
    return result


def prepare_video(settings: Settings, job: MediaJob, payload: dict[str, Any], frame: Any,
                  project: dict[str, Any]) -> MediaJob:
    mode = shot_mode(payload, dict(job.params))
    if mode == "t2v":
        return replace(job, inputs={**job.inputs, "refs": []}, params={**job.params, "mode": mode})
    if mode == "a2v":
        source = job.params.get("source_audio") or payload.get("audio_asset") or project.get("source_audio")
        if not source:
            source = (payload.get("audio") or {}).get("source_audio")
        if not frame or not source:
            raise LedgerError("a2v requires an adopted keyframe and source_audio")
        cfg = settings.default_model(MediaKind.VIDEO_LIPSYNC.value)
        params = {k: v for k, v in job.params.items() if k != "source_audio"}
        params.update(mode="a2v", audio_start_time=float(params.get("audio_start_time", payload.get("audio_start", 0))))
        return replace(job, kind=MediaKind.VIDEO_LIPSYNC, model_ref=cfg.ref, params=params,
                       inputs={**job.inputs, "refs": [{"role": "image", "path": f"asset:{frame.id}"},
                                                      {"role": "audio", "path": str(source)}]})
    if mode == "i2v" and not frame:
        raise LedgerError("i2v requires an adopted keyframe")
    return job
