"""Turn the story into editable generation specs (``author='agent'`` drafts).

Idempotent and respectful: a spec that does not exist is created; one the agent wrote earlier is refreshed (new
version) when the story changed; one a person edited (``author='user'``) or locked is never touched.
"""
from __future__ import annotations

import re
from typing import Any

from storyagent.domain import MediaKind
from storyagent.media.specs import SpecLocked, SpecRecord, SpecStore

from . import keyframe
from .stages import Ctx

PORTRAIT, VOICE, LOCATION = "character.portrait", "character.voice", "scene.keyframe"
KEYFRAME, VIDEO, DIALOGUE = "shot.keyframe", "shot.video", "dialogue.line"
THEME, BGM, SHOT_SFX = "music.theme_song", "music.bgm", "shot.sfx"


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


_COSTUME = re.compile(r"\b(wears|wearing|dressed in|dons|dresses)\b", re.IGNORECASE)


def _identity(template: str) -> str:
    """The character's identity-only description (face, hair, skin, build) with any costume sentence removed,
    so a shot prompt describes clothing per shot instead of copying the character's default outfit."""
    parts = re.split(r"(?<=[.!?])\s+", template)
    return " ".join(p for p in parts if not _COSTUME.search(p)).strip()


def _character_identity(template: str) -> str:
    """A short, clean identity (age, face, hair, skin, build) extracted from a character's English prompt_template,
    with the portrait framing, costume and style/lighting tail removed. Injected verbatim so the same person looks the
    same in every shot instead of relying on the prompter LLM to re-write it."""
    t = _identity(template)
    t = re.sub(r"^A\s+(?:cinematic\s+)?close-up\s+portrait\s+of\s+", "", t, flags=re.IGNORECASE)
    cut = len(t)
    for marker in ("{action}", "{setting}", "lighting", "cinematic", "8k", "resolution", "background", "atmosphere"):
        m = re.search(re.escape(marker), t, re.IGNORECASE)
        if m and m.start() < cut:
            cut = m.start()
    t = t[:cut].strip()
    t = re.sub(r"[^.!?]*$", "", t)  # drop a trailing partial sentence
    return t.strip().rstrip(",;. ")


_FEMALE = re.compile(r"\b(woman|female|girl|lady)\b", re.IGNORECASE)
_MALE = re.compile(r"\b(man|male|boy|gentleman)\b", re.IGNORECASE)
_ORDINALS = ("first", "second", "third", "fourth")


def _character_identities(shot: dict[str, Any], chars: dict[str, dict[str, Any]]) -> str:
    """'The woman is ... the man is ...' block for verbatim injection. A person is called by the sex their own
    description gives (never by the order they are listed in: a lone man is 'the man'); two of the same sex are told
    apart as 'the first woman' / 'the second woman'."""
    found: list[tuple[str, str]] = []
    for name in shot["characters"]:
        c = chars.get(name, {})
        ident = _character_identity(c.get("prompt_template", "") or c.get("appearance", ""))
        if not ident:
            continue
        noun = "woman" if _FEMALE.search(ident) else "man" if _MALE.search(ident) else "person"
        found.append((noun, ident[0].lower() + ident[1:]))
    total = {noun: sum(1 for n, _ in found if n == noun) for noun, _ in found}
    seen: dict[str, int] = {}
    parts: list[str] = []
    for noun, ident in found:
        seen[noun] = seen.get(noun, 0) + 1
        label = f"the {noun}" if total[noun] == 1 else f"the {_ORDINALS[min(seen[noun], 4) - 1]} {noun}"
        parts.append(f"{label} is {ident}.")
    return " ".join(parts)


def _shot_prompts(ctx: Ctx, shot: dict[str, Any], chars: dict[str, dict[str, Any]], style: dict[str, Any],
                  previous: str, use_llm: bool) -> dict[str, str]:
    payload = shot["render_payload"]
    composed = bool(ctx.settings.workflow.get("keyframe_from_references", True))
    worn = {c["name"]: c.get("costume", "") for c in payload["subject"]["characters"]}
    looks = [{"appearance": chars.get(n, {}).get("appearance", ""), "costume": worn.get(n, ""),
              "stable_description": _identity(chars.get(n, {}).get("prompt_template", ""))} for n in shot["characters"]]
    props = payload.get("continuity", {}).get("props", [])
    base_style = keyframe.clean(str(style.get("visual_style", "")))
    plain = ", ".join(x for x in (
        payload["camera"]["shot_type"],
        "; ".join(f"{lk['stable_description'] or lk['appearance']}"
                  + (f", wearing {lk['costume']}" if lk["costume"] else "") for lk in looks),
        "; ".join(f"{p['name']}: {p['description']}" if p["description"] else p["name"] for p in props),
        payload["subject"]["action"], payload["subject"]["expression"], shot["location"], payload["scene"]["lighting"],
        payload["scene"]["time_of_day"], base_style) if x)
    staging = payload.get("staging", {})
    lens = f"{staging['lens_mm']}mm lens" if staging.get("lens_mm") else ""
    opening = ", ".join(x for x in (plain, staging.get("start_pose", ""), staging.get("camera_position", ""), lens)
                        if x)
    motion = ", ".join(x for x in (payload["camera"]["movement"], staging.get("gait", ""),
                                   f"ends {staging['end_pose']}" if staging.get("end_pose") else "") if x)
    fallback = {"image_prompt": opening, "video_prompt": opening + (", " + motion if motion else ""),
                "negative_prompt": payload["style"]["negative_prompt"]}
    if not use_llm:
        return fallback
    try:
        keys = ("subject", "scene", "camera", "story", "staging")
        data = ctx.run_agent("shot.visual", {"shot": {k: payload[k] for k in keys if k in payload},
                                             "characters": looks, "props": props, "scene": shot["location"],
                                             "style": base_style,
                                             "tool_notes": keyframe.guide(with_references=composed),
                                             "previous": previous or "(start)"})
        return {k: str(data.get(k) or fallback[k]) for k in fallback}
    except Exception:  # an LLM hiccup on one shot must not stop the whole stage  # noqa: BLE001
        ctx.extra.setdefault("visual_prompt_fallbacks", []).append(shot["id"])
        return fallback


def _sfx_prompts(ctx: Ctx, shot: dict[str, Any], cues: list[str], use_llm: bool) -> dict[str, tuple[str, float]]:
    """cue -> (English prompt, seconds). Without the agent (or when it fails) the cue itself is the prompt."""
    if not use_llm:
        return {}
    payload = shot["render_payload"]
    try:
        data = ctx.run_agent("sound.cue", {"location": shot["location"], "action": payload["subject"]["action"],
                                           "time_of_day": payload["scene"]["time_of_day"], "cues": cues})
    except Exception:  # an LLM hiccup on one shot must not stop the whole stage  # noqa: BLE001
        ctx.extra.setdefault("sfx_prompt_fallbacks", []).append(shot["id"])
        return {}
    return {str(e["cue"]).strip(): (str(e["prompt"]), min(max(float(e.get("duration_sec") or 0), 1.0), 6.0))
            for e in data.get("effects", [])}


def seed_specs(ctx: Ctx) -> dict[str, Any]:
    specs: SpecStore = ctx.spec_store
    seeder = Seeder(specs, ctx.pid)
    store, style = ctx.store, dict(ctx.state.get_data(ctx.pid, "style", {}) or {})
    lang = ctx.project.content_language
    neg = keyframe.negative_prompt(str(style.get("negative_prompt", "")), language=lang)
    visual = str(style.get("visual_style", ""))
    chars = {c["name"]: c for c in store.list_characters(ctx.pid)}
    portraits: dict[str, SpecRecord] = {}
    voices: dict[str, SpecRecord] = {}
    total = len(chars) + 1

    for i, c in enumerate(chars.values()):
        ctx.step(i, total, c["name"])
        extra = ctx.state.get_data(ctx.pid, f"character_extra:{c['name']}", {}) or {}
        prompt = keyframe.image_prompt(extra.get("image_prompt") or c["prompt_template"] or c["appearance"],
                                       language=lang)
        params: dict[str, Any] = {}
        if c["lora_ref"]:
            params["loras"] = [{"path": c["lora_ref"], "scale": 0.8}]
        portraits[c["name"]] = seeder.sync(PORTRAIT, str(c["id"]), MediaKind.IMAGE_GENERATE, prompt=prompt,
                                           negative_prompt=neg, refs=list(c["ref_images"]), params=params,
                                           seed=keyframe.fixed_seed(ctx.pid, f"character:{c['name']}"))
        sample = str(extra.get("voice_sample_text") or ctx.settings.workflow.get("voice_sample_text") or "")
        # ^ the character designer writes a neutral sample; the config value is only the fallback
        vp = {"voice_ref": _voice_param(c)} if _voice_param(c) else {}
        vp["emotion"] = False          # the voice_profile above is a description, not a speaking tone
        voices[c["name"]] = seeder.sync(VOICE, str(c["id"]), MediaKind.AUDIO_TTS, prompt=c["voice_profile"],
                                        text_payload=sample, params=vp)

    locations: dict[str, SpecRecord] = {}
    for loc in store.list_locations(ctx.pid):
        extra = ctx.state.get_data(ctx.pid, f"location_extra:{loc['name']}", {}) or {}
        locations[loc["name"]] = seeder.sync(
            LOCATION, str(loc["id"]), MediaKind.IMAGE_GENERATE, negative_prompt=neg, refs=list(loc["ref_images"]),
            prompt=keyframe.scene_prompt(extra.get("image_prompt") or loc["prompt_template"] or loc["description"]),
            seed=keyframe.fixed_seed(ctx.pid, f"location:{loc['name']}"))

    for m in store.list_music(ctx.pid):
        if m["kind"] == "theme":
            seeder.sync(THEME, str(m["id"]), MediaKind.AUDIO_MUSIC, prompt=m["gen_prompt"], text_payload=m["lyrics"],
                        params={"duration": m["duration_sec"]} if m["duration_sec"] else {})
        else:
            seeder.sync(BGM, str(m["id"]), MediaKind.AUDIO_MUSIC, prompt=m["gen_prompt"],
                        params={"duration": m["duration_sec"]} if m["duration_sec"] else {})

    shots = store.list_shots(ctx.pid)
    use_llm = bool(ctx.params.get("visual_prompts", True))
    previous = ""

    narration_labels = {str(x).strip().lower() for x in
                        (ctx.settings.workflow.get("non_character_speakers") or ()) if str(x).strip()}
    # 旁白只做字幕的口径：workflow.narration_voiced = false 时，不为「旁白/narrator」这类说话人建 TTS spec。
    narration_voiced = bool(ctx.settings.workflow.get("narration_voiced", True))

    def sync_dialogue(shot: dict[str, Any]) -> None:
        """Spoken lines follow the story every time (cheap, deterministic): an edited line reaches its spec."""
        sid = str(shot["id"])
        for idx, line in enumerate(shot["dialogue"]):
            if not narration_voiced and str(line.get("character") or "").strip().lower() in narration_labels:
                continue
            who = chars.get(line["character"])
            vparam = _voice_param(who) if who else None
            seeder.sync(DIALOGUE, f"{sid}#{idx}", MediaKind.AUDIO_TTS, prompt=line.get("emotion", ""),
                        text_payload=line["line"], params={"voice_ref": vparam} if vparam else {},
                        depends_on=seeder.dep(voices.get(line["character"])))

    def sync_sfx(shot: dict[str, Any]) -> None:
        """One sound-effect spec per cue the shot lists; its English prompt is written once (an edit is kept)."""
        cues = [str(c).strip() for c in (shot["render_payload"].get("audio", {}) or {}).get("sfx_cues", [])
                if str(c).strip()]
        sid = str(shot["id"])
        if not cues or all(specs.current(ctx.pid, SHOT_SFX, f"{sid}#{i}") for i in range(len(cues))) \
                and not ctx.force():
            return
        written = _sfx_prompts(ctx, shot, cues, use_llm)
        for idx, cue in enumerate(cues):
            prompt, seconds = written.get(cue, (cue, 0.0))
            seeder.sync(SHOT_SFX, f"{sid}#{idx}", MediaKind.AUDIO_SFX, prompt=prompt, text_payload=cue,
                        params={"duration": seconds} if seconds else {})

    for i, shot in enumerate(shots):
        ctx.step(len(chars) + i, len(chars) + len(shots), f"shot {shot['id']}")
        sid = str(shot["id"])
        have = specs.current(ctx.pid, KEYFRAME, sid)
        if have is not None and not ctx.force():
            sync_dialogue(shot)
            sync_sfx(shot)
            previous = shot["narration"]
            continue
        prompts = _shot_prompts(ctx, shot, chars, style, previous, use_llm)
        deps = [d for n in shot["characters"] for d in seeder.dep(portraits.get(n))]
        deps += seeder.dep(locations.get(shot["location"]))
        refs: list[Any] = []
        for n in shot["characters"]:
            refs += list(chars.get(n, {}).get("ref_images", []))
        shown = prompts["image_prompt"]
        video_shown = prompts["video_prompt"]
        composed = bool(ctx.settings.workflow.get("keyframe_from_references", True))
        lead = shot["characters"][0] if shot["characters"] else f"location:{shot['location']}"
        if shot["characters"]:
            lead = f"character:{lead}"
            ident = _character_identities(shot, chars)
            if ident:
                shown = f"{shown} {ident}"
                video_shown = f"{video_shown} {ident}"
            shown = keyframe.image_prompt(shown, language=lang,
                                          with_portraits=composed and any(portraits.get(n) for n in shot["characters"]))
        else:
            shown = keyframe.scene_prompt(shown)
        shot_neg = (keyframe.negative_prompt(prompts["negative_prompt"], language=lang)
                    if prompts["negative_prompt"] else neg)
        key = seeder.sync(KEYFRAME, sid, MediaKind.IMAGE_GENERATE, prompt=shown,
                          negative_prompt=shot_neg, refs=refs, depends_on=deps,
                          seed=keyframe.fixed_seed(ctx.pid, lead))
        seeder.sync(VIDEO, sid, MediaKind.VIDEO_GENERATE, prompt=keyframe.clean(video_shown),
                    negative_prompt=shot_neg, depends_on=seeder.dep(key))
        sync_dialogue(shot)
        sync_sfx(shot)
        previous = shot["narration"]
    ctx.step(total + len(shots), total + len(shots), "done")
    return {"created": seeder.created, "refreshed": seeder.refreshed, "kept_user_edits": seeder.kept,
            "visual_prompt_fallbacks": len(ctx.extra.get("visual_prompt_fallbacks", [])),
            "visual_style_used": bool(visual)}
