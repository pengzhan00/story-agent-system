"""Storyboard check against the shot grammar (``grammar.toml``): a pure function over the shot rows, so the same
report serves the API, the CLI, the workflow summary and the web page. Each violation names its rule, the shot,
the numbers behind it and - where a mechanical repair is safe - the fix, which ``apply_fixes`` carries out."""
from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from storyagent.config import Grammar
from storyagent.story import ShotLocked, StoryStore


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str
    shot_id: int
    episode: int
    act: int
    scene: int
    shot: int
    params: Mapping[str, Any] = field(default_factory=dict)
    fix: Mapping[str, Any] | None = None       # {"type": "size"|"add_character"|"screen_direction", "value": ...}

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "severity": self.severity, "shot_id": self.shot_id, "episode": self.episode,
                "act": self.act, "scene": self.scene, "shot": self.shot, "params": dict(self.params),
                "fixable": self.fix is not None, "fix": dict(self.fix) if self.fix else None}


@dataclass(frozen=True)
class GrammarReport:
    violations: list[Violation]
    shots: int
    unclassified: int            # shots whose framing text is not on the scale (not judged by the size rules)

    def counts(self) -> dict[str, int]:
        c = Counter(v.severity for v in self.violations)
        return {s: c.get(s, 0) for s in ("error", "warning", "info")}

    def as_dict(self) -> dict[str, Any]:
        return {"shots": self.shots, "unclassified": self.unclassified, "counts": self.counts(),
                "violations": [v.as_dict() for v in self.violations]}


def _sign(text: str) -> int:
    t = text.strip().lower()
    return -1 if t == "left" else 1 if t == "right" else 0


def check(shots: Sequence[Mapping[str, Any]], episodes: Mapping[int, int], grammar: Grammar,
          characters: Collection[str] | None = None) -> GrammarReport:
    """``shots``: rows from ``StoryStore.list_shots``; ``episodes``: episode_id -> episode number."""
    if not grammar.enabled:
        return GrammarReport([], len(shots), 0)
    ordered = sorted(shots, key=lambda s: (episodes.get(s["episode_id"], 0), s["act_number"], s["scene_number"],
                                           s["shot_number"], s["id"]))
    scenes: dict[tuple[int, int, int], list[Mapping[str, Any]]] = {}
    for s in ordered:
        scenes.setdefault((episodes.get(s["episode_id"], 0), s["act_number"], s["scene_number"]), []).append(s)
    out: list[Violation] = []
    unclassified = sum(1 for s in ordered if grammar.rank(s["shot_type"]) is None)

    def add(rule_id: str, shot: Mapping[str, Any], fix: Mapping[str, Any] | None = None, **params: Any) -> None:
        rule = grammar.rule(rule_id)
        if rule is None:
            return
        out.append(Violation(rule_id, rule.severity, shot["id"], episodes.get(shot["episode_id"], 0),
                             shot["act_number"], shot["scene_number"], shot["shot_number"], params, fix))

    def rule_params(rule_id: str) -> Mapping[str, Any]:
        rule = grammar.rules.get(rule_id)
        return rule.params if rule else {}

    previous_location: dict[int, str] = {}
    for (episode, _act, _scene), group in scenes.items():
        first = group[0]
        # establishing: the first shot of a scene somewhere new
        if grammar.rule("establishing") and first["location"] != previous_location.get(episode):
            p = rule_params("establishing")
            rank, limit = grammar.rank(first["shot_type"]), grammar.sizes.index(p["max_size"])
            if rank is not None and rank > limit:
                add("establishing", first, {"type": "size", "value": p["fix_size"]}, size=grammar.sizes[rank],
                    max_size=p["max_size"])
        previous_location[episode] = group[-1]["location"]
        ranks = [grammar.rank(s["shot_type"]) for s in group]
        # neighbouring shots
        for prev, cur, r0, r1 in zip(group, group[1:], ranks, ranks[1:], strict=False):
            if r0 is not None and r1 is not None and set(prev["characters"]) == set(cur["characters"]) \
                    and prev["location"] == cur["location"]:
                step = abs(r1 - r0)
                p = rule_params("jump_size")
                if grammar.rule("jump_size") and step < int(p["min_step"]):
                    nxt = r1 + 1 if r1 + 1 < len(grammar.sizes) else r1 - 1
                    add("jump_size", cur, {"type": "size", "value": grammar.sizes[nxt]}, size=grammar.sizes[r1],
                        previous=prev["shot_number"])
                if grammar.rule("size_leap") and step > int(rule_params("size_leap")["max_step"]):
                    add("size_leap", cur, None, previous_size=grammar.sizes[r0], size=grammar.sizes[r1], steps=step)
            d0 = _sign(str(prev["render_payload"].get("staging", {}).get("screen_direction", "")))
            d1 = _sign(str(cur["render_payload"].get("staging", {}).get("screen_direction", "")))
            if d0 and d1 and d0 != d1 and set(prev["characters"]) & set(cur["characters"]):
                add("axis_cross", cur, {"type": "screen_direction", "value": prev["render_payload"]["staging"][
                    "screen_direction"]}, previous=prev["shot_number"])
        # the scene as a whole
        p = rule_params("coverage")
        known = {r for r in ranks if r is not None}
        if grammar.rule("coverage") and len(group) >= int(p["min_shots"]) and len(known) < int(p["min_distinct"]) \
                and all(r is not None for r in ranks):
            add("coverage", first, None, shots=len(group), distinct=len(known))
        p = rule_params("dialogue_close")
        lines = sum(len(s["dialogue"]) for s in group)
        if grammar.rule("dialogue_close") and lines >= int(p["min_lines"]) and known \
                and max(known) < grammar.sizes.index(p["min_size"]):
            add("dialogue_close", first, None, lines=lines, min_size=p["min_size"])
        # the longest run of equal shot lengths
        p = rule_params("monotone_pace")
        if grammar.rule("monotone_pace"):
            run = 1
            for a, b in zip(group, group[1:], strict=False):
                da = a["render_payload"].get("camera", {}).get("duration_sec")
                db = b["render_payload"].get("camera", {}).get("duration_sec")
                run = run + 1 if da is not None and da == db else 1
                if run == int(p["max_same_run"]) + 1:
                    add("monotone_pace", b, None, run=run, seconds=db)
    for s in ordered:
        for line in s["dialogue"]:
            who = str(line.get("character", "")).strip()
            if who and who not in s["characters"] and (characters is None or who in characters):
                add("speaker_in_frame", s, {"type": "add_character", "value": who}, character=who)
        action = f" {s['render_payload'].get('subject', {}).get('action', '')} ".lower()
        p = rule_params("single_action")
        if grammar.rule("single_action"):
            hits = [c for c in p["connectors"] if c.lower() in action]
            if len(hits) > int(p["max_connectors"]):
                add("single_action", s, None, connectors=", ".join(h.strip() for h in hits))
    severity_order = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda v: (severity_order[v.severity], v.episode, v.act, v.scene, v.shot, v.rule))
    return GrammarReport(out, len(ordered), unclassified)


def apply_fix(store: StoryStore, violation: Violation) -> str | None:
    """Carry out the violation's fix. Returns None when done, else the reason it was left alone."""
    if violation.fix is None:
        return "no_fix"
    shot = store.get_shot(violation.shot_id)
    if shot["locked"]:
        return "locked"
    payload = dict(shot["render_payload"])
    kind, value = violation.fix["type"], violation.fix["value"]
    changes: dict[str, Any] = {}
    if kind == "size":
        payload["camera"] = {**payload.get("camera", {}), "shot_type": value}
        changes = {"shot_type": value, "render_payload": payload}
    elif kind == "screen_direction":
        payload["staging"] = {**payload.get("staging", {}), "screen_direction": value}
        changes = {"render_payload": payload}
    elif kind == "add_character":
        if value in shot["characters"]:
            return None
        who = store.find_character(shot["project_id"], value)
        subject = dict(payload.get("subject", {}))
        in_payload = [c for c in subject.get("characters", []) if c.get("name") == value]
        subject["characters"] = [*subject.get("characters", []), *([] if in_payload else [
                                 {"name": value, "appearance": (who or {}).get("appearance", ""), "costume": "",
                                  "look": "", "voice_profile": (who or {}).get("voice_profile", ""),
                                  "prompt_template": (who or {}).get("prompt_template", ""),
                                  "voice_ref": (who or {}).get("voice_ref_audio", ""),
                                  "lora_ref": (who or {}).get("lora_ref", ""),
                                  "ref_images": (who or {}).get("ref_images", [])}])]
        payload["subject"] = subject
        names = [*shot["characters"], value]
        payload["continuity"] = {**payload.get("continuity", {}),
                                 "character_anchor": "|".join(sorted(names))}
        changes = {"characters": names, "render_payload": payload}
    else:
        return "unknown_fix"
    try:
        store.update_shot(violation.shot_id, changes)
    except ShotLocked:
        return "locked"
    return None


def apply_fixes(
    store: StoryStore, project_id: int, grammar: Grammar, *, episode: int | None = None,
    rules: Sequence[str] | None = None, shot_ids: Sequence[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], GrammarReport]:
    """Repair the fixable violations (optionally only some rules/shots/an episode), one pass.
    Returns (fixed, left alone with the reason, the report after the repairs)."""
    report = run_check(store, project_id, grammar, episode)
    fixed: list[dict[str, Any]] = []
    left: list[dict[str, Any]] = []
    done: set[tuple[int, str]] = set()
    for v in report.violations:
        if v.fix is None or (rules and v.rule not in rules) or (shot_ids and v.shot_id not in shot_ids):
            continue
        if (v.shot_id, v.fix["type"]) in done:        # one repair per shot and kind per pass: the check runs again
            continue
        reason = apply_fix(store, v)
        if reason is None:
            fixed.append({"shot_id": v.shot_id, "rule": v.rule})
            done.add((v.shot_id, v.fix["type"]))
        else:
            left.append({"shot_id": v.shot_id, "rule": v.rule, "reason": reason})
    return fixed, left, run_check(store, project_id, grammar, episode)


def run_check(store: StoryStore, project_id: int, grammar: Grammar, episode: int | None = None) -> GrammarReport:
    episodes = {e["id"]: e["number"] for e in store.list_episodes(project_id)}
    shots = store.list_shots(project_id)
    if episode is not None:
        shots = [s for s in shots if episodes.get(s["episode_id"]) == episode]
    names = {c["name"] for c in store.list_characters(project_id)}
    return check(shots, episodes, grammar, names)
