"""Prompt dialects: how a spec's prompt is phrased for a given model (``ModelSpec.prompt_profile``).

Models disagree on prompt style (H3 wants Scene/Action/Camera/Look/Audio blocks; ACE-Step wants tags and
lyrics separately; most others take free text). Adding a dialect = one function in ``PROFILES``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from storyagent.config import ConfigError
from storyagent.domain.errors import ErrorCode
from storyagent.util import cjk_words

from .contracts import ValidationIssue

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


# Kinds whose prompt never holds speech: Chinese in it is a leak from the story text, not a spoken line (the sound
# clips legitimately carry a Mandarin line in their prompt, so they are only warned about at enqueue time).
_STRICT_KINDS = frozenset({"image.generate", "image.edit", "video.generate"})


def language_issues(prompt_language: str, kind: str, inputs: Mapping[str, Any]) -> list[ValidationIssue]:
    """An English-only picture or video model must not be sent Chinese words: it draws something else (a leaked word
    turned a clothed street scene into a naked one), so the job stops here and says which words."""
    if prompt_language != "en" or kind not in _STRICT_KINDS:
        return []
    found: list[str] = []
    for key in ("prompt", "negative_prompt"):
        found += cjk_words(str(inputs.get(key) or ""))
    if not found:
        return []
    words = ", ".join(dict.fromkeys(found[:5]))
    return [ValidationIssue(ErrorCode.INVALID_INPUT, f"the prompt contains Chinese ({words}) but the model reads "
                            "English only: write the prompt in English", {"detail": words})]


def render_prompt(profile: str, prompt: str, structured: Mapping[str, Any] | None = None) -> str:
    try:
        return PROFILES[profile](prompt, structured)
    except KeyError:
        raise ConfigError(f"unknown prompt_profile {profile!r} (known: {sorted(PROFILES)})") from None
