"""Script -> shot rows. Deterministic (no LLM): the creative decisions were made by the script stage and can be edited
shot by shot afterwards. Shot contract ``shot.v3``: one self-contained payload per shot so that media specs, the
timeline and QC never have to guess which fields to read."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .continuity import look_for, props_in

SHOT_SCHEMA = "shot.v3"
# In-shot staging (optional, written by the script stage or by hand): where the person starts and ends, how they move,
# where the camera stands, the lens and which way they face on screen (left | right | center).
STAGING_FIELDS = ("start_pose", "end_pose", "gait", "camera_position", "lens_mm", "screen_direction")
DEFAULT_NEGATIVE = (
    "low quality, blurry, bad anatomy, extra fingers, duplicated person, broken face, text, watermark, logo, "
    "inconsistent costume, flat lighting, overexposed, underexposed"
)
_CJK_RANGES = ((0x3400, 0x9FFF), (0x3040, 0x30FF), (0xAC00, 0xD7AF))  # Han, kana, hangul (spoken-speed heuristic)
_CJK = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _CJK_RANGES) + "]")


def estimate_speech_sec(text: str) -> float:
    """Rough spoken duration, only to size shots before the real voice-over exists (the measured length replaces it)."""
    text = text.strip()
    if not text:
        return 0.0
    cjk = len(_CJK.findall(text))
    words = len(re.findall(r"[A-Za-z0-9']+", text))
    return round(cjk / 4.5 + words / 2.6 + 0.4, 2)


def normalize_dialogue(lines: Sequence[Mapping[str, Any]] | None) -> list[dict[str, str]]:
    out = []
    for line in lines or []:
        text = str(line.get("line", "")).strip()
        if text:
            out.append({"character": str(line.get("character", "")).strip(), "line": text,
                        "emotion": str(line.get("emotion", "")).strip()})
    return out


def shot_duration(
    shot: Mapping[str, Any], dialogue: Sequence[Mapping[str, str]], minimum: float, maximum: float
) -> float:
    asked = float(shot.get("duration_sec") or 0)
    spoken = sum(estimate_speech_sec(d["line"]) for d in dialogue) + 0.3 * max(0, len(dialogue) - 1)
    return round(min(max(asked, spoken + 0.5, minimum), maximum), 2)


def build_payload(
    *, scene: Mapping[str, Any], shot: Mapping[str, Any], dialogue: list[dict[str, str]],
    location: Mapping[str, Any] | None, characters: Mapping[str, Mapping[str, Any]], style: Mapping[str, Any],
    profile: Mapping[str, Any], scene_id: str, duration: float, previous: str,
    looks: Sequence[Mapping[str, Any]] = (), props: Sequence[Mapping[str, Any]] = (),
    position: tuple[int, int, int] = (1, 1, 1),
) -> dict[str, Any]:
    present = [n for n in (shot.get("characters_present") or scene.get("characters") or []) if str(n).strip()]
    subject = []
    for name in present:
        c = characters.get(str(name).strip())
        look = look_for(looks, int(c["id"]), *position) if c and "id" in c else None
        subject.append({
            "name": str(name).strip(), "appearance": (c or {}).get("appearance", ""),
            "costume": str(look["costume"]) if look else "", "look": str(look["label"]) if look else "",
            "voice_profile": (c or {}).get("voice_profile", ""),
            "prompt_template": (c or {}).get("prompt_template", ""),
            "voice_ref": (c or {}).get("voice_ref_audio", ""), "lora_ref": (c or {}).get("lora_ref", ""),
            "ref_images": (c or {}).get("ref_images", []),
        })
    mood = str(shot.get("mood") or scene.get("mood") or "")
    loc = location or {}
    return {
        "schema_version": SHOT_SCHEMA, "scene_id": scene_id,
        "subject": {"characters": subject, "action": str(shot.get("action", "")),
                    "expression": str(shot.get("expression", "")), "emotion": mood},
        "scene": {"location": str(scene.get("location", "")), "time_of_day": str(scene.get("time_of_day", "")),
                  "weather": str(scene.get("weather", "")), "lighting": str(loc.get("lighting", "")),
                  "atmosphere": str(loc.get("atmosphere", "")), "description": str(loc.get("description", ""))},
        "story": {"beat": str(scene.get("beat", "")), "mood": mood,
                  "narration": str(shot.get("narration") or scene.get("narration") or ""), "dialogue": dialogue},
        "camera": {"shot_type": str(shot.get("camera_angle", "")), "movement": str(shot.get("camera_movement", "")),
                   "duration_sec": duration},
        "staging": {k: str(shot.get(k) or "").strip() for k in STAGING_FIELDS},
        "audio": {"bgm_mood": str(scene.get("bgm_mood") or mood), "tts_required": bool(dialogue),
                  "sfx_cues": [str(c) for c in shot.get("sfx_cues", [])]},
        "style": {"visual_style": str(style.get("visual_style", "")),
                  "negative_prompt": str(style.get("negative_prompt") or DEFAULT_NEGATIVE)},
        "continuity": {"character_anchor": "|".join(sorted(str(n).strip() for n in present)),
                       "scene_anchor": str(scene.get("location", "")), "previous_shot_summary": previous,
                       "props": props_in(props, [str(shot.get("action", "")), str(shot.get("expression", "")),
                                                 str(shot.get("narration") or scene.get("narration") or ""),
                                                 str(scene.get("beat", "")), *(d["line"] for d in dialogue),
                                                 *(str(c) for c in shot.get("sfx_cues", []))], position[0])},
        "output_spec": {"width": int(profile.get("width", 0)), "height": int(profile.get("height", 0)),
                        "fps": int(profile.get("fps", 0))},
    }


def build_shot_rows(
    script: Mapping[str, Any], *, characters: Sequence[Mapping[str, Any]], locations: Sequence[Mapping[str, Any]],
    style: Mapping[str, Any], profile: Mapping[str, Any], min_shot_sec: float = 2.0, max_shot_sec: float = 8.0,
    looks: Sequence[Mapping[str, Any]] = (), props: Sequence[Mapping[str, Any]] = (), episode: int = 1,
) -> list[dict[str, Any]]:
    by_char = {str(c["name"]).strip(): c for c in characters}
    by_loc = {str(loc["name"]).strip(): loc for loc in locations}
    rows: list[dict[str, Any]] = []
    previous = ""
    for act in script.get("acts", []):
        act_no = int(act.get("number", 1))
        for scene in act.get("scenes", []):
            scene_no = int(scene.get("number", 1))
            shots = scene.get("shots") or [
                {"shot_number": 1, "camera_angle": "medium", "narration": scene.get("narration", "")}
            ]
            for i, shot in enumerate(shots, start=1):
                number = int(shot.get("shot_number") or i)
                dialogue = normalize_dialogue(shot.get("dialogue"))
                duration = shot_duration(shot, dialogue, min_shot_sec, max_shot_sec)
                scene_id = f"act{act_no:02d}_scene{scene_no:02d}_shot{number:02d}"
                payload = build_payload(
                    scene=scene, shot=shot, dialogue=dialogue,
                    location=by_loc.get(str(scene.get("location", "")).strip()), characters=by_char, style=style,
                    profile=profile, scene_id=scene_id, duration=duration, previous=previous,
                    looks=looks, props=props, position=(episode, act_no, scene_no),
                )
                present = [str(n).strip() for n in (shot.get("characters_present") or scene.get("characters") or [])
                           if str(n).strip()]
                rows.append({
                    "script_id": script.get("id"), "act_number": act_no, "scene_number": scene_no,
                    "shot_number": number,
                    "location": str(scene.get("location", "")), "shot_type": str(shot.get("camera_angle", "")),
                    "mood": payload["story"]["mood"], "time_of_day": str(scene.get("time_of_day", "")),
                    "weather": str(scene.get("weather", "")), "characters": present,
                    "narration": payload["story"]["narration"], "dialogue": dialogue,
                    "camera_notes": str(shot.get("camera_movement", "")), "visual_prompt": "",
                    "payload_version": SHOT_SCHEMA, "render_payload": payload, "status": "ready",
                })
                previous = payload["story"]["narration"] or str(shot.get("action", "")) or str(scene.get("beat", ""))
    return rows
