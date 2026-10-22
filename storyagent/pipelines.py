"""Which settings a project runs with: its pipeline plan's (the recommended, complete set of models), with the model
choices the person changed by hand on top, or the plain global ones for a project made before plans.

A plan is the recommended set. A project may swap single models (``projects.settings['pipeline_models']`` =
``{kind: model ref}``); removing the choice (or all of them) is the one-click return to the plan's defaults. Some kinds
only make sense next to a model of the same family (an edit model next to the picture model it edits for, the audio
video model next to the video model): changing the leader pairs the follower again, or switches the follower's use off
when the family has none, and says so (``notes``).
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from storyagent.config import ConfigError, Settings
from storyagent.content import model_rating

# follower kind -> the kind it must match by model family
FOLLOWERS = {"image.edit": "image.generate", "video.av": "video.generate", "video.lipsync": "video.generate"}
SETTINGS_KEY = "pipeline_models"


def _family(settings: Settings, ref: str | None) -> str:
    model = settings.models.get(ref or "")
    return model.family if model else ""


def check_choices(settings: Settings, pipeline: str, choices: Mapping[str, Any]) -> dict[str, str]:
    """The model choices cleaned and checked: only kinds the plan uses (or pairs), only models of that kind."""
    plan = settings.pipeline(pipeline)
    allowed = {*plan.models, *FOLLOWERS}
    out: dict[str, str] = {}
    for kind, ref in choices.items():
        if kind not in allowed:
            raise ConfigError(f"pipeline_models: kind {kind!r} is not used by plan {plan.name!r}; "
                              f"known: {sorted(allowed)}")
        model = settings.models.get(str(ref))
        if model is None or model.kind != kind:
            raise ConfigError(f"pipeline_models: {ref!r} is not a {kind} model")
        out[kind] = str(ref)
    return out


def effective_models(settings: Settings, pipeline: str, choices: Mapping[str, Any] | None
                     ) -> tuple[dict[str, str], dict[str, Any], list[dict[str, str]]]:
    """``(models, workflow patch, notes)`` of a plan with the person's choices: the plan's models, the choices over
    them, and the followers re-paired with a changed leader."""
    plan = settings.pipeline(pipeline)
    picked = check_choices(settings, pipeline, choices or {})
    models = {**plan.models, **picked}
    patch: dict[str, Any] = {}
    notes: list[dict[str, str]] = []
    for follower, leader in FOLLOWERS.items():
        if leader not in picked or follower in picked or follower not in models:
            continue
        family = _family(settings, models[leader])
        if _family(settings, models[follower]) == family:
            continue
        match = next((r for r, m in sorted(settings.models.items()) if m.kind == follower and m.family == family), None)
        if match:
            models[follower] = match
            notes.append({"code": "auto_paired", "kind": follower, "model": match})
        else:
            del models[follower]
            if follower == "image.edit":
                patch["keyframe_from_references"] = False       # no edit model of this family: keyframes made directly
            notes.append({"code": "dropped", "kind": follower, "model": plan.models[follower]})
    return models, patch, notes


def settings_for(settings: Settings, pipeline: str, choices: Mapping[str, Any] | None = None) -> Settings:
    """``pipeline`` empty = a project that predates plans: it keeps the global configuration unchanged."""
    if not pipeline:
        return settings
    if not choices:
        return settings.for_pipeline(pipeline)
    models, patch, _ = effective_models(settings, pipeline, choices)
    return settings.for_pipeline(pipeline, models=models, workflow=patch)


def project_settings_for(settings: Settings, db: Any, project_id: int | None) -> Settings:
    """The effective settings of a project (by id); no project (the workshop) = the global configuration."""
    if not project_id:
        return settings
    row = db.conn.execute("SELECT pipeline, settings FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        return settings_for(settings, "")
    try:
        choices = json.loads(row["settings"] or "{}").get(SETTINGS_KEY)
    except ValueError:
        choices = None
    return settings_for(settings, row["pipeline"], choices)


def describe(settings: Settings, pipeline: str, choices: Mapping[str, Any] | None, project_rating: str
             ) -> dict[str, Any]:
    """What the pipeline panel shows: per kind the plan's recommendation, what is in use, the models to choose from
    (marked when they do not pair with the picture / video model in use) and the notes."""
    plan = settings.pipeline(pipeline)
    picked = check_choices(settings, pipeline, choices or {})
    models, _, notes = effective_models(settings, pipeline, picked)
    kinds = [k for k in (*plan.models, *FOLLOWERS) if k in plan.models or k in models]
    rows: list[dict[str, Any]] = []
    for kind in dict.fromkeys(kinds):
        leader = FOLLOWERS.get(kind)
        want = _family(settings, models.get(leader)) if leader else ""
        chosen = models.get(kind, "")
        cands = [{"ref": m.ref, "family": m.family, "rating": model_rating(settings, m.ref),
                  "paired": not leader or m.family == want, "remote": m.remote, "commercial_ok": m.commercial_ok,
                  "license_tag": m.license_tag}
                 for m in sorted(settings.models.values(), key=lambda m: m.ref) if m.kind == kind]
        default = plan.models.get(kind, "")
        rows.append({"kind": kind, "default": default, "chosen": chosen, "overridden": kind in picked,
                     "auto": bool(chosen) and kind not in picked and chosen != default, "candidates": cands})
        if chosen and leader and kind in picked and _family(settings, chosen) != want:
            notes.append({"code": "family_mismatch", "kind": kind, "model": chosen})
    cfg = settings.content
    for kind, ref in models.items():
        if cfg.policy(model_rating(settings, ref)).rank > cfg.policy(project_rating).rank and kind in picked:
            notes.append({"code": "rated_model", "kind": kind, "model": ref})
    return {"plan": plan.name, "rows": rows, "notes": notes, "overridden": bool(picked)}
