"""Finite, persisted a2v expansion. All I/O is delegated to ordinary media jobs."""

from __future__ import annotations

import json
import math
from typing import Any

from storyagent.media.lipsync import frames_for

from .catalog import ControlError, descriptor


def sequence_options(node: dict[str, Any]) -> dict[str, Any] | None:
    if node["kind"] != "video.lipsync" or not node["params"].get("duration"):
        return None
    p = node["params"]
    try:
        duration, maximum = float(p.pop("duration")), float(p.pop("max_part_seconds", 5))
        start = float(p.get("audio_start_time", 0))
        fps = int(p.get("frame_rate", 24))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ControlError("Invalid lipsync interval") from exc
    if (
        not all(math.isfinite(x) for x in (duration, maximum, start))
        or not 0 < duration <= 360
        or not 1 <= maximum <= 5
        or start < 0
        or not 8 <= fps <= 60
    ):
        raise ControlError("Invalid bounded lipsync interval")
    return {
        "start": start,
        "end": start + duration,
        "maximum": maximum,
        "fps": fps,
        "pieces": [],
        "lengths": [],
        "cursor": start,
        "next": "render",
        "child": None,
        "max_parts": min(200, math.ceil(duration / maximum) * 2 + 2),
    }


def advance(store: Any, run: dict[str, Any], node: dict[str, Any], states: dict[str, Any]) -> None:
    rid, nid, spec = run["id"], node["id"], node["sequence"]
    own = states[nid]
    if own["status"] in {"succeeded", "failed", "blocked", "cancelled"}:
        return
    deps = [states[d] for d in node["depends_on"]]
    if any(d["status"] in {"failed", "blocked", "cancelled", "skipped"} for d in deps):
        store._set(rid, nid, "blocked", "dependency_failed")
        return
    if not all(d["status"] == "succeeded" for d in deps):
        return
    child = states.get(spec["child"])
    if child:
        if child["status"] in {"failed", "blocked", "cancelled"}:
            store._set(rid, nid, "blocked", "dependency_failed")
            return
        if child["status"] != "succeeded":
            return
        asset = store.ledger.get_asset(child["outputs"]["main"])
        if spec["next"] == "render":
            spec["pieces"].append(asset.id)
            remaining = spec["end"] - spec["cursor"]
            # The part just rendered covers whole frames, a little past `maximum`: what is left within that is done.
            covered = frames_for(min(spec["maximum"], remaining), spec["fps"]) / spec["fps"]
            if remaining <= max(spec["maximum"], covered) + 1e-6:
                spec["lengths"].append(remaining)
                spec["next"] = "stitch"
            else:
                spec["next"] = "anchor"
        elif spec["next"] == "anchor":
            start = float(asset.metadata["audio_start_time"])
            if not spec["cursor"] + 0.05 < start <= spec["cursor"] + spec["maximum"] + 0.05:
                store._set(rid, nid, "failed", "anchor_did_not_advance")
                return
            if start >= spec["end"] - 1e-6:         # the anchor lies at or past the end: nothing is left to render
                spec["lengths"].append(spec["end"] - spec["cursor"])
                spec["cursor"] = spec["end"]
                spec["next"] = "stitch"
            else:
                spec["lengths"].append(start - spec["cursor"])
                spec["cursor"] = start
                spec["anchor"] = asset.id
                spec["next"] = "render"
        else:
            store._set(rid, nid, "waiting_review" if node["review"] else "succeeded", outputs=child["outputs"])
            return
    elif spec["child"]:
        raise ControlError("Sequence checkpoint refers to a missing child")
    if len(spec["pieces"]) > spec["max_parts"]:
        store._set(rid, nid, "failed", "sequence_limit")
        return
    values = store.resolve(node["inputs"], states)
    # Source length is verified before any GPU task is queued.
    if not spec.get("checked"):
        raw = str(values["audio"])
        if not raw.startswith("asset:"):
            raise ControlError("Sequence audio requires an asset reference")
        audio = store.ledger.get_asset(raw[6:])
        if audio.duration_sec is None or spec["end"] > audio.duration_sec + 0.02:
            store._set(rid, nid, "failed", "source_interval_out_of_range")
            return
        spec["checked"] = True
    operation = spec["next"]
    if len(run["plan"]["nodes"]) >= 1000:
        store._set(rid, nid, "failed", "sequence_limit")
        return
    ident = f"{nid}__{operation}_{len(spec['pieces'])}"
    inputs: dict[str, Any]
    params: dict[str, Any]
    if operation == "render":
        if len(spec["pieces"]) >= spec["max_parts"]:
            store._set(rid, nid, "failed", "sequence_limit")
            return
        image = f"asset:{spec['anchor']}" if spec.get("anchor") else values["image"]
        inputs = {
            "prompt": values.get("prompt", ""),
            "refs": [{"role": "image", "path": image}, {"role": "audio", "path": values["audio"]}],
        }
        params = {
            **store.resolve(node["params"], states),
            "frames": frames_for(min(spec["maximum"], spec["end"] - spec["cursor"]), spec["fps"]),
            "audio_start_time": spec["cursor"],
            "mode": "a2v",
        }
        kind, model = "video.lipsync", node["model_ref"]
    elif operation == "anchor":
        source = store.ledger.get_asset(spec["pieces"][-1])
        panel = source.metadata.get("panel_file")
        if not panel:
            store._set(rid, nid, "failed", "panel_source_missing")
            return
        inputs = {"panel_clip": panel, "source_video": f"asset:{source.id}"}
        params = {"operation": "anchor", "source_start": spec["cursor"], "source_end": spec["cursor"] + spec["maximum"]}
        kind, model = "image.generate", "control-anchor"
    else:
        inputs = {"clips": [f"asset:{a}" for a in spec["pieces"]], "source_audio": values["audio"]}
        params = {
            "operation": "stitch",
            "lengths": spec["lengths"],
            "duration": spec["end"] - spec["start"],
            "audio_start": spec["start"],
            "fps": spec["fps"],
        }
        kind, model = "compose.assemble", "control-stitch"
    cfg = store.ledger.settings_for(run["project_id"])
    cap = descriptor(cfg, kind)
    binding = next(b for b in cap["bindings"] if b["id"] == model)
    created = {
        "id": ident,
        "kind": kind,
        "model_ref": model,
        "tier": node["tier"],
        "inputs": inputs,
        "params": params,
        "seed": node["seed"],
        "binding_digest": binding["digest"],
        "outputs": cap["outputs"],
        "review": False,
        "skip": False,
        "depends_on": [],
        "prepared": True,
    }
    run["plan"]["nodes"].append(created)
    spec["child"] = ident
    store.db.conn.execute("INSERT INTO control_nodes(run_id,node_id) VALUES (?,?)", (rid, ident))
    store.db.conn.execute(
        "UPDATE control_runs SET plan=?,revision=revision+1 WHERE id=?", (json.dumps(run["plan"]), rid)
    )
    if own["status"] == "pending":
        store._set(rid, nid, "running")
    store.event(rid, nid, "expanded", child=ident, operation=operation, source_start=spec["cursor"])
