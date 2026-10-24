"""A script (acts > scenes > shots) as plain Markdown text, and its counts: for reading, copying and downloading.

Labels come from the caller's translator (``script.text.*`` keys), so the text follows the reader's language.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

Translate = Callable[..., str]


def counts(acts: list[dict[str, Any]]) -> tuple[int, int, int]:
    """(scenes, shots, dialogue lines)."""
    scenes = shots = lines = 0
    for act in acts:
        for scene in act.get("scenes", []):
            scenes += 1
            for shot in scene.get("shots", []):
                shots += 1
                lines += len([d for d in shot.get("dialogue", []) if isinstance(d, dict) and d.get("line")])
    return scenes, shots, lines


def _join(parts: list[object], sep: str = " · ") -> str:
    return sep.join(str(p) for p in parts if p)


def render(title: str, synopsis: str, acts: list[dict[str, Any]], t: Translate, *, heading: str = "") -> str:
    out: list[str] = [f"# {heading or title}"]
    if heading and title and title != heading:
        out.append(f"**{title}**")
    if synopsis:
        out += ["", f"> {synopsis}"]
    for act in acts:
        out += ["", f"## {t('script.text.act', n=act.get('number', ''))}"]
        for scene in act.get("scenes", []):
            head = _join([t("script.text.scene", n=scene.get("number", "")), scene.get("location"),
                          scene.get("time_of_day"), scene.get("weather"), scene.get("mood")])
            out += ["", f"### {head}"]
            if scene.get("beat"):
                out.append(str(scene["beat"]))
            if scene.get("narration"):
                out.append(f"*{t('script.text.narration')}:* {scene['narration']}")
            for shot in scene.get("shots", []):
                seconds = t("script.text.seconds", n=shot["duration_sec"]) if shot.get("duration_sec") else ""
                out += ["", "**" + _join([t("script.text.shot", n=shot.get("shot_number", "")),
                                          shot.get("camera_angle"), shot.get("camera_movement"), seconds]) + "**"]
                if shot.get("action"):
                    out.append(str(shot["action"]))
                if shot.get("narration"):
                    out.append(f"*{t('script.text.narration')}:* {shot['narration']}")
                for d in shot.get("dialogue", []):
                    if isinstance(d, dict) and d.get("line"):
                        who = str(d.get("character", ""))
                        mood = f" ({d['emotion']})" if d.get("emotion") else ""
                        out.append(f"- **{who}**{mood}: {d['line']}")
    return "\n".join(out) + "\n"
