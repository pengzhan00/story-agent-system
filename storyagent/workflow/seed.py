"""Turn the story into editable generation specs (``author='agent'`` drafts).

Idempotent and respectful: a spec that does not exist is created; one the agent wrote earlier is refreshed (new
version) when the story changed; one a person edited (``author='user'``) or locked is never touched.
"""
from __future__ import annotations

from typing import Any

from storyagent.domain import MediaKind
from storyagent.media.specs import SpecLocked, SpecRecord, SpecStore

from .stages import Ctx

PORTRAIT, VOICE, LOCATION = "character.portrait", "character.voice", "scene.keyframe"
KEYFRAME, VIDEO, DIALOGUE = "shot.keyframe", "shot.video", "dialogue.line"
THEME, BGM, SFX = "music.theme_song", "music.bgm", "sfx.cue"


def _voice_param(c: dict[str, Any]) -> Any:
    ref = str(c.get("voice_ref_audio", "")).strip()
    if not ref:
        return None
    if ref.startswith("library:"):
        return ref
    return {"audio": ref, "text": c.get("voice_ref_text", ""), "consent": bool(c.get("voice_ref_consent"))}


class Seeder:
    def __init__(self, specs: SpecStore, project_id: int) -> None:
        self.specs, self.pid = specs, project_id
        self.created = self.refreshed = self.kept = 0

    def sync(
        self, subject_type: str, subject_id: str, kind: MediaKind, *, slot: str = "main", **fields: Any
    ) -> SpecRecord:
        current = self.specs.current(self.pid, subject_type, subject_id, slot)
        if current is None:
            self.created += 1
            return self.specs.create(self.pid, subject_type, subject_id, kind, slot=slot, author="agent", **fields)
        same = all(getattr(current, k) == v for k, v in fields.items())
        if same:
            return current
        if current.author != "agent" or self.specs.is_locked(self.pid, subject_type, subject_id, slot):
            self.kept += 1
            return current
        try:
            self.refreshed += 1
            return self.specs.edit(self.pid, subject_type, subject_id, slot=slot, author="agent",
                                   note="story changed", **fields)
        except SpecLocked:
            self.refreshed -= 1
            self.kept += 1
            return current

    def dep(self, spec: SpecRecord | None) -> list[dict[str, Any]]:
        return [{"subject_type": spec.subject_type, "subject_id": spec.subject_id, "slot": spec.slot,
                 "version": spec.version}] if spec else []


def _shot_prompts(ctx: Ctx, shot: dict[str, Any], chars: dict[str, dict[str, Any]], style: dict[str, Any],
                  previous: str, use_llm: bool) -> dict[str, str]:
    payload = shot["render_payload"]
    looks = [{"appearance": chars.get(n, {}).get("appearance", ""), "costume": chars.get(n, {}).get("costume", ""),
              "stable_description": chars.get(n, {}).get("prompt_template", "")} for n in shot["characters"]]
    base_style = str(style.get("visual_style", ""))
    plain = ", ".join(x for x in (
        payload["camera"]["shot_type"], "; ".join(f"{lk['stable_description'] or lk['appearance']}" for lk in looks),
        payload["subject"]["action"], payload["subject"]["expression"], shot["location"], payload["scene"]["lighting"],
        payload["scene"]["time_of_day"], base_style) if x)
    fallback = {"image_prompt": plain, "video_prompt": plain + (", " + payload["camera"]["movement"]
                                                                  if payload["camera"]["movement"] else ""),
                "negative_prompt": payload["style"]["negative_prompt"]}
    if not use_llm:
        return fallback
    try:
        data = ctx.run_agent("shot.visual", {"shot": {k: payload[k] for k in ("subject", "scene", "camera", "story")},
                                             "characters": looks, "scene": shot["location"], "style": base_style,
                                             "previous": previous or "(start)"})
        return {k: str(data.get(k) or fallback[k]) for k in fallback}
    except Exception:  # an LLM hiccup on one shot must not stop the whole stage  # noqa: BLE001
        ctx.extra.setdefault("visual_prompt_fallbacks", []).append(shot["id"])
        return fallback


def seed_specs(ctx: Ctx) -> dict[str, Any]:
    specs: SpecStore = ctx.spec_store
    seeder = Seeder(specs, ctx.pid)
    store, style = ctx.store, dict(ctx.state.get_data(ctx.pid, "style", {}) or {})
    neg = str(style.get("negative_prompt", ""))
    visual = str(style.get("visual_style", ""))
    chars = {c["name"]: c for c in store.list_characters(ctx.pid)}
    portraits: dict[str, SpecRecord] = {}
    voices: dict[str, SpecRecord] = {}
    total = len(chars) + 1

    for i, c in enumerate(chars.values()):
        ctx.step(i, total, c["name"])
        extra = ctx.state.get_data(ctx.pid, f"character_extra:{c['name']}", {}) or {}
        prompt = extra.get("image_prompt") or c["prompt_template"] or c["appearance"]
        params: dict[str, Any] = {}
        if c["lora_ref"]:
            params["loras"] = [{"path": c["lora_ref"], "scale": 0.8}]
        portraits[c["name"]] = seeder.sync(PORTRAIT, str(c["id"]), MediaKind.IMAGE_GENERATE, prompt=prompt,
                                           negative_prompt=neg, refs=list(c["ref_images"]), params=params)
        sample = extra.get("voice_sample_text") or ""
        vp = {"voice_ref": _voice_param(c)} if _voice_param(c) else {}
        voices[c["name"]] = seeder.sync(VOICE, str(c["id"]), MediaKind.AUDIO_TTS, prompt=c["voice_profile"],
                                        text_payload=sample, params=vp)

    locations: dict[str, SpecRecord] = {}
    for loc in store.list_locations(ctx.pid):
        extra = ctx.state.get_data(ctx.pid, f"location_extra:{loc['name']}", {}) or {}
        locations[loc["name"]] = seeder.sync(
            LOCATION, str(loc["id"]), MediaKind.IMAGE_GENERATE, negative_prompt=neg, refs=list(loc["ref_images"]),
            prompt=extra.get("image_prompt") or loc["prompt_template"] or loc["description"])

    for m in store.list_music(ctx.pid):
        if m["kind"] == "theme":
            seeder.sync(THEME, str(m["id"]), MediaKind.AUDIO_MUSIC, prompt=m["gen_prompt"], text_payload=m["lyrics"],
                        params={"duration": m["duration_sec"]} if m["duration_sec"] else {})
        else:
            seeder.sync(BGM, str(m["id"]), MediaKind.AUDIO_MUSIC, prompt=m["gen_prompt"],
                        params={"duration": m["duration_sec"]} if m["duration_sec"] else {})
    for e in store.list_sfx(ctx.pid):
        seeder.sync(SFX, str(e["id"]), MediaKind.AUDIO_SFX, prompt=f"{e['description']} ({e['tags']})".strip())

    shots = store.list_shots(ctx.pid)
    use_llm = bool(ctx.params.get("visual_prompts", True))
    previous = ""
    for i, shot in enumerate(shots):
        ctx.step(len(chars) + i, len(chars) + len(shots), f"shot {shot['id']}")
        sid = str(shot["id"])
        have = specs.current(ctx.pid, KEYFRAME, sid)
        if have is not None and not ctx.force():
            previous = shot["narration"]
            continue
        prompts = _shot_prompts(ctx, shot, chars, style, previous, use_llm)
        deps = [d for n in shot["characters"] for d in seeder.dep(portraits.get(n))]
        deps += seeder.dep(locations.get(shot["location"]))
        refs: list[Any] = []
        for n in shot["characters"]:
            refs += list(chars.get(n, {}).get("ref_images", []))
        key = seeder.sync(KEYFRAME, sid, MediaKind.IMAGE_GENERATE, prompt=prompts["image_prompt"],
                          negative_prompt=prompts["negative_prompt"] or neg, refs=refs, depends_on=deps)
        seeder.sync(VIDEO, sid, MediaKind.VIDEO_GENERATE, prompt=prompts["video_prompt"],
                    negative_prompt=prompts["negative_prompt"] or neg, depends_on=seeder.dep(key))
        for idx, line in enumerate(shot["dialogue"]):
            who = chars.get(line["character"])
            vparam = _voice_param(who) if who else None
            seeder.sync(DIALOGUE, f"{sid}#{idx}", MediaKind.AUDIO_TTS, prompt=line.get("emotion", ""),
                        text_payload=line["line"], params={"voice_ref": vparam} if vparam else {},
                        depends_on=seeder.dep(voices.get(line["character"])))
        previous = shot["narration"]
    ctx.step(total + len(shots), total + len(shots), "done")
    return {"created": seeder.created, "refreshed": seeder.refreshed, "kept_user_edits": seeder.kept,
            "visual_prompt_fallbacks": len(ctx.extra.get("visual_prompt_fallbacks", [])),
            "visual_style_used": bool(visual)}
