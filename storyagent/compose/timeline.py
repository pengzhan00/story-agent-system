"""Build the edit decision list (EDL) of one episode.

Pure and deterministic: given the shots, the assets that exist for them and the delivery/mix settings, return a JSON
document that the assemble runner executes. The order of work is *speech first*: dialogue durations are measured
(ffprobe, stored on the asset), only then is the shot as long as it needs to be - so nothing is ever cut mid-sentence
and the timeline is never built from a character-count guess.

Per shot: ``duration = max(planned, lead_in + sum(lines) + gaps + tail)``. The picture follows the duration: a video
clip that is too short is slowed down (up to ``max_slowdown``) and then holds its last frame; one that is too long is
trimmed; a still image gets a slow push-in (Ken Burns). A shot with no picture at all becomes a black card and a
warning - the episode still renders, and the gap is visible in the report.

Sound and edit grammar (all switchable, ``delivery.toml [mix]`` / ``[edit]``): a quiet room tone under every scene so
a pause is never dead silence; several music cues (one per run of scenes, cross-faded) instead of one bed; the music
swells a little where nobody speaks; a J-cut (the next line is heard before its picture) or an L-cut (the picture
changes while the last line is still being said) at a cut inside one scene; a cross-dissolve at a change of scene.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

GAP_SEC = 0.25          # silence between two lines of one shot
TAIL_SEC = 0.35         # breathing room after the last line
MIN_SHOT_SEC = 1.0
END_CARD_SEC = 2.0
CARD_FADE_SEC = 0.25


@dataclass(frozen=True)
class AssetRef:
    """What the timeline needs to know about one stored asset (never a path: the worker resolves ``asset:<id>``)."""

    id: str
    kind: str                       # image.generate | video.generate | audio.tts ...
    duration_sec: float | None = None

    @property
    def ref(self) -> str:
        return f"asset:{self.id}"


@dataclass(frozen=True)
class Card:
    """A text card shown before or after the story (episode title, "previously", "to be continued")."""

    kind: str                       # title | recap | next
    lines: tuple[str, ...]          # first line is the heading
    sec: float = 2.0


@dataclass(frozen=True)
class MusicCue:
    """A music bed that plays from the first shot of ``from_scene`` ("act.scene") until the next cue starts."""

    asset: AssetRef
    from_scene: str = ""


@dataclass
class ShotInput:
    shot_id: int
    label: str
    planned_sec: float
    video: AssetRef | None = None   # a clip
    video_audio: bool = False       # the clip carries its own sound (video.av): it is mixed in, no TTS needed
    image: AssetRef | None = None   # the keyframe, used when there is no clip
    lines: list[tuple[str, AssetRef | None]] = field(default_factory=list)   # (text, speech asset or None)
    narration: str = ""
    scene: str = ""                 # "act.scene": a change of scene gets a short fade when ``scene_fade_sec`` > 0
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
    intro_cards: Sequence[Card] = ()            # shown before the first shot, in order
    outro_cards: Sequence[Card] = ()            # shown after the last shot, before the AIGC end card
    scene_fade_sec: float = 0.0                 # fade out/in at a change of scene (0 = hard cut)
    music_cues: Sequence[MusicCue] = ()         # several beds; ``bgm`` is the one-bed shorthand
    dissolve_sec: float = 0.0                   # cross-dissolve at a change of scene (0 = none; wins over the fade)
    j_cut_sec: float = 0.0                      # a line is heard this long before its picture, inside a scene (0 = off)
    l_cut_sec: float = 0.0                      # the last line runs this long over the next picture (0 = off)


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

    def add_cards(cards: Sequence[Card]) -> None:
        nonlocal cursor
        for card in cards:
            segments.append({"shot_id": 0, "label": card.kind, "start": round(cursor, 3),
                             "duration": round(card.sec, 3), "fade_in": CARD_FADE_SEC, "fade_out": CARD_FADE_SEC,
                             "source": {"type": "card", "kind": card.kind, "lines": list(card.lines)}})
            cursor += card.sec

    add_cards(inp.intro_cards)
    story_start = len(segments)
    scene_of: list[str] = []
    meta: list[dict[str, Any]] = []         # per shot: what the cut rules need (segment, speech span, clip length)
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
        scene_of.append(shot.scene)
        meta.append({"i": len(segments) - 1, "scene": shot.scene, "start": round(cursor, 3),
                     "duration": round(duration, 3),
                     "clip": shot.video.duration_sec if shot.video is not None else None,
                     "video": shot.video.ref if shot.video is not None else None,
                     "first": lines_out[0]["offset"] if lines_out else None,
                     "last_end": round(lines_out[-1]["offset"] + lines_out[-1]["duration"], 3) if lines_out else None})

        for ln in lines_out:
            start = cursor + ln["offset"]
            if ln["asset"]:
                speech.append({"asset": ln["asset"], "start": round(start, 3), "duration": ln["duration"]})
            if ln["text"].strip():
                end = round(start + ln["duration"], 3)
                subtitles.append({"start": round(start, 3), "end": end, "text": ln["text"]})
        if shot.video is not None and shot.video_audio:
            # the clip carries its own sound: its audio track is the speech for this shot (no separate TTS line)
            speech.append({"asset": shot.video.ref, "start": round(cursor, 3), "duration": round(duration, 3),
                           "from_video": True})
        # The shot's own narration is a visual/action description for the prompter, not a spoken line: it must not
        # appear on screen as a subtitle (it made the finished film look like a wall of descriptive text).
        for asset, offset in shot.sfx:            # an effect ends with its shot (the mixer trims it to ``duration``)
            room = max(duration - offset, 0.0)
            length = min(asset.duration_sec, room) if asset.duration_sec else room
            sfx.append({"asset": asset.ref, "start": round(cursor + offset, 3), "duration": round(length, 3)})
        cursor += duration

    _adjust_cuts(inp, segments, meta, max_slow)
    fade, dissolve = float(inp.scene_fade_sec), float(inp.dissolve_sec)
    for i in range(1, len(scene_of)):
        if scene_of[i] == scene_of[i - 1]:
            continue
        before, after = segments[story_start + i - 1], segments[story_start + i]
        if dissolve > 0:                  # picture melts into the next scene; the runner overlaps the two shots
            d = round(min(dissolve, before["duration"] / 2, after["duration"] / 2), 3)
            if d > 0:
                before["dissolve_out"] = d
        elif fade > 0:                    # dip through black at a change of scene (timing is unchanged)
            before["fade_out"] = fade
            after["fade_in"] = fade
    add_cards(inp.outro_cards)

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
    music = _music(inp, mix, meta, segments, total, warnings)
    swell = _swell(mix, speech, total) if music else None
    tone = _room_tone(mix, meta, segments)
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
        "segments": segments, "speech": speech, "sfx": sfx, "music": music, "music_swell": swell,
        "room_tone": tone, "warnings": warnings, "label": label_block, "metadata": dict(inp.metadata),
    }


def _adjust_cuts(inp: TimelineInput, segments: list[dict[str, Any]], meta: list[dict[str, Any]],
                 max_slow: float) -> None:
    """J- and L-cuts. Inside one scene the picture cut moves relative to the sound: a J-cut lets the next shot's first
    line start ``j_cut_sec`` before its picture; an L-cut lets the last line of a shot run ``l_cut_sec`` over the next
    picture. The sound never moves (it is placed by absolute time); only the two neighbouring segments trade seconds, so
    the total length is unchanged. A cut that would leave a shot shorter than MIN_SHOT_SEC is left alone."""
    j, l_cut = float(inp.j_cut_sec), float(inp.l_cut_sec)
    if (j <= 0 and l_cut <= 0) or len(meta) < 2:
        return
    before: dict[int, float] = {}       # segment index -> seconds the cut in front of it moved later
    after: dict[int, float] = {}        # ... the cut behind it moved later
    kind: dict[int, str] = {}
    for a, b in zip(meta, meta[1:], strict=False):
        if a["scene"] != b["scene"]:
            continue
        ia, ib = a["i"], b["i"]
        if j > 0 and b["first"] is not None:
            delta, tag = b["first"] + j, "j"
        elif l_cut > 0 and a["last_end"] is not None:
            delta, tag = -((a["duration"] - a["last_end"]) + l_cut), "l"
        else:
            continue
        long_a = a["duration"] - before.get(ia, 0.0) + delta
        long_b = b["duration"] - delta
        if long_a < MIN_SHOT_SEC or long_b < MIN_SHOT_SEC:
            continue
        after[ia], before[ib], kind[ib] = delta, delta, tag
    for m in meta:
        i = m["i"]
        if i not in before and i not in after:
            continue
        seg = segments[i]
        seg["start"] = round(m["start"] + before.get(i, 0.0), 3)
        seg["duration"] = round(m["duration"] - before.get(i, 0.0) + after.get(i, 0.0), 3)
        if i in kind:
            seg["cut_in"] = kind[i]
        if m["video"] is not None:
            seg["source"] = {"type": "video", "asset": m["video"], **_fit_video(m["clip"], seg["duration"], max_slow)}


def _music(inp: TimelineInput, mix: Mapping[str, Any], meta: Sequence[Mapping[str, Any]],
           segments: Sequence[Mapping[str, Any]], total: float, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gain = float(mix.get("bgm_gain", 0.16))
    if not inp.music_cues:
        if inp.bgm is None:
            warnings.append({"code": "no_bgm"})
            return []
        return [{"asset": inp.bgm.ref, "start": 0.0, "duration": total, "loop": True, "gain": gain}]
    first_start: dict[str, float] = {}
    for m in meta:
        first_start.setdefault(m["scene"], float(segments[m["i"]]["start"]))
    placed: list[tuple[float, MusicCue]] = []
    for n, cue in enumerate(inp.music_cues):
        at = 0.0 if n == 0 else first_start.get(cue.from_scene)
        if at is None:
            warnings.append({"code": "music_cue_scene_missing", "scene": cue.from_scene})
            continue
        if not placed or at > placed[-1][0] + 1e-6:          # two cues on the same scene: the first one stays
            placed.append((at, cue))
    xfade = max(float(mix.get("music_crossfade_sec", 1.5)), 0.0)
    out: list[dict[str, Any]] = []
    for k, (at, cue) in enumerate(placed):
        end = placed[k + 1][0] if k + 1 < len(placed) else total
        x = min(xfade, (end - at) / 2)
        last = k + 1 == len(placed)
        start = max(at - (x / 2 if k else 0.0), 0.0)
        stop = min(end + (0.0 if last else x / 2), total)
        out.append({"asset": cue.asset.ref, "start": round(start, 3), "duration": round(stop - start, 3),
                    "loop": True, "gain": gain, "fade_in": round(x if k else 0.0, 3),
                    "fade_out": round(min(1.0, total / 4) if last else x, 3)})
    return out


def _swell(mix: Mapping[str, Any], speech: Sequence[Mapping[str, Any]], total: float) -> dict[str, Any] | None:
    """Where nobody speaks for a while the music comes up a little (``music_swell_db``); under speech it ducks."""
    db = float(mix.get("music_swell_db", 0.0))
    if db <= 0 or not speech:
        return None
    margin, minimum = 0.5, float(mix.get("music_swell_min_sec", 2.0))
    spans = sorted((float(s["start"]), float(s["start"]) + float(s["duration"])) for s in speech)
    edges = [0.0, *[x for span in spans for x in span], total]
    gaps = [(a + margin, b - margin) for a, b in zip(edges[::2], edges[1::2], strict=False)
            if b - a - 2 * margin >= minimum]
    return {"db": db, "ramp": 0.4, "intervals": [[round(a, 3), round(b, 3)] for a, b in gaps]} if gaps else None


def _room_tone(mix: Mapping[str, Any], meta: Sequence[Mapping[str, Any]],
               segments: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """A faint noise bed under every run of shots of one scene: a pause between lines is room, not digital silence."""
    if not mix.get("room_tone"):
        return None
    blocks: list[dict[str, Any]] = []
    for m in meta:
        seg = segments[m["i"]]
        if blocks and blocks[-1]["scene"] == m["scene"]:
            blocks[-1]["duration"] = round(float(seg["start"]) + float(seg["duration"]) - blocks[-1]["start"], 3)
        else:
            blocks.append({"scene": m["scene"], "start": float(seg["start"]), "duration": float(seg["duration"])})
    return {"db": float(mix.get("room_tone_db", -50.0)), "color": str(mix.get("room_tone_color", "brown")),
            "fade": 0.4, "blocks": blocks}
