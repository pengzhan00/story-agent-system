"""The production plan: what ``produce`` would make, listed so a person can confirm it first.

``produce`` used to start every missing image, voice, song and video at once. The plan lists each task (one per spec
that needs media) with its model, an estimate and a state; the person unticks what they do not want (no videos yet,
only the heroine's portrait ...) and confirms, and the run is started with ``only = [spec ids]``. Nothing here
spends machine time: it only reads the specs and the ledger.

State of a task: ``new`` (no asset yet) | ``stale`` (an asset exists, made from an older version of the spec) |
``done`` (up to date) | ``blocked`` (cannot be made, ``reason`` says why: no model, no voice ...) | ``skip`` (a shot the
tiering policy leaves as a keyframe with a slow move; tick it to make its clip anyway).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from storyagent.config import ConfigError, Settings
from storyagent.domain import QualityTier
from storyagent.media.ledger import Ledger
from storyagent.media.specs import SpecRecord, SpecStore
from storyagent.story import StoryStore

from .media import FALLBACK_SECONDS, PHASES, current_asset, default_voice_of, up_to_date, video_picks


@dataclass
class PlannedTask:
    spec_id: str
    subject_type: str
    subject_id: str
    label: str
    kind: str
    model_ref: str
    phase: int
    state: str
    reason: str = ""
    est_seconds: int = 0
    needs: list[str] = field(default_factory=list)     # spec ids made earlier in the same run that this one builds on


def _labels(store: StoryStore, project_id: int) -> dict[str, dict[str, str]]:
    shots = store.list_shots(project_id)
    return {
        "character": {str(c["id"]): str(c["name"]) for c in store.list_characters(project_id)},
        "location": {str(x["id"]): str(x["name"]) for x in store.list_locations(project_id)},
        "music": {str(m["id"]): str(m["name"]) for m in store.list_music(project_id)},
        "sfx": {str(e["id"]): str(e["name"]) for e in store.list_sfx(project_id)},
        "shot": {str(s["id"]): f"{s['act_number']}.{s['scene_number']}.{s['shot_number']}" for s in shots},
    }


def _label(spec: SpecRecord, names: dict[str, dict[str, str]]) -> str:
    kind = spec.subject_type.split(".")[0]
    head, _, line = spec.subject_id.partition("#")
    if spec.subject_type == "dialogue.line":
        text = spec.text_payload.strip()
        return f"{names['shot'].get(head, head)} #{int(line) + 1 if line.isdigit() else line}: {text[:40]}"
    if spec.subject_type.startswith("shot."):
        return names["shot"].get(head, head)
    if spec.subject_type.startswith("music."):
        return names["music"].get(head, head)
    if spec.subject_type == "sfx.cue":
        return names["sfx"].get(head, head)
    table = "character" if kind == "character" else "location"
    return names[table].get(head, head)


def produce_plan(
    settings: Settings, store: StoryStore, specs: SpecStore, ledger: Ledger, project_id: int, tier: QualityTier,
    *, force: bool = False, project_settings: dict[str, Any] | None = None,
) -> list[PlannedTask]:
    names = _labels(store, project_id)
    default_voice = default_voice_of(settings, project_settings)
    picks = video_picks(settings, store, ledger, project_id, project_settings, tier)
    out: list[PlannedTask] = []
    current = specs.list_current(project_id)
    by_key = {(s.subject_type, s.subject_id, s.slot): s for s in current}
    for phase, types in enumerate(PHASES, 1):
        for spec in current:
            if spec.subject_type not in types:
                continue
            asset = current_asset(ledger, spec)
            state = "new" if asset is None else "done" if up_to_date(ledger, spec) and not force else "stale"
            reason, model_ref = "", spec.model_ref
            try:
                model_ref = model_ref or settings.default_model(spec.kind.value).ref
                settings.model(model_ref)
            except ConfigError:
                state, reason = "blocked", "no_model"
            if spec.subject_type == "dialogue.line" and not spec.params.get("voice_ref") and not default_voice:
                state, reason = "blocked", "no_voice"
            if spec.subject_type == "shot.video" and state == "new" and spec.subject_id not in picks:
                state, reason = "skip", "still"
            guess = FALLBACK_SECONDS.get(spec.kind.value) or FALLBACK_SECONDS.get(spec.kind.value.split(".")[0], 60.0)
            known = ledger.estimate_seconds(model_ref, tier.value) if model_ref else 0
            needs = [by_key[(d["subject_type"], d["subject_id"], d.get("slot", "main"))].id
                     for d in spec.depends_on
                     if (d.get("subject_type"), d.get("subject_id"), d.get("slot", "main")) in by_key]
            out.append(PlannedTask(
                spec_id=spec.id, subject_type=spec.subject_type, subject_id=spec.subject_id,
                label=_label(spec, names), kind=spec.kind.value, model_ref=model_ref, phase=phase, state=state,
                reason=reason, est_seconds=int(known or guess), needs=needs))
    return out


def totals(tasks: list[PlannedTask]) -> dict[str, Any]:
    todo = [t for t in tasks if t.state in ("new", "stale")]
    return {"count": len(todo), "est_seconds": sum(t.est_seconds for t in todo),
            "by_state": {s: sum(1 for t in tasks if t.state == s) for s in ("new", "stale", "done", "blocked", "skip")}}


def video_summary(tasks: list[PlannedTask], budget_sec: float | None) -> dict[str, Any]:
    """The clips at a glance: how many shots get one, what that costs, and the budget it is held to."""
    clips = [t for t in tasks if t.subject_type == "shot.video"]
    chosen = [t for t in clips if t.state != "skip"]
    return {"shots": len(clips), "chosen": len(chosen),
            "est_seconds": sum(t.est_seconds for t in chosen if t.state in ("new", "stale")),
            "budget_seconds": int(budget_sec) if budget_sec is not None else None}
