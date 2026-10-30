"""Stdlib-only runner, invoked by the existing media worker."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def run(config: dict) -> dict:
    out = Path(config["out"])
    p, inputs, ffmpeg = config["params"], config["inputs"], config["ffmpeg"]
    if config["operation"] == "anchor":
        data = urllib.parse.urlencode({"clip": inputs["panel_clip"]}).encode()
        req = urllib.request.Request(config["service"].rstrip("/") + "/a2v/continue_song", data=data)
        with urllib.request.urlopen(req, timeout=30) as response:
            anchor = json.load(response)
        if not anchor.get("ok") or not anchor.get("anchor_image"):
            raise ValueError("Closed-mouth anchor unavailable; choose a frame explicitly")
        # The copied/upscaled clip may have no panel sidecar; our frozen source offset is authoritative.
        start = float(p["source_start"]) + float(anchor["anchor_time_s"])
        if not float(p["source_start"]) + 0.05 < start <= float(p["source_end"]) + 0.05:
            raise ValueError("Anchor must advance within this clip")
        shutil.copy2(anchor["anchor_image"], out / "anchor.png")
        return {
            "file": "anchor.png",
            "audio_start_time": start,
            "anchor_time_s": float(anchor["anchor_time_s"]),
            "panel_anchor": anchor["anchor_image"],
        }
    clips, lengths = inputs["clips"], p["lengths"]
    if not clips or len(clips) != len(lengths) or abs(sum(lengths) - float(p["duration"])) > 0.01:
        raise ValueError("Clip plan does not cover requested audio interval")
    names = []
    for index, (clip, seconds) in enumerate(zip(clips, lengths, strict=True)):
        name = f"part-{index:04d}.mp4"
        filters = f"fps={p['fps']}"
        if p.get("allow_tail_freeze") and index == len(clips) - 1:
            filters += ",tpad=stop_mode=clone:stop_duration=1"
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-y",
                "-i",
                clip,
                "-t",
                str(seconds),
                "-an",
                "-vf",
                filters,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(out / name),
            ],
            check=True,
            timeout=300,
        )
        names.append(f"file '{name}'")
    (out / "clips.txt").write_text("\n".join(names) + "\n")
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            str(out / "clips.txt"),
            "-ss",
            str(p["audio_start"]),
            "-i",
            inputs["source_audio"],
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            str(p["duration"]),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out / "sequence.mp4"),
        ],
        check=True,
        timeout=300,
    )
    ffprobe = config.get("ffprobe") or str(Path(ffmpeg).with_name("ffprobe"))
    checked = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(out / "sequence.mp4")],
        check=True, capture_output=True, text=True, timeout=30,
    )
    streams = json.loads(checked.stdout)["streams"]
    durations = {s["codec_type"]: float(s["duration"]) for s in streams if s.get("duration")}
    tolerance = 1 / float(p["fps"]) + 0.001
    for media in ("video", "audio"):
        if media not in durations or abs(durations[media] - float(p["duration"])) > tolerance:
            raise ValueError(f"{media.title()} stream does not cover the locked interval")
    return {
        "file": "sequence.mp4",
        "stream_durations": durations,
        "source_audio_start": p["audio_start"],
        "planned_seconds": p["duration"],
        "parts": len(clips),
        "original_audio": True,
    }


if __name__ == "__main__":
    cfg = json.loads(Path(sys.argv[1]).read_text())
    (Path(cfg["out"]) / "result.json").write_text(json.dumps(run(cfg)))
