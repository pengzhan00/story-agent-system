"""The production chain as data: which step runs what, on which model, reading and writing which things.

``describe_flow`` answers "what will happen / what happened" for the web client and for people reading the API. The
chain itself is declared here once (``CHAIN``); everything that depends on configuration - which language model serves a
role, which media model a kind uses and with which parameters per quality tier - is looked up live, so the picture
always matches what a run would really do. With a project, each step also carries its state and counts.
"""
from __future__ import annotations

from typing import Any

from storyagent.agents.registry import load_specs
from storyagent.config import ConfigError, Settings
from storyagent.db import Database
from storyagent.domain import MediaKind, QualityTier
from storyagent.media.params import resolve_params

from .state import STAGES, WorkflowState

# (stage, how it works, agents (always), agents (only on some runs), reads, writes)
CHAIN: tuple[dict[str, Any], ...] = (
    {"stage": "analyze", "how": "llm", "agents": ["director.analyze", "art.style"], "optional": [],
     "reads": ["premise"], "writes": ["plan", "style"]},
    {"stage": "cast", "how": "llm", "agents": ["character.design"], "optional": [],
     "reads": ["plan", "style"], "writes": ["characters"]},
    {"stage": "script", "how": "llm", "agents": ["screenwriter.outline", "screenwriter.shots"],
     "optional": ["screenwriter.adapt"], "reads": ["plan", "characters"], "writes": ["outline", "shot_drafts"]},
    {"stage": "scenes", "how": "llm", "agents": ["scene.design"], "optional": [],
     "reads": ["plan", "outline", "style"], "writes": ["locations"]},
    {"stage": "music", "how": "llm", "agents": ["composer.bgm", "composer.theme", "sound.design"], "optional": [],
     "reads": ["plan", "outline"], "writes": ["music", "sfx"]},
    {"stage": "shotplan", "how": "rules", "agents": [], "optional": [],
     "reads": ["outline", "shot_drafts", "characters", "locations", "style"], "writes": ["shots"]},
    {"stage": "specs", "how": "rules", "agents": [], "optional": ["shot.visual"],
     "reads": ["shots", "characters", "locations", "music", "sfx", "style"], "writes": ["specs"]},
    {"stage": "produce", "how": "media", "agents": [], "optional": [], "reads": ["specs"], "writes": ["assets"]},
    {"stage": "compose", "how": "compose", "agents": [], "optional": [], "reads": ["assets", "shots"],
     "writes": ["episode", "subtitles", "qc_report", "package"]},
)

# the things produce makes: spec subject -> media kind (order = the order produce works in)
MEDIA: tuple[tuple[str, MediaKind, int], ...] = (
    ("character.portrait", MediaKind.IMAGE_GENERATE, 1),
    ("character.voice", MediaKind.AUDIO_TTS, 1),
    ("scene.keyframe", MediaKind.IMAGE_GENERATE, 1),
    ("music.theme_song", MediaKind.AUDIO_MUSIC, 1),
    ("music.bgm", MediaKind.AUDIO_MUSIC, 1),
    ("sfx.cue", MediaKind.AUDIO_SFX, 1),
    ("dialogue.line", MediaKind.AUDIO_TTS, 1),
    ("shot.keyframe", MediaKind.IMAGE_GENERATE, 2),
    ("shot.video", MediaKind.VIDEO_GENERATE, 3),
)


def _model_info(settings: Settings, ref: str) -> dict[str, Any]:
    try:
        m = settings.model(ref)
    except ConfigError:
        return {"ref": ref, "known": False}
    params = {t.value: resolve_params(settings, ref, t) for t in QualityTier}
    return {"ref": ref, "known": True, "provider": m.provider, "kind": m.kind, "heavy": m.heavy,
            "license_tag": m.license_tag, "commercial_ok": m.commercial_ok, "params": params}


def _agents(settings: Settings, names: list[str], *, optional: bool) -> list[dict[str, Any]]:
    specs = load_specs(settings)
    out: list[dict[str, Any]] = []
    for name in names:
        spec = specs.get(name)
        if spec is None:
            continue
        role = settings.llm_role(spec.role)
        out.append({"name": name, "optional": optional, "role": spec.role, "backend": role.backend,
                    "model": spec.model or role.model, "description": spec.description})
    return out


def _counts(db: Database, ledger: Any, project_id: int, subject: str) -> dict[str, Any]:
    rows = db.conn.execute(
        "SELECT id, version, model_ref FROM gen_specs_current WHERE project_id=? AND subject_type=?",
        (project_id, subject)).fetchall()
    made = stale = 0
    models: dict[str, int] = {}
    for r in rows:
        models[r["model_ref"]] = models.get(r["model_ref"], 0) + 1
        assets = ledger.list_assets(project_id=project_id, spec_id=r["id"], current_only=True, limit=1)
        if assets:
            made += 1
            if (assets[0].spec_version or 0) < r["version"]:
                stale += 1
    return {"specs": len(rows), "made": made, "stale": stale, "models": models}


def describe_flow(
    settings: Settings, db: Database, state: WorkflowState, ledger: Any, project_id: int | None = None,
) -> dict[str, Any]:
    status = {s["stage"]: s for s in state.stages(project_id)} if project_id is not None else {}
    steps: list[dict[str, Any]] = []
    for node in CHAIN:
        stage = node["stage"]
        step: dict[str, Any] = {
            "stage": stage, "how": node["how"], "reads": node["reads"], "writes": node["writes"],
            "agents": _agents(settings, node["agents"], optional=False) + _agents(settings, node["optional"],
                                                                                   optional=True),
            "media": [], "status": status.get(stage, {}).get("status"), "summary": status.get(stage, {}).get("summary"),
        }
        if stage == "produce":
            for subject, kind, phase in MEDIA:
                counts = _counts(db, ledger, project_id, subject) if project_id is not None else None
                refs = list(counts["models"]) if counts and counts["models"] else []
                if not refs:
                    try:
                        refs = [settings.default_model(kind.value).ref]
                    except ConfigError:
                        refs = []
                step["media"].append({
                    "subject": subject, "kind": kind.value, "phase": phase, "counts": counts,
                    "models": [_model_info(settings, r) for r in refs]})
        if stage == "compose":
            try:
                ref = settings.default_model(MediaKind.COMPOSE_ASSEMBLE.value).ref
                step["media"].append({"subject": "episode", "kind": MediaKind.COMPOSE_ASSEMBLE.value, "phase": 1,
                                      "counts": None, "models": [_model_info(settings, ref)]})
            except ConfigError:
                pass
        steps.append(step)
    assert [s["stage"] for s in steps] == list(STAGES)
    return {"steps": steps}
