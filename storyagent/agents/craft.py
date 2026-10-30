"""What the writing agents are told about the form (kind of work) and the genre of the thing they write.

The rules are data (``kinds.toml`` ``writing`` / ``dialogue_chars`` / ``shot_sec``, ``genres.toml``), never prompt text
in code; this module only looks them up and phrases the numbers, so a prompt names ``$form_guidance`` and friends.
"""
from __future__ import annotations

from storyagent.config import ConfigError, Settings, WorkKind

NONE = "(none)"


def resolve_kind(settings: Settings, name: str | None) -> WorkKind:
    """The kind a project works to. A project without one keeps the default kind's rules; ``None`` (the workshop,
    no project) gets the neutral ``custom`` kind when it exists."""
    if name is None:
        return settings.kinds.get("custom") or settings.kind()
    try:
        return settings.kind(name)
    except ConfigError:
        return settings.kind()


def shot_bounds(settings: Settings, name: str | None) -> tuple[float, float]:
    kind = resolve_kind(settings, name)
    return kind.shot_sec.min, kind.shot_sec.max


def writing_vars(settings: Settings, kind_name: str | None, genre: str = "",
                 vertical: bool | None = None) -> dict[str, str]:
    """Prompt variables ``form_guidance``, ``genre_guidance``, ``dialogue_chars``, ``shot_sec``, ``frame_note``."""
    kind = resolve_kind(settings, kind_name)
    shot = kind.shot_sec
    if vertical is None:
        frame = NONE
    elif vertical:
        frame = ("Vertical (portrait) frame: one subject at a time, faces large, simple tall compositions; "
                 "avoid shots that need a wide horizontal spread.")
    else:
        frame = ("Landscape frame: wide compositions, two or more people in one shot are fine, "
                 "use the width for place and blocking.")
    return {
        "form_guidance": kind.writing.strip() or NONE,
        "genre_guidance": settings.genres.guidance_for(genre) or NONE,
        "dialogue_chars": str(kind.dialogue_chars),
        "shot_sec": f"between {shot.min:g} and {shot.max:g} seconds, usually about {shot.default:g}",
        "frame_note": frame,
    }
