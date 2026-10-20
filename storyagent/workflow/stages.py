"""The pipeline stages. Each one is idempotent and resumable: it skips what already exists (unless ``force``) and
saves after every LLM call, so an interrupted or failed run continues where it stopped.

    analyze   premise -> production plan + visual style            (director.analyze, art.style)
    cast      plan -> characters                                    (character.design)
    script    outline -> per-scene shots with dialogue              (screenwriter.outline|adapt, .shots)
    scenes    script locations -> location designs                  (scene.design)
    music     theme song + scene BGM + sound effects                (composer.*, sound.design)
    shotplan  script -> shot rows (shot.v3), deterministic
    specs     everything above -> editable generation specs
    produce   specs -> pictures, voices, music, video (waits for the media worker; see workflow/media.py)
    compose   media -> the episode file (timeline + ffmpeg)

Params (all optional): ``premise``, ``episode`` (default 1), ``acts``, ``scenes_per_act``, ``shots_per_scene``,
``source_text`` (adapt an existing text instead of inventing), ``force`` (redo even if present), ``skip_sfx``,
``theme_song`` (default true), ``max_bgm``, ``visual_prompts`` (LLM image/video prompts per shot, default true).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from storyagent.agents import AgentRunner
from storyagent.agents.runner import language_name
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.projects import Project, ProjectStore
from storyagent.story import StoryStore

from .shotplan import build_shot_rows
from .state import WorkflowState

Progress = Callable[[str, int, int, str], None]


class WorkflowCancelled(RuntimeError):
    pass


class StageError(RuntimeError):
    """A stage cannot run (missing prerequisite). ``code`` is an i18n suffix ``workflow.error.<code>``."""

    def __init__(self, code: str, message: str = "", **params: object) -> None:
        super().__init__(message or code)
        self.code, self.params = code, params


@dataclass
class Ctx:
    settings: Settings
    db: Database
    store: StoryStore
    state: WorkflowState
    agents: AgentRunner
    projects: ProjectStore
    project: Project
    params: dict[str, Any]
    progress: Progress = lambda stage, done, total, msg: None
    cancelled: Callable[[], bool] = lambda: False
    spec_store: Any = None
    stage: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def pid(self) -> int:
        return self.project.id

    @property
    def language(self) -> str:
        return language_name(self.project.content_language)

    def force(self) -> bool:
        return bool(self.params.get("force"))

    def step(self, done: int, total: int, msg: str = "") -> None:
        if self.cancelled():
            raise WorkflowCancelled()
        self.progress(self.stage, done, total, msg)

    def run_agent(self, name: str, variables: dict[str, Any]) -> dict[str, Any]:
        if self.cancelled():
            raise WorkflowCancelled()
        return self.agents.run(name, {"language": self.language, **variables}, project_id=self.pid).data


# ---------------------------------------------------------------------------------------------------------
def _plan(ctx: Ctx) -> dict[str, Any]:
    plan = ctx.state.get_data(ctx.pid, "plan")
    if not plan:
        raise StageError("needs_stage", "run the analyze stage first", stage="analyze")
    return dict(plan)


def _style(ctx: Ctx) -> dict[str, Any]:
    return dict(ctx.state.get_data(ctx.pid, "style", {}) or {})


def _premise(ctx: Ctx) -> str:
    text = str(ctx.params.get("premise") or ctx.project.description or "").strip()
    if not text:
        raise StageError("no_premise", "the project has no description to start from")
    return text


def _character_brief(chars: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"name": c["name"], "role": c.get("role", ""), "look": str(c.get("appearance", ""))[:120]} for c in chars]


def analyze(ctx: Ctx) -> dict[str, Any]:
    acts = int(ctx.params.get("acts") or 4)
    ctx.step(0, 2, "plan")
    plan = ctx.run_agent("director.analyze", {
        "premise": _premise(ctx), "format": ctx.project.aspect_ratio + " vertical short drama", "acts": acts})
    plan["act_count"] = acts
    ctx.state.put_data(ctx.pid, "plan", plan)
    ctx.step(1, 2, "style")
    style = ctx.run_agent("art.style", {
        "title": plan.get("project_name", ctx.project.name), "genre": plan.get("genre", ""),
        "tone": plan.get("tone", ""), "keywords": plan.get("style_keywords", [])})
    ctx.state.put_data(ctx.pid, "style", style)
    patch = {}
    if plan.get("genre") and not ctx.project.genre:
        patch["genre"] = str(plan["genre"])
    if patch:
        ctx.projects.update(ctx.pid, **patch)
    ctx.step(2, 2, "done")
    return {"characters": len(plan.get("characters", [])), "locations": len(plan.get("locations", []))}


def cast(ctx: Ctx) -> dict[str, Any]:
    plan, style = _plan(ctx), _style(ctx)
    wanted = [c for c in plan.get("characters", []) if str(c.get("name", "")).strip()]
    created = skipped = 0
    for i, entry in enumerate(wanted):
        name = str(entry["name"]).strip()
        ctx.step(i, len(wanted), name)
        existing = ctx.store.find_character(ctx.pid, name)
        if existing and not ctx.force():
            skipped += 1
            continue
        data = ctx.run_agent("character.design", {
            "name": name, "role_hint": entry.get("role", ""), "story": plan.get("summary", ""),
            "others": [c.get("name", "") for c in wanted if c is not entry], "style": style.get("visual_style", "")})
        fields = {k: data.get(k, "") for k in ("role", "age", "gender", "appearance", "personality", "background",
                                              "voice_profile", "prompt_template")}
        fields["role"] = fields["role"] or entry.get("role", "")
        fields["relationships"] = data.get("relationships", [])
        # the image prompt is kept in prompt_template when the model did not give a template of its own
        fields["prompt_template"] = fields["prompt_template"] or data.get("image_prompt", "")
        if existing:
            ctx.store.update_character(existing["id"], fields)
        else:
            ctx.store.create_character(ctx.pid, {"name": name, **fields})
            created += 1
        ctx.extra.setdefault("character_extra", {})[name] = {
            "image_prompt": data.get("image_prompt", ""), "costume": data.get("costume", ""),
            "voice_sample_text": data.get("voice_sample_text", "")}
        ctx.state.put_data(ctx.pid, f"character_extra:{name}", ctx.extra["character_extra"][name])
    ctx.step(len(wanted), len(wanted), "done")
    return {"created": created, "skipped": skipped}


def _renumber(outline: dict[str, Any]) -> None:
    for a, act in enumerate(outline.get("acts", []), start=1):
        act["number"] = a
        for s, scene in enumerate(act.get("scenes", []), start=1):
            scene["number"] = s


def script(ctx: Ctx) -> dict[str, Any]:
    plan = _plan(ctx)
    episode = int(ctx.params.get("episode") or 1)
    chars = ctx.store.list_characters(ctx.pid)
    if not chars:
        raise StageError("needs_stage", "run the cast stage first", stage="cast")
    acts_n = int(ctx.params.get("acts") or plan.get("act_count") or 4)
    per_act = int(ctx.params.get("scenes_per_act") or 3)
    shots_n = int(ctx.params.get("shots_per_scene") or 3)
    existing = ctx.store.get_script(ctx.pid, episode)
    ctx.store.ensure_episode(ctx.pid, episode)

    if existing and existing["acts"] and not ctx.force():
        outline = {"title": existing["title"], "synopsis": existing["synopsis"], "acts": existing["acts"]}
    else:
        brief = _character_brief(chars)
        source = str(ctx.params.get("source_text") or "").strip()
        ctx.step(0, 1, "outline")
        if source:
            outline = ctx.run_agent("screenwriter.adapt", {"source": source[:24000], "acts": acts_n,
                                                           "scenes_per_act": per_act, "characters": brief})
        else:
            premise = _premise(ctx)
            if episode > 1:
                before = [s["synopsis"] for s in ctx.store.list_scripts(ctx.pid) if s["episode_no"] < episode]
                premise += f"\n\nThis is episode {episode} of a series. Earlier episodes: {' / '.join(before)}"
            outline = ctx.run_agent("screenwriter.outline", {
                "premise": premise, "genre": plan.get("genre", ctx.project.genre), "tone": plan.get("tone", ""),
                "acts": acts_n, "scenes_per_act": per_act, "characters": brief, "plan": plan.get("summary", "")})
        _renumber(outline)
        ctx.store.save_script(ctx.pid, episode, title=outline["title"], synopsis=outline.get("synopsis", ""),
                              acts=outline["acts"])

    scenes = [(act, sc) for act in outline["acts"] for sc in act.get("scenes", [])]
    names = [c["name"] for c in chars]
    previous = ""
    done = 0
    for act, scene in scenes:
        ctx.step(done, len(scenes), f"{act['number']}.{scene['number']}")
        if scene.get("shots") and not ctx.force():
            previous = scene.get("beat", "")
            done += 1
            continue
        data = ctx.run_agent("screenwriter.shots", {
            "title": outline["title"], "act_title": act.get("title", ""), "scene": scene,
            "characters": _character_brief(chars), "shots_per_scene": shots_n, "previous": previous or "(start)"})
        shots = data["shots"]
        for i, shot in enumerate(shots, start=1):
            shot["shot_number"] = i
        scene["shots"] = shots
        ctx.store.update_script_scene(ctx.pid, episode, act["number"], scene["number"], {"shots": shots})
        previous = scene.get("beat", "")
        done += 1
    unknown = sorted({str(line.get("character", "")).strip() for _, sc in scenes for sh in sc.get("shots", [])
                      for line in sh.get("dialogue", []) if str(line.get("character", "")).strip() not in names})
    ctx.store.ensure_episode(ctx.pid, episode, title=outline["title"], summary=outline.get("synopsis", ""))
    ctx.step(len(scenes), len(scenes), "done")
    return {"episode": episode, "acts": len(outline["acts"]), "scenes": len(scenes),
            "shots": sum(len(sc.get("shots", [])) for _, sc in scenes), "unknown_speakers": unknown}


def scenes(ctx: Ctx) -> dict[str, Any]:
    plan, style = _plan(ctx), _style(ctx)
    created = skipped = 0
    usage: dict[str, list[str]] = {}
    for sc in ctx.store.list_scripts(ctx.pid):
        for act in sc["acts"]:
            for scene in act.get("scenes", []):
                name = str(scene.get("location", "")).strip()
                if name:
                    usage.setdefault(name, []).append(str(scene.get("beat", "")))
    limit = int(ctx.params.get("max_locations") or 12)
    names = list(usage)[:limit]
    for i, name in enumerate(names):
        ctx.step(i, len(names), name)
        existing = ctx.store.find_location(ctx.pid, name)
        if existing and not ctx.force():
            skipped += 1
            continue
        data = ctx.run_agent("scene.design", {"name": name, "story": plan.get("summary", ""),
                                              "style": style.get("visual_style", ""), "usage": usage[name][:3]})
        fields = {k: data.get(k, "") for k in ("description", "lighting", "color_palette", "atmosphere",
                                              "prompt_template")}
        fields["prompt_template"] = fields["prompt_template"] or data.get("image_prompt", "")
        if existing:
            ctx.store.update_location(existing["id"], fields)
        else:
            ctx.store.create_location(ctx.pid, {"name": name, **fields})
            created += 1
        ctx.state.put_data(ctx.pid, f"location_extra:{name}", {"image_prompt": data.get("image_prompt", ""),
                                                               "weather": data.get("weather", "")})
    ctx.step(len(names), len(names), "done")
    return {"created": created, "skipped": skipped}


def _music_fields(data: dict[str, Any], kind: str, scene_ref: str = "") -> dict[str, Any]:
    return {"name": data.get("name", ""), "kind": kind, "mood": data.get("mood", ""),
            "tempo": str(data.get("tempo", "")), "instruments": data.get("instruments", ""),
            "key_signature": data.get("key_signature", ""), "description": data.get("description", ""),
            "gen_prompt": data.get("music_prompt", ""), "lyrics": data.get("lyrics", ""),
            "duration_sec": data.get("duration_hint_sec"), "scene_ref": scene_ref}


def music(ctx: Ctx) -> dict[str, Any]:
    plan = _plan(ctx)
    title = plan.get("project_name", ctx.project.name)
    existing = ctx.store.list_music(ctx.pid)
    made = 0
    jobs: list[tuple[str, dict[str, Any]]] = []
    if ctx.params.get("theme_song", True) and (ctx.force() or not any(m["kind"] == "theme" for m in existing)):
        jobs.append(("theme", {"subject": title, "mood": plan.get("tone", ""), "vocal": "song"}))
    moods: dict[str, str] = {}
    for sc in ctx.store.list_scripts(ctx.pid):
        for act in sc["acts"]:
            for scene in act.get("scenes", []):
                ref = f"act{int(act.get('number', 1)):02d}_scene{int(scene.get('number', 1)):02d}"
                moods.setdefault(str(scene.get("bgm_mood") or scene.get("mood") or ""), ref)
    have_refs = {m["scene_ref"] for m in existing if m["kind"] == "bgm"}
    for mood, ref in list(moods.items())[: int(ctx.params.get("max_bgm") or 4)]:
        if ref not in have_refs or ctx.force():
            jobs.append(("bgm", {"scene": ref, "mood": mood, "ref": ref}))
    total = len(jobs) + (0 if ctx.params.get("skip_sfx") else 1)
    for i, (kind, spec) in enumerate(jobs):
        ctx.step(i, total, kind)
        if kind == "theme":
            data = ctx.run_agent("composer.theme", {"title": title, "genre": plan.get("genre", ""),
                                                    "tone": plan.get("tone", ""), "subject": spec["subject"],
                                                    "mood": spec["mood"], "vocal": spec["vocal"]})
            ctx.store.create_music(ctx.pid, _music_fields(data, "theme"))
        else:
            names = [c["name"] for c in ctx.store.list_characters(ctx.pid)]
            data = ctx.run_agent("composer.bgm", {"scene": spec["scene"], "mood": spec["mood"], "characters": names})
            ctx.store.create_music(ctx.pid, _music_fields(data, "bgm", spec["ref"]))
        made += 1
    sfx = 0
    if not ctx.params.get("skip_sfx") and (ctx.force() or not ctx.store.list_sfx(ctx.pid)):
        ctx.step(len(jobs), total, "sfx")
        script_row = ctx.store.get_script(ctx.pid, int(ctx.params.get("episode") or 1))
        first = next((sc for act in (script_row or {}).get("acts", []) for sc in act.get("scenes", [])), None)
        if first:
            data = ctx.run_agent("sound.design", {
                "location": first.get("location", ""), "weather": first.get("weather", ""),
                "time_of_day": first.get("time_of_day", ""), "description": first.get("beat", ""),
                "actions": [s.get("action", "") for s in first.get("shots", [])]})
            for e in data["sound_effects"]:
                ctx.store.create_sfx(ctx.pid, {k: e.get(k, "") for k in ("name", "category", "description", "tags")})
                sfx += 1
    ctx.step(total, total, "done")
    return {"music": made, "sfx": sfx}


def shotplan(ctx: Ctx) -> dict[str, Any]:
    episode = int(ctx.params.get("episode") or 1)
    script_row = ctx.store.get_script(ctx.pid, episode)
    if script_row is None or not script_row["acts"]:
        raise StageError("needs_stage", "run the script stage first", stage="script")
    profile = ctx.settings.delivery_profile(ctx.project.delivery_profile)
    ep = ctx.store.ensure_episode(ctx.pid, episode)
    rows = build_shot_rows(
        script_row, characters=ctx.store.list_characters(ctx.pid), locations=ctx.store.list_locations(ctx.pid),
        style=_style(ctx), profile=profile,
        min_shot_sec=float(ctx.params.get("min_shot_sec", 2.0)),
        max_shot_sec=float(ctx.params.get("max_shot_sec", 8.0)),
    )
    created, kept = ctx.store.replace_episode_shots(ctx.pid, ep["id"], rows)
    ctx.store.update_episode(ep["id"], {"status": "planned"})
    unresolved = sorted({line["character"] for line in ctx.store.dialogue_lines(ctx.pid, ep["id"])
                         if line["character_id"] is None and line["character"]})
    ctx.step(1, 1, "done")
    return {"episode": episode, "shots": len(created), "kept_locked": len(kept), "unresolved_speakers": unresolved,
            "duration_sec": round(sum(float(r["render_payload"]["camera"]["duration_sec"]) for r in rows), 1)}


def specs(ctx: Ctx) -> dict[str, Any]:
    from .seed import seed_specs

    return seed_specs(ctx)


def produce(ctx: Ctx) -> dict[str, Any]:
    from .media import produce as run

    return run(ctx)


def compose(ctx: Ctx) -> dict[str, Any]:
    from .media import compose as run

    return run(ctx)


STAGE_FUNCS: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "analyze": analyze, "cast": cast, "script": script, "scenes": scenes, "music": music, "shotplan": shotplan,
    "specs": specs, "produce": produce, "compose": compose,
}


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
