"""Prompt dialects: how a spec's prompt is phrased for a given model (``ModelSpec.prompt_profile``).

Models disagree on prompt style (H3 wants Scene/Action/Camera/Look/Audio blocks; ACE-Step wants tags and
lyrics separately; most others take free text). Adding a dialect = one function in ``PROFILES``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from storyagent.config import ConfigError

_H3_ORDER = (("scene", "Scene"), ("action", "Action"), ("camera", "Camera"), ("look", "Look"), ("audio", "Audio"))


def _plain(prompt: str, structured: Mapping[str, Any] | None) -> str:
    return prompt.strip()


def _h3_structured(prompt: str, structured: Mapping[str, Any] | None) -> str:
    if not structured:
        return prompt.strip()
    lines = [f"{label}: {str(structured[key]).strip()}" for key, label in _H3_ORDER if structured.get(key)]
    if prompt.strip():
        lines.insert(0, prompt.strip())
    return "\n".join(lines)


def _ltx_av(prompt: str, structured: Mapping[str, Any] | None) -> str:
    """LTX-2.5 makes picture and sound in one pass: the picture text first, then ``AUDIO:`` (sound design) and
    ``DIALOGUE in <language>:`` (the spoken line, kept verbatim). Keep the prompt well-lit and crisp; fog/dawn
    wording makes the model render dark and soft."""
    parts = [prompt.strip()] if prompt.strip() else []
    s = structured or {}
    for key in ("scene", "action", "camera", "look"):
        if s.get(key):
            parts.append(str(s[key]).strip())
    if s.get("audio"):
        parts.append(f"AUDIO: {str(s['audio']).strip()}")
    if s.get("dialogue"):
        parts.append(f"DIALOGUE in {s.get('dialogue_language', 'Mandarin')}: {str(s['dialogue']).strip()}")
    return " ".join(p for p in parts if p)


PROFILES: dict[str, Callable[[str, Mapping[str, Any] | None], str]] = {
    "ltx_av": _ltx_av,
    "plain": _plain,
    "natural_en": _plain,
    "cinematic_en": _plain,
    "music_tags": _plain,  # tags live in `prompt`, lyrics in `text_payload`; the adapter combines them
    "h3_structured": _h3_structured,
}


def render_prompt(profile: str, prompt: str, structured: Mapping[str, Any] | None = None) -> str:
    try:
        return PROFILES[profile](prompt, structured)
    except KeyError:
        raise ConfigError(f"unknown prompt_profile {profile!r} (known: {sorted(PROFILES)})") from None
