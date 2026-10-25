"""Guardrails for image prompts: the same ethnicity every time, natural skin, a readable exposure.

The style, character and shot prompts are written by a language model and can say anything ("wet skin texture", "deep
shadows"). This module rewrites the words that make a picture greasy or murky and adds the identity and realism
clauses, so the result does not depend on how the model happened to phrase them. Pure functions; the wording is data
(``keyframe.toml``).
"""
from __future__ import annotations

import hashlib
import re
import tomllib
from functools import lru_cache
from importlib import resources
from typing import Any


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    return tomllib.loads(resources.files("storyagent.workflow").joinpath("keyframe.toml").read_text("utf-8"))


def guide(*, with_references: bool = False) -> str:
    """What the image tool can and cannot do, for the agent that writes the prompts."""
    return " ".join(str(_data()["guides"]["edit" if with_references else "direct"]).split())


def _for_language(table: dict[str, str], language: str) -> str:
    code = (language or "").lower()
    return next((v for k, v in table.items() if code.startswith(k)), "")


def clean(text: str) -> str:
    """Rewrite the words that make skin look oily or the frame murky."""
    for rule in _data()["rewrite"]:
        text = re.sub(rule["pattern"], rule["replace"], text, flags=re.IGNORECASE)
    return text


def image_prompt(prompt: str, *, language: str = "", with_portraits: bool = False) -> str:
    """A prompt for a picture with people in it: cleaned, then who they are and how it should look."""
    data = _data()
    parts = [clean(prompt).strip().rstrip(".,; ")]
    who = _for_language(data["identity"], language)
    if who:
        parts.append(who)
    if with_portraits:
        parts.append(data["same_faces"])
    parts.append(data["realism"])
    return ", ".join(p for p in parts if p)


def scene_prompt(prompt: str) -> str:
    """A prompt for an empty place: cleaned, and kept bright and open."""
    return ", ".join(p for p in (clean(prompt).strip().rstrip(".,; "), _data()["realism"]) if p)


def negative_prompt(negative: str, *, language: str = "") -> str:
    """The style's own negatives minus the ones that hurt, plus what must never show up."""
    data = _data()
    drop = {d.lower() for d in data["negative"]["drop"]}
    kept = [t.strip() for t in negative.split(",") if t.strip() and t.strip().lower() not in drop]
    extra = [data["negative"]["always"]]
    if _for_language(data["identity"], language):
        extra.append(_for_language(data["negative"], language))
    seen: set[str] = set()
    out = []
    for token in [*kept, *(t.strip() for e in extra for t in e.split(","))]:
        if token and token.lower() not in seen:
            seen.add(token.lower())
            out.append(token)
    return ", ".join(out)


def fixed_seed(project_id: int, key: str) -> int:
    """A seed that never changes for (project, key): the same character gets the same start noise in every picture,
    which keeps the face close from one shot to the next (a help, not a guarantee, without reference images)."""
    digest = hashlib.sha256(f"{project_id}:{key}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % 2_000_000_000
