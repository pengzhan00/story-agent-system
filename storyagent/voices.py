"""Voices for characters that have none: pick one from the library by gender, one per character, and put it on the
character and on its spoken lines.

A voice's gender comes from its tags (``female`` / ``male``), else from its description; a character's from its
``gender`` field, else from the words of its sheet. A character that cannot be told apart, or has no voice of its gender
left in the library, is reported, not guessed. Voices are only ever taken from the library, so their consent flag and
licence are the library item's own (a restricted project's gate still checks them when the line is made).
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storyagent.media.library import Library, LibraryItem
from storyagent.media.specs import SpecStore
from storyagent.story import StoryStore

FEMALE, MALE = "female", "male"
_FEMALE_LATIN = re.compile(r"\b(female|woman|women|girl|lady|she|her|hers)\b", re.I)
_MALE_LATIN = re.compile(r"\b(male|man|men|boy|gentleman|he|his|him)\b", re.I)
_WORDS = tomllib.loads((Path(__file__).parent / "voices.toml").read_text("utf-8"))
_FEMALE_CJK = tuple(_WORDS["female"]["cjk"])
_MALE_CJK = tuple(_WORDS["male"]["cjk"])
_NOISE = tuple(_WORDS["noise"]["cjk"])


def gender_of_text(text: str) -> str:
    """``female`` / ``male`` when the words lean clearly one way, else ``""``."""
    for noise in _NOISE:
        text = text.replace(noise, " ")
    f = len(_FEMALE_LATIN.findall(text)) + sum(text.count(c) for c in _FEMALE_CJK)
    m = len(_MALE_LATIN.findall(text)) + sum(text.count(c) for c in _MALE_CJK)
    if f == m:
        return ""
    return FEMALE if f > m else MALE


def character_gender(c: dict[str, Any]) -> str:
    own = gender_of_text(str(c.get("gender", "")))
    if own:
        return own
    sheet = " ".join(str(c.get(k, "")) for k in ("role", "appearance", "personality", "background", "voice_profile"))
    return gender_of_text(sheet)


def voice_gender(item: LibraryItem) -> str:
    for tag in item.tags:
        if tag.lower() in (FEMALE, MALE):
            return tag.lower()
    return gender_of_text(item.description)


@dataclass
class Assignment:
    character: str
    voice: str = ""
    lines: int = 0
    reason: str = ""            # when no voice was given: unknown_gender | no_voice_left


@dataclass
class AutoVoices:
    assigned: list[Assignment] = field(default_factory=list)
    unresolved: list[Assignment] = field(default_factory=list)


def _rank(item: LibraryItem) -> tuple[int, int, str]:
    """Voices made for real use before ones kept only to check the pipeline, adult ones before "young" ones, then by
    name, so the choice is stable."""
    desc = item.description.lower()
    return (1 if "pilot verification only" in desc else 0, 0 if "adult" in desc else 1, item.name)


def _may_be_young(item: LibraryItem) -> bool:
    """A voice whose speaker's age group reaches under 18 (``age:<14``, ``age:14-25``)."""
    return any(tag in ("age:<14", "age:14-25") for tag in item.tags)


def auto_assign(
    store: StoryStore, library: Library, specs: SpecStore, project_id: int, *, adults_only: bool = False,
) -> AutoVoices:
    """Give every character without a voice one from the library (a voice already on a character is kept), then put it
    on that character's spoken lines that have none (a locked line is left alone). ``adults_only`` (a gated rating)
    leaves out voices whose speaker may be under 18."""
    out = AutoVoices()
    voices = [v for v in library.list(role="voice") if v.meta.get("consent") and voice_gender(v)
              and not (adults_only and _may_be_young(v))]
    used = {str(c.get("voice_ref_audio", "")) for c in store.list_characters(project_id)}
    chars = store.list_characters(project_id)
    by_name = {c["name"]: c for c in chars}
    for c in chars:
        if str(c.get("voice_ref_audio", "")).strip():
            continue
        gender = character_gender(c)
        if not gender:
            out.unresolved.append(Assignment(c["name"], reason="unknown_gender"))
            continue
        pool = sorted((v for v in voices if voice_gender(v) == gender), key=_rank)
        pick = next((v for v in pool if v.ref not in used), pool[0] if pool else None)
        if pick is None:
            out.unresolved.append(Assignment(c["name"], reason="no_voice_left"))
            continue
        used.add(pick.ref)
        store.update_character(c["id"], {"voice_ref_audio": pick.ref, "voice_ref_text": str(pick.meta.get("text", "")),
                                         "voice_ref_consent": 1})
        by_name[c["name"]] = store.get_character(c["id"])
        out.assigned.append(Assignment(c["name"], pick.name))
    given = {a.character: a for a in out.assigned}
    for shot in store.list_shots(project_id):
        for idx, line in enumerate(shot["dialogue"]):
            who = by_name.get(line["character"])
            ref = str(who.get("voice_ref_audio", "")).strip() if who else ""
            spec = specs.current(project_id, "dialogue.line", f"{shot['id']}#{idx}")
            if not ref or spec is None or spec.params.get("voice_ref") \
                    or specs.is_locked(project_id, "dialogue.line", spec.subject_id):
                continue
            specs.edit(project_id, "dialogue.line", spec.subject_id, params_patch={"voice_ref": ref},
                       note="voice chosen automatically")
            if line["character"] in given:
                given[line["character"]].lines += 1
    return out


def _voice_value(character: dict[str, Any]) -> Any:
    """What a dialogue line should carry for this character: a library ref, a reference object, or none (the default
    voice). Mirrors the seeder so the two never disagree."""
    ref = str(character.get("voice_ref_audio", "")).strip()
    if not ref:
        return None
    if ref.startswith("library:"):
        return ref
    return {"audio": ref, "text": character.get("voice_ref_text", ""),
            "consent": bool(character.get("voice_ref_consent"))}


def propagate_voice(store: StoryStore, specs: SpecStore, project_id: int, character_id: int) -> int:
    """A character's voice changed: put the new voice on its spoken lines. Each save is a new spec version, so the
    old audio goes stale and is re-made on the next produce run. Returns how many lines changed."""
    char = store.get_character(character_id)
    name = str(char["name"])
    new = _voice_value(char)
    changed = 0
    for shot in store.list_shots(project_id):
        for idx, line in enumerate(shot["dialogue"]):
            if str(line.get("character", "")) != name:
                continue
            subject_id = f"{shot['id']}#{idx}"
            spec = specs.current(project_id, "dialogue.line", subject_id)
            if spec is None or specs.is_locked(project_id, "dialogue.line", subject_id):
                continue
            if spec.params.get("voice_ref") == new:
                continue
            specs.edit(project_id, "dialogue.line", subject_id, params_patch={"voice_ref": new}, note="voice changed")
            changed += 1
    return changed
