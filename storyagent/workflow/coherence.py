"""Keeping the story in one piece: what the writers are told about the rest of the story, and the checks that send an
outline or a scene back before anything is drawn.

Three things went wrong in dramas made without this (``docs/reviews``: a villain designed but never on screen, a parcel
whose sender is never told, a woman who is "just there" at the beach, a line said one shot before it is true):

* the outline was never read by anyone before the shots were written from it;
* the shot writer of scene 7 was told one sentence about scene 6 and nothing about scenes 1-5;
* a revision could only rewrite shots, so a hole in the outline stayed.

So: the outline states its ``facts`` (what is true in the story, every secret with its answer) and ``threads`` (each
thing planted and the scene that pays it off); ``outline_problems`` (plain code) and the ``reviewer.outline`` agent
read it, and the outline is written again with their notes up to ``[workflow] outline_review_rounds`` times; every
scene is written knowing the whole story so far, the last lines spoken and the facts; and after the shots are written
the script editor reads the script once more and the scenes with a serious problem are written again
(``script_review_rounds``). What is left open is kept in the stage summary and under ``story_check:<episode>`` in the
workflow data, so a person sees it instead of finding it in the film.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

SEVERE = "high"
_WHERE = re.compile(r"(\d+)\s*[.\-/:：,，]\s*(\d+)")
# "Act 2 / Scene 1", or the Chinese form "<nth> 2 <act> <nth> 1 <scene>" (the three words as escapes: no CJK in code)
_NTH, _ACT, _SCENE = chr(0x7B2C), chr(0x5E55), chr(0x573A)
_WHERE_WORDS = re.compile(
    r"act\D{0,3}(\d+)\D{0,12}?scene\D{0,3}(\d+)|" + _NTH + r"\s*(\d+)\s*" + _ACT + r"\D{0,6}?(\d+)\s*" + _SCENE,
    re.IGNORECASE)
SO_FAR_CHARS = 3200        # the story so far given to a shot writer is cut to this (the latest scenes are kept whole)
BEAT_CHARS = 260


def scene_of(where: Any) -> str | None:
    """``"2.1"`` out of a reviewer's free-text place (``"2.1"``, ``"Act 2 / Scene 1 / shot 3"``, ``"第2幕第1场"``,
    ``"2.1.3"``); ``None`` when it names no scene (a note about the whole piece)."""
    text = str(where or "")
    found = _WHERE_WORDS.search(text)
    if found:
        a, s = (found.group(1), found.group(2)) if found.group(1) else (found.group(3), found.group(4))
        return f"{int(a)}.{int(s)}"
    found = _WHERE.search(text)
    return f"{int(found.group(1))}.{int(found.group(2))}" if found else None


def scene_list(outline: Mapping[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(act, sc) for act in outline.get("acts", []) for sc in act.get("scenes", [])]


def _key(act: Mapping[str, Any], scene: Mapping[str, Any]) -> str:
    return f"{act.get('number')}.{scene.get('number')}"


def _issue(code: str, where: str, problem: str, fix: str, severity: str = SEVERE) -> dict[str, str]:
    return {"severity": severity, "where": where, "problem": problem, "fix": fix, "lens": "structure", "code": code}


def outline_problems(outline: Mapping[str, Any], cast: Sequence[str], *, must_use_all: bool = True,
                     closed_cast: bool = True) -> list[dict[str, str]]:
    """What plain code can tell is wrong with an outline. ``cast`` are the names the writer was given.

    * a person of the cast who is in no scene (``must_use_all``: the first episode; a later one need not use everyone)
    * a person in a scene who is not in the cast (``closed_cast``: the writer was told to bring in nobody else)
    * a scene without a beat
    * a thread whose pay-off names no scene, a scene that does not exist, or one that is not after its set-up
    """
    scenes = scene_list(outline)
    order = {_key(a, s): n for n, (a, s) in enumerate(scenes)}
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for act, sc in scenes:
        where = _key(act, sc)
        people = [str(n).strip() for n in sc.get("characters") or [] if str(n).strip()]
        seen.update(people)
        if not str(sc.get("beat") or "").strip():
            out.append(_issue("empty_beat", where, "the scene has no beat: nothing is said to happen in it",
                              "say who wants what, what gets in the way and what has changed at the end"))
        for name in people:
            if closed_cast and name not in cast:
                out.append(_issue("unknown_character", where, f"{name} is in this scene but is not one of the cast",
                                  "use one of the listed characters, or leave the person out", "medium"))
    if must_use_all:
        for name in cast:
            if name not in seen:
                out.append(_issue(
                    "unused_character", "whole", f"{name} is in the cast but appears in no scene",
                    f"give {name} a scene where what {name} does changes what happens next (shown, not reported)"))
    for thread in outline.get("threads") or []:
        if not isinstance(thread, Mapping):
            continue
        setup, payoff = scene_of(thread.get("setup_scene")), scene_of(thread.get("payoff_scene"))
        what = str(thread.get("setup") or "").strip() or "something planted"
        if payoff is None or payoff not in order:
            out.append(_issue("thread_open", setup or "whole", f"planted and never paid off: {what}",
                              "name the scene where the viewer gets the answer (or a clear pointer to it), "
                              "or do not plant it"))
        elif setup in order and order[payoff] <= order[setup]:
            out.append(_issue("thread_order", payoff, f"paid off before it is planted: {what}",
                              "plant it in an earlier scene than the one that answers it"))
    return out


def notes_text(issues: Sequence[Mapping[str, Any]]) -> str:
    """Review issues as the editor's notes handed back to a writer."""
    return "\n".join(f"[{i.get('where') or 'whole'}] {i.get('problem', '')}"
                     + (f" -> {i['fix']}" if i.get("fix") else "") for i in issues) or "(none)"


def severe(issues: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(i) for i in issues if str(i.get("severity") or "").strip().lower() == SEVERE]


def facts_text(facts: Any) -> str:
    items = [str(f).strip() for f in facts or [] if str(f).strip()] if isinstance(facts, list) else []
    return "\n".join(f"- {f}" for f in items) or "(none)"


def story_so_far(scenes: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]], index: int, lead: str = "") -> str:
    """Every scene before ``scenes[index]`` in one line each (where, when, who, what happened); ``lead`` (earlier
    episodes) goes first. Too long: the oldest lines are shortened first, the last three scenes are kept whole."""
    lines = []
    for act, sc in scenes[:index]:
        who = ", ".join(str(n) for n in sc.get("characters") or [])
        head = ", ".join(str(x) for x in (sc.get("location"), sc.get("time_of_day")) if x)
        lines.append(f"{_key(act, sc)} [{head}] {who}: {str(sc.get('beat') or '').strip()}")
    if not lines and not lead:
        return "(this is the first scene)"
    size = sum(len(x) for x in lines) + len(lead)
    for n in range(max(len(lines) - 3, 0)):
        if size <= SO_FAR_CHARS:
            break
        short = lines[n][:BEAT_CHARS // 2]
        size -= len(lines[n]) - len(short)
        lines[n] = short
    return "\n".join(([lead] if lead else []) + lines)


def last_lines(scenes: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]], index: int, count: int = 4) -> str:
    """The last ``count`` lines spoken before ``scenes[index]`` (``Name: line``), oldest first."""
    spoken: list[str] = []
    for _, sc in reversed(scenes[:index]):
        for shot in reversed(sc.get("shots") or []):
            for line in reversed(shot.get("dialogue") or []):
                if isinstance(line, Mapping) and str(line.get("line") or "").strip():
                    spoken.append(f"{line.get('character', '')}: {str(line['line']).strip()}")
                    if len(spoken) >= count:
                        return "\n".join(reversed(spoken))
    return "\n".join(reversed(spoken)) or "(nothing has been said yet)"


def earlier_episodes(scripts: Sequence[Mapping[str, Any]], episode: int, facts_of: Mapping[int, Any]) -> str:
    """What the writers of ``episode`` are told about the episodes before it: each one's synopsis, how it ended (its
    last scene) and the facts fixed so far. Empty for the first episode. One script per episode (the newest)."""
    newest: dict[int, Mapping[str, Any]] = {}
    for row in scripts:
        if int(row["episode_no"]) < episode:
            newest[int(row["episode_no"])] = row
    if not newest:
        return ""
    lines = ["Earlier episodes (already shown; this episode starts where the last one ended):"]
    fixed: list[str] = []
    for number in sorted(newest):
        row = newest[number]
        ending = next((str(sc.get("beat") or "") for _, sc in reversed(scene_list(row)) if sc.get("beat")), "")
        lines.append(f"Episode {number} \"{row.get('title', '')}\": {row.get('synopsis', '')}"
                     + (f" It ended with: {ending[:BEAT_CHARS]}" if ending else ""))
        for fact in facts_of.get(number) or []:
            if str(fact).strip() and str(fact).strip() not in fixed:
                fixed.append(str(fact).strip())
    if fixed:
        lines.append("Facts fixed in earlier episodes (never contradict them): " + "; ".join(fixed)[:1600])
    return "\n".join(lines)


def outline_text(outline: Mapping[str, Any]) -> str:
    """The outline as plain text for the outline reader: synopsis, facts, threads, then every scene in order."""
    lines = [f"Title: {outline.get('title', '')}", f"Synopsis: {outline.get('synopsis', '')}", "",
             "Facts the writer fixed:", facts_text(outline.get("facts")), "", "Threads (planted -> paid off):"]
    threads = [t for t in outline.get("threads") or [] if isinstance(t, Mapping)]
    lines += [f"- {t.get('setup', '')} (scene {t.get('setup_scene', '?')}) -> {t.get('payoff', '')} "
              f"(scene {t.get('payoff_scene', '?')})" for t in threads] or ["(none)"]
    for act, sc in scene_list(outline):
        who = ", ".join(str(n) for n in sc.get("characters") or [])
        lines += ["", f"Scene {_key(act, sc)} | {sc.get('location', '')} | {sc.get('time_of_day', '')} | "
                      f"{sc.get('weather', '')} | {sc.get('scene_type', '')} | present: {who}",
                  str(sc.get("beat") or "")]
    return "\n".join(lines)


def summary_of(review: Mapping[str, Any], rounds: int, rewritten: Sequence[str] = ()) -> dict[str, Any]:
    """What a check leaves in the stage summary: the score, how often the text went back, and what is still open."""
    issues = [dict(i) for i in review.get("issues") or []]
    return {"score": float(review.get("score") or 0), "rounds": rounds, "rewritten": list(rewritten),
            "open": [{k: str(i.get(k) or "") for k in ("severity", "where", "problem", "fix", "lens")}
                     for i in issues][:20],
            "open_high": len(severe(issues))}


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
