"""Build the edit decision list (EDL) of one episode.

Pure and deterministic: given the shots, the assets that exist for them and the delivery/mix settings, return a JSON
document that the assemble runner executes. The order of work is *speech first*: dialogue durations are measured
(ffprobe, stored on the asset), only then is the shot as long as it needs to be - so nothing is ever cut mid-sentence
and the timeline is never built from a character-count guess.

Per shot: ``duration = max(planned, lead_in + sum(lines) + gaps + tail)``. The picture follows the duration: a video
clip that is too short is slowed down (up to ``max_slowdown``) and then holds its last frame; one that is too long is
trimmed; a still image gets a slow push-in (Ken Burns). A shot with no picture at all becomes a black card and a
warning - the episode still renders, and the gap is visible in the report.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

GAP_SEC = 0.25          # silence between two lines of one shot
TAIL_SEC = 0.35         # breathing room after the last line
MIN_SHOT_SEC = 1.0
END_CARD_SEC = 2.0


@dataclass(frozen=True)
class AssetRef:
    """What the timeline needs to know about one stored asset (never a path: the worker resolves ``asset:<id>``)."""

    id: str
    kind: str                       # image.generate | video.generate | audio.tts ...
    duration_sec: float | None = None

    @property
    def ref(self) -> str:
        return f"asset:{self.id}"


@dataclass
class ShotInput:
    shot_id: int
    label: str
    planned_sec: float
    video: AssetRef | None = None   # a clip
    image: AssetRef | None = None   # the keyframe, used when there is no clip
    lines: list[tuple[str, AssetRef | None]] = field(default_factory=list)   # (text, speech asset or None)
    narration: str = ""
    sfx: list[tuple[AssetRef, float]] = field(default_factory=list)          # (asset, offset in shot)


@dataclass
class TimelineInput:
    shots: Sequence[ShotInput]
    profile: Mapping[str, Any]
    mix: Mapping[str, Any]
    subtitles: Mapping[str, Any] = field(default_factory=dict)
    bgm: AssetRef | None = None
    theme: AssetRef | None = None
    title: str = ""
    label: Mapping[str, Any] | None = None       # {"text": ..., "mode": "overlay" | "both"} - the visible AIGC label
    metadata: Mapping[str, str] = field(default_factory=dict)   # container metadata (implicit AIGC marking)


Timeline = dict[str, Any]


def _fit_video(clip: float | None, target: float, max_slowdown: float) -> dict[str, Any]:
    """How to make a ``clip``-second video last ``target`` seconds."""
    if not clip or clip <= 0:
        return {"mode": "hold", "speed": 1.0, "trim": target}
    if clip >= target:
        return {"mode": "trim", "speed": 1.0, "trim": round(target, 3)}
    speed = min(target / clip, max_slowdown)           # >1 means slower
    played = clip * speed
    return {"mode": "slow" if speed > 1.001 else "hold", "speed": round(speed, 4),
            "trim": round(min(played, target), 3), "hold_after": round(max(target - played, 0.0), 3)}


def build_timeline(inp: TimelineInput) -> Timeline:
    mix, prof = inp.mix, inp.profile
    lead = float(mix.get("lead_in_sec", 0.4))
    max_slow = float(mix.get("max_slowdown", 2.2))
    segments: list[dict[str, Any]] = []
    subtitles: list[dict[str, Any]] = []
    speech: list[dict[str, Any]] = []
    sfx: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    cursor = 0.0

    for shot in inp.shots:
        t = lead
        lines_out: list[dict[str, Any]] = []
        for i, (text, asset) in enumerate(shot.lines):
            if asset is None or not asset.duration_sec:
                warnings.append({"code": "missing_speech", "shot_id": shot.shot_id, "line": i})
                # still show the words, for a readable reading time
                dur = max(1.2, 0.18 * len(text))
                lines_out.append({"text": text, "asset": None, "offset": round(t, 3), "duration": round(dur, 3)})
                t += dur + GAP_SEC
                continue
            lines_out.append({"text": text, "asset": asset.ref, "offset": round(t, 3), "duration": asset.duration_sec})
            t += asset.duration_sec + GAP_SEC
        spoken_end = (t - GAP_SEC + TAIL_SEC) if lines_out else 0.0
        duration = max(shot.planned_sec, spoken_end, MIN_SHOT_SEC)

        seg: dict[str, Any] = {"shot_id": shot.shot_id, "label": shot.label, "start": round(cursor, 3),
                               "duration": round(duration, 3)}
        if shot.video is not None:
            fit = _fit_video(shot.video.duration_sec, duration, max_slow)
            seg["source"] = {"type": "video", "asset": shot.video.ref, **fit}
        elif shot.image is not None:
            seg["source"] = {"type": "image", "asset": shot.image.ref, "mode": "kenburns"}
            warnings.append({"code": "no_video", "shot_id": shot.shot_id})
        else:
            seg["source"] = {"type": "black"}
            warnings.append({"code": "no_picture", "shot_id": shot.shot_id})
        segments.append(seg)

        for ln in lines_out:
            start = cursor + ln["offset"]
            if ln["asset"]:
                speech.append({"asset": ln["asset"], "start": round(start, 3), "duration": ln["duration"]})
            if ln["text"].strip():
                end = round(start + ln["duration"], 3)
                subtitles.append({"start": round(start, 3), "end": end, "text": ln["text"]})
        if shot.narration.strip() and not lines_out:
            subtitles.append({"start": round(cursor + lead, 3), "end": round(cursor + min(duration, lead + 3.5), 3),
                              "text": shot.narration})
        for asset, offset in shot.sfx:
            sfx.append({"asset": asset.ref, "start": round(cursor + offset, 3), "duration": asset.duration_sec})
        cursor += duration

    label_block: dict[str, Any] | None = None
    if inp.label and inp.label.get("text"):
        label_block = {"text": str(inp.label["text"]), "mode": str(inp.label.get("mode", "overlay"))}
        if label_block["mode"] == "both":                    # an end card: the label alone on black for two seconds
            label_block["card_start"] = round(cursor, 3)
            segments.append({"shot_id": 0, "label": "card", "start": round(cursor, 3), "duration": END_CARD_SEC,
                             "source": {"type": "black"}})
            cursor += END_CARD_SEC
    total = round(cursor, 3)
    if label_block is not None:
        label_block["total"] = total
    music: list[dict[str, Any]] = []
    if inp.bgm is not None:
        music.append({"asset": inp.bgm.ref, "start": 0.0, "duration": total, "loop": True,
                      "gain": float(mix.get("bgm_gain", 0.16))})
    else:
        warnings.append({"code": "no_bgm"})
    return {
        "version": 1, "title": inp.title, "total_sec": total,
        "video": {"width": int(prof["width"]), "height": int(prof["height"]), "fps": int(prof["fps"]),
                  "codec": prof.get("video_codec", "libx264"), "crf": int(prof.get("crf", 18)),
                  "pix_fmt": prof.get("pix_fmt", "yuv420p")},
        "audio": {"codec": prof.get("audio_codec", "aac"), "bitrate": prof.get("audio_bitrate", "192k"),
                  "rate": int(prof.get("audio_rate", 48000)), "voice_gain": float(mix.get("voice_gain", 1.0)),
                  "sfx_gain": float(mix.get("sfx_gain", 0.5)),
                  "loudnorm": {"i": float(mix.get("loudnorm_i", -16.0)), "tp": float(mix.get("loudnorm_tp", -1.5)),
                               "lra": float(mix.get("loudnorm_lra", 11.0))}},
        "subtitles": {**dict(inp.subtitles), "cues": subtitles},
        "segments": segments, "speech": speech, "sfx": sfx, "music": music, "warnings": warnings,
        "label": label_block, "metadata": dict(inp.metadata),
    }
