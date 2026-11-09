"""Shot detail cards and the fixed descriptions they lean on.

A screenwriter's shot is a summary. For the scenes where the picture has to carry the story (``detail_scene_types``:
fights, intimate scenes) a second pass turns every shot into a *card*: what each person wears, shows, does with every
limb, what touches what, the one movement, the camera, the light, the end state for the next shot. The card, not a
language model's second guess, then becomes the keyframe and video prompts.

What never changes (a person's build, skin, marks; a room's layout and the size of its bed) is written once as an
*anchor* (``anchor:character:<name>`` / ``anchor:location:<name>`` in the project's workflow data), locked, and pasted
word for word into every shot prompt; the card only holds what varies from shot to shot.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .stages import Ctx

CARD_TEXT = ("summary", "position_name", "contact", "light", "setting", "sound", "start_frame", "end_frame",
             "video_motion", "continuity_out", "negative")
PERSON_TEXT = ("who", "clothing", "body_in_view", "pose", "limbs", "face", "skin")
SHOT_KEYS = ("action", "start_pose", "end_pose", "expression", "camera_angle", "camera_movement", "narration",
             "dialogue", "mood", "duration_sec", "sfx_cues")


# -- anchors -----------------------------------------------------------------------------------------------
def anchor_of(state: Any, project_id: int, kind: str, name: str) -> str:
    """The locked fixed description of a character (``kind='character'``) or a place (``'location'``), or ''."""
    data = state.get_data(project_id, f"anchor:{kind}:{name}")
    return str((data or {}).get("text") or "").strip()


def anchors_of(state: Any, project_id: int, names: Sequence[str]) -> dict[str, str]:
    """The locked fixed description of each named character that has one."""
    found = {n: anchor_of(state, project_id, "character", n) for n in names}
    return {n: text for n, text in found.items() if text}


_NOT_ENGLISH =re.compile(r"[^\x00-\x7f]+")        # a description is English: anything else is left out


def ensure_anchors(ctx: Ctx, characters: Sequence[Mapping[str, Any]], locations: Sequence[Mapping[str, Any]],
                   *, redo: bool = False, story: str = "") -> int:
    """Write the fixed description of every character and place that has none yet (one request each); a written one
    is kept, an edit of it too, unless ``redo``. ``story`` is what the writer already decided (a scar, a brand, the
    room): the description keeps to it. Returns how many were written."""
    style = _style_text(ctx)
    made = 0
    jobs: list[tuple[str, str, dict[str, Any]]] = [
        ("character", str(c["name"]), {k: c.get(k, "") for k in ("name", "role", "appearance", "prompt_template")})
        for c in characters] + [
        ("location", str(loc["name"]), {k: loc.get(k, "") for k in ("name", "description", "lighting", "atmosphere",
                                                                    "prompt_template")}) for loc in locations]
    for kind, name, subject in jobs:
        if anchor_of(ctx.state, ctx.pid, kind, name) and not redo:
            continue
        data = ctx.run_agent("anchor.fixed", {"kind": kind, "subject": json.dumps(subject, ensure_ascii=False),
                                              "style": style or "(none)", "story": story or "(none)"})
        text = " ".join(_NOT_ENGLISH.sub(" ", str(data.get("text") or "")).split())
        if text:
            ctx.state.put_data(ctx.pid, f"anchor:{kind}:{name}", {"text": text, "locked": True})
            made += 1
    return made


def fixed_block(state: Any, project_id: int, names: list[str], chars: dict[str, dict[str, Any]], location: str) -> str:
    """The locked descriptions of the people in frame and of the place, in the labels the prompts use."""
    from .seed import labelled_identities

    anchors = anchors_of(state, project_id, names)
    parts = [f"{label}, fixed details: {text}"
             for label, name, _ in labelled_identities(names, chars, keep_missing=True, anchors=anchors)
             if (text := anchors.get(name))]
    place = anchor_of(state, project_id, "location", location)
    if place:
        parts.append(f"The place, fixed details: {place}")
    return " ".join(p if p.endswith((".", "!", "?")) else p + "." for p in parts)


# -- the card ----------------------------------------------------------------------------------------------
def _style_text(ctx: Ctx) -> str:
    return str((ctx.state.get_data(ctx.pid, "style", {}) or {}).get("visual_style", ""))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def clean_card(data: Mapping[str, Any]) -> dict[str, Any]:
    """The model's answer with every text trimmed and the structure it should have."""
    card: dict[str, Any] = {k: _clean(data.get(k)) for k in CARD_TEXT}
    card["people"] = [{k: _clean(p.get(k)) for k in PERSON_TEXT}
                      for p in data.get("people") or [] if isinstance(p, Mapping)]
    motion: Mapping[str, Any] = data["motion"] if isinstance(data.get("motion"), Mapping) else {}
    card["motion"] = {k: _clean(motion.get(k)) for k in ("main", "rhythm", "secondary", "still")}
    cam: Mapping[str, Any] = data["camera"] if isinstance(data.get("camera"), Mapping) else {}
    card["camera"] = {k: _clean(cam.get(k)) for k in ("size", "angle", "height", "movement", "crop", "focus")}
    try:
        card["camera"]["lens_mm"] = float(cam.get("lens_mm") or 0)
    except (TypeError, ValueError):
        card["camera"]["lens_mm"] = 0.0
    return card


def lint_card(card: Mapping[str, Any], *, people: int, vague: Sequence[str], min_words: int,
              used: Sequence[str]) -> list[str]:
    """What is wrong with a card, as short sentences the writer is asked to fix (empty = good)."""
    problems: list[str] = []
    for key in ("start_frame", "end_frame", "video_motion", "continuity_out", "light", "setting", "negative",
                "position_name"):
        if not card.get(key):
            problems.append(f"`{key}` is empty")
    if people and len(card.get("people") or []) != people:
        problems.append(f"`people` must have {people} entries, one for each person in frame")
    for p in card.get("people") or []:
        for key in PERSON_TEXT:
            if not p.get(key):
                problems.append(f"`people.{key}` of {p.get('who') or 'a person'} is empty")
    if people > 1 and not card.get("contact"):
        problems.append("`contact` is empty although several people are in frame: say what touches what and whose part")
    motion, camera = card.get("motion") or {}, card.get("camera") or {}
    if not motion.get("main"):
        problems.append("`motion.main` is empty")
    for key in ("size", "angle", "movement", "crop"):
        if not camera.get(key):
            problems.append(f"`camera.{key}` is empty")
    if len(str(card.get("start_frame", "")).split()) < min_words:
        problems.append(f"`start_frame` is shorter than {min_words} words: describe the whole still")
    text = json.dumps(card, ensure_ascii=False).lower()
    found = sorted({w for w in vague if w.lower() in text})
    if found:
        problems.append("vague words that show nothing: " + ", ".join(found) + "; say what is physically happening")
    if people > 1:
        owned = [(p.get("clothing"), p.get("limbs")) for p in card.get("people") or []]
        if len(set(owned)) < len(owned):
            problems.append("two people have the same `clothing` and `limbs`: each entry describes only its own person")
    position = _position_key(str(card.get("position_name", "")))
    if position and len(used) >= 2 and all(_position_key(u) == position for u in used[-2:]):
        problems.append(f"the position '{card.get('position_name')}' was already used in the last two shots; "
                        "change the arrangement of the bodies")
    return problems


def _position_key(name: str) -> str:
    """The first words of a position tag: 'woman prone man behind' and 'woman prone man behind deep thrust' are the
    same arrangement."""
    return " ".join(name.lower().split()[:4])


def prompts_from_detail(card: Mapping[str, Any], style: str = "") -> dict[str, str]:
    """``image_prompt`` / ``video_prompt`` / ``negative_prompt`` built from a card, word for word: the still is the
    card's opening frame with each person's visible state, the clip starts from that still and only moves."""
    cam = card.get("camera") or {}
    lens = f"{cam['lens_mm']:g}mm lens" if cam.get("lens_mm") else ""
    seen: set[str] = set()
    framing_parts: list[str] = []
    for x in (cam.get("size"), cam.get("angle"), cam.get("height"), lens, cam.get("crop"), cam.get("focus")):
        word = str(x or "").strip().rstrip(".")
        if word and word.lower() not in seen:                      # the model sometimes repeats the size as the angle
            seen.add(word.lower())
            framing_parts.append(word)
    framing = ", ".join(framing_parts)
    people = " ".join(
        f"{p['who']}: " + ", ".join(x for x in (p.get("clothing"), p.get("body_in_view"), p.get("pose"),
                                                p.get("limbs"), p.get("face"), p.get("skin")) if x) + "."
        for p in card.get("people") or [])
    parts = [framing + "." if framing else "", card.get("start_frame", ""), people,
             f"Contact: {card['contact']}." if card.get("contact") else "",
             f"Setting: {card['setting']}." if card.get("setting") else "",
             f"Light: {card['light']}." if card.get("light") else "", style]
    motion = card.get("motion") or {}
    clip = [f"Starting exactly from the opening frame: {card.get('start_frame', '')}",
            card.get("video_motion", ""),
            f"Rhythm: {motion['rhythm']}." if motion.get("rhythm") else "",
            f"Small motions: {motion['secondary']}." if motion.get("secondary") else "",
            f"Camera: {cam['movement']}." if cam.get("movement") else "",
            f"Ends on: {card['end_frame']}" if card.get("end_frame") else ""]
    return {"image_prompt": " ".join(x for x in parts if x), "video_prompt": " ".join(x for x in clip if x),
            "negative_prompt": str(card.get("negative", ""))}


# -- the pass ----------------------------------------------------------------------------------------------
def _in_frame(shot: Mapping[str, Any], scene: Mapping[str, Any], names: Sequence[str]) -> list[str]:
    """Who is in the shot: the writer's list plus everyone the shot's own words name (a writer who lists one person
    for 'he grips her wrist' still means two), else the scene's cast."""
    asked = [str(n).strip() for n in shot.get("characters_present") or []]
    text = " ".join(str(shot.get(k) or "") for k in ("action", "start_pose", "end_pose", "expression", "narration"))
    named = [n for n in names if n in text]
    present = [n for n in names if n in asked or n in named]
    return present or [n for n in (str(x).strip() for x in scene.get("characters") or []) if n in names]


def write_card(ctx: Ctx, shot: Mapping[str, Any], scene: Mapping[str, Any], names: list[str],
               chars: dict[str, dict[str, Any]], place: str, previous: Mapping[str, Any] | None, ledger: str,
               used: list[str], number: int, total: int) -> dict[str, Any]:
    """One shot's card: asked of the model, checked, asked again with the problems named (at most
    ``detail_attempts`` more times); a card that still has problems is kept with them listed."""
    from .seed import labelled_identities

    cfg = ctx.settings.workflow
    labelled = labelled_identities(names, chars, keep_missing=True,
                                   anchors=anchors_of(ctx.state, ctx.pid, names))
    people = [{"label": label, "fixed": anchor_of(ctx.state, ctx.pid, "character", name) or ident}
              for label, name, ident in labelled]
    variables: dict[str, Any] = {
        "shot": json.dumps({k: shot.get(k) for k in SHOT_KEYS if shot.get(k)}, ensure_ascii=False),
        "scene": json.dumps({k: scene.get(k) for k in ("beat", "location", "time_of_day", "weather", "scene_type")},
                            ensure_ascii=False),
        "people": json.dumps(people, ensure_ascii=False),
        "setting": anchor_of(ctx.state, ctx.pid, "location", str(scene.get("location", ""))) or "(none)",
        "style": _style_text(ctx) or "(none)",
        "previous": json.dumps({k: previous.get(k) for k in ("summary", "position_name", "end_frame")}
                               if previous else "(start of the scene)", ensure_ascii=False),
        "ledger": ledger or "(nothing yet: everyone starts as the scene's first shot describes)",
        "used_positions": json.dumps(used, ensure_ascii=False) if used else "(none)",
        "position": f"{number} of {total}", "problems": "(none)"}
    vague = [str(w) for w in cfg.get("detail_vague") or ()]
    min_words = int(cfg.get("detail_min_words", 60))
    card: dict[str, Any] = {}
    problems: list[str] = []
    for _ in range(1 + int(cfg.get("detail_attempts", 2))):
        card = clean_card(ctx.run_agent("shot.detail", variables))
        if shot.get("camera_angle"):                  # the framing was decided with the cuts in mind (framing.py)
            card["camera"]["size"] = _clean(shot["camera_angle"])
        problems = lint_card(card, people=len(labelled), vague=vague, min_words=min_words, used=used)
        if not problems:
            break
        variables["problems"] = "; ".join(problems)
    if problems:
        card["problems"] = problems
    return card


def detail_pass(ctx: Ctx, episode: int, scenes: Sequence[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """Cards for every shot of the scenes whose type is in ``[workflow] detail_scene_types`` that has none yet (all of
    them with the run parameter ``redetail``). Each shot is saved as it is done, so a stopped run goes on from there."""
    cfg = ctx.settings.workflow
    if ctx.params.get("detail") is False or not cfg.get("detail", True):
        return {"detailed": 0, "detail_problems": 0}
    kinds = {str(k) for k in cfg.get("detail_scene_types") or ()}
    todo = [(act, sc) for act, sc in scenes if str(sc.get("scene_type") or "") in kinds and sc.get("shots")]
    if not todo:
        return {"detailed": 0, "detail_problems": 0}
    again = ctx.params.get("redetail")                 # true: every card; "problems": only the cards that had problems
    redo = bool(again) and again != "problems"
    chars ={c["name"]: c for c in ctx.store.list_characters(ctx.pid)}
    ctx.step(0, 1, "anchors")
    known = {str(loc["name"]): loc for loc in ctx.store.list_locations(ctx.pid)}
    places: dict[str, Mapping[str, Any]] = {}
    for _, sc in todo:           # the places come from the scenes: the locations stage has not run yet at this point
        name = str(sc.get("location") or "").strip()
        if name and name not in places:
            places[name] = known.get(name) or {"name": name, "description": f"{sc.get('beat', '')} "
                                               f"({sc.get('time_of_day', '')}, {sc.get('weather', '')})"}
    row = ctx.store.get_script(ctx.pid, episode) or {}
    story = json.dumps({"synopsis": row.get("synopsis", ""), "scenes": [
        {"where": f"{act['number']}.{sc['number']}", "beat": sc.get("beat", "")} for act, sc in scenes]},
        ensure_ascii=False)[:3500]
    ensure_anchors(ctx, list(chars.values()), list(places.values()), redo=bool(ctx.params.get("reanchor")),
                   story=story)
    made = flagged = 0
    total = sum(len(sc["shots"]) for _, sc in todo)
    done = 0
    for act, scene in todo:
        shots = scene["shots"]
        previous: dict[str, Any] | None = None
        ledger = ""
        used: list[str] = []
        for i, shot in enumerate(shots, start=1):
            ctx.step(done, total, f"{act['number']}.{scene['number']}.{i}")
            done += 1
            card = shot.get("detail")
            if not card or redo or (again == "problems" and card.get("problems")):
                names = _in_frame(shot, scene, list(chars))
                card = write_card(ctx, shot, scene, names, chars, str(scene.get("location", "")), previous, ledger,
                                  used, i, len(shots))
                shot["detail"] = card
                made += 1
                flagged += 1 if card.get("problems") else 0
                ctx.store.update_script_scene(ctx.pid, episode, act["number"], scene["number"], {"shots": shots})
            previous, ledger = card, str(card.get("continuity_out") or ledger)
            used.append(str(card.get("position_name") or ""))
    return {"detailed": made, "detail_problems": flagged}


# -- the review's view of the package ---------------------------------------------------------------------
def merge_reviews(results: Sequence[tuple[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """One review out of several reviewers' (``lens``, review) pairs: the score is their mean, the summaries are
    kept apart with the lens named (a lone story review stays as it was), each issue says which lens found it."""
    issues = [{**dict(i), "lens": lens} for lens, r in results for i in r.get("issues") or []]
    scores = [float(r.get("score") or 0) for _, r in results]
    if len(results) == 1:
        summary = str(results[0][1].get("summary") or "")
    else:
        summary = "\n".join(f"[{lens}] {r.get('summary') or ''}" for lens, r in results)
    return {"score": round(sum(scores) / len(scores), 1) if scores else 0.0, "summary": summary, "issues": issues}



def package_text(title: str, acts: Sequence[Mapping[str, Any]]) -> str:
    """The story with the shot cards, as plain text for the reviewers: a scene's beat and each shot's summary,
    and where a card exists its people, contact, motion, camera and end state."""
    lines = [f"Title: {title}"]
    for act in acts:
        for scene in act.get("scenes", []):
            lines.append(f"\n## {act.get('number')}.{scene.get('number')} [{scene.get('scene_type', '')}] "
                         f"{scene.get('location', '')}: {scene.get('beat', '')}")
            for i, shot in enumerate(scene.get("shots", []), start=1):
                where = f"{act.get('number')}.{scene.get('number')}.{shot.get('shot_number') or i}"
                card = shot.get("detail") or {}
                if not card:
                    lines.append(f"- {where}: {shot.get('action', '')}")
                    continue
                lines.append(f"- {where}: {card.get('summary') or shot.get('action', '')} "
                             f"(position: {card.get('position_name', '')})")
                for p in card.get("people", []):
                    lines.append(f"    {p['who']}: wears {p['clothing']}; shows {p['body_in_view']}; {p['pose']}; "
                                 f"limbs: {p['limbs']}; face: {p['face']}; skin: {p['skin']}")
                m, c = card.get("motion", {}), card.get("camera", {})
                lines.append(f"    contact: {card.get('contact', '')}; "
                             f"motion: {m.get('main', '')} / {m.get('rhythm', '')}; "
                             f"camera: {c.get('size', '')}, {c.get('angle', '')}, {c.get('movement', '')}, "
                             f"crop {c.get('crop', '')}; end state: {card.get('continuity_out', '')}")
    return "\n".join(lines)
