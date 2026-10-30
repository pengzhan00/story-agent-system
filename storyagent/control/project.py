"""Compile selected story shots into the same editable graph used by the workshop."""

from __future__ import annotations

import math
from typing import Any

from storyagent.domain import MediaKind
from storyagent.media.lipsync import frames_for
from storyagent.media.specs import SpecStore
from storyagent.workflow.media import slot_asset

from .catalog import ControlError


def project_plan(
    ctx: Any,
    pid: int,
    script_id: int,
    source_audio: str = "",
    assemble: bool = False,
    tail_policy: str = "reject",
    separate_vocals: bool = True,
) -> dict[str, Any]:
    script = ctx.db.conn.execute("SELECT title FROM scripts WHERE id=? AND project_id=?", (script_id, pid)).fetchone()
    if script is None:
        raise ControlError("Script does not belong to this project")
    rows = ctx.db.conn.execute(
        "SELECT id FROM shots WHERE project_id=? AND script_id=? ORDER BY act_number,scene_number,shot_number,id",
        (pid, script_id),
    ).fetchall()
    if not rows or len(rows) > 150:
        raise ControlError("A script plan requires 1..150 selected shots")
    cfg = ctx.ledger.settings_for(pid)
    nodes: list[dict[str, Any]] = []
    placements: list[dict[str, Any]] = []
    cursor = 0.0
    for row in rows:
        shot = ctx.story.get_shot(row[0])
        payload = shot["render_payload"]
        spec = SpecStore(ctx.db, cfg, ctx.ledger).current(pid, "shot.video", str(shot["id"]))
        spec_params = dict(spec.params) if spec else {}
        mode = str(spec_params.get("mode") or payload.get("mode") or "i2v")
        try:
            seconds = float(payload.get("seconds") or (payload.get("camera") or {}).get("duration_sec") or 3)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ControlError("Invalid shot duration") from exc
        if mode not in {"t2v", "i2v", "a2v"} or not math.isfinite(seconds) or not 0 < seconds <= 40:
            raise ControlError(f"Shot {shot['shot_number']} has an unsupported mode or duration")
        inputs: dict[str, Any] = {
            "prompt": str(
                (spec.prompt if spec else "")
                or payload.get("prompt")
                or payload.get("video_prompt")
                or (payload.get("subject") or {}).get("action")
                or shot.get("narration")
                or ""
            )
        }
        try:
            fps = int(spec_params.get("frame_rate", 24))
        except (ValueError, TypeError, OverflowError) as exc:
            raise ControlError("Invalid shot frame rate") from exc
        if not 8 <= fps <= 60:
            raise ControlError("Invalid shot frame rate")
        params: dict[str, Any] = {**spec_params, "mode": mode, "frames": frames_for(seconds, fps), "frame_rate": fps}
        if mode != "t2v":
            image = slot_asset(ctx.ledger, pid, "shot.keyframe", str(shot["id"]), "main")
            if image is None:
                raise ControlError(f"Shot {shot['shot_number']} requires an adopted keyframe")
            inputs["image"] = f"asset:{image.id}"
        kind = MediaKind.VIDEO_LIPSYNC if mode == "a2v" else MediaKind.VIDEO_GENERATE
        if mode == "a2v":
            audio = source_audio or spec_params.get("source_audio") or payload.get("audio_asset")
            if not audio:
                raise ControlError(f"Shot {shot['shot_number']} requires a source audio asset")
            inputs["audio"] = audio
            params.update(
                duration=seconds,
                audio_start_time=float(spec_params.get("audio_start_time", payload.get("audio_start", cursor))),
                audio_stem_auto=separate_vocals,
            )
        ident = f"shot_{shot['id']}"
        model = spec.model_ref if spec and spec.kind == kind else cfg.default_model(kind.value).ref
        nodes.append(
            {
                "id": ident,
                "kind": kind.value,
                "model_ref": model,
                "inputs": inputs,
                "params": params,
                "review": False,
            }
        )
        placements.append({"video": {"$node": ident, "port": "main"}, "seconds": seconds})
        cursor += seconds
    if assemble:
        if not source_audio.startswith("asset:"):
            raise ControlError("Audio-locked assembly requires a source audio asset")
        audio = ctx.ledger.get_asset(source_audio[6:])
        if audio.duration_sec is None:
            raise ControlError("Source audio duration is unverified")
        delta = audio.duration_sec - cursor
        if abs(delta) > 0.02:
            if tail_policy != "freeze_last" or not 0 <= delta <= 1 or nodes[-1]["kind"] == "video.lipsync":
                raise ControlError(
                    f"Shot duration {cursor:.6f} differs from audio {audio.duration_sec:.6f}; "
                    "select an explicit tail policy or edit the shots"
                )
            placements[-1]["seconds"] += delta
        nodes.append(
            {
                "id": "film",
                "kind": "compose.assemble",
                "model_ref": "control-stitch",
                "inputs": {"shots": placements, "bgm": source_audio},
                "params": {
                    "operation": "stitch",
                    "fps": 24,
                    "audio_start": 0,
                    "duration": audio.duration_sec,
                    "allow_tail_freeze": tail_policy == "freeze_last",
                },
                "review": True,
            }
        )
    return {"name": script["title"], "project_id": pid, "nodes": nodes}
