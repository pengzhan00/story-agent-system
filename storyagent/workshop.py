"""Workshop: every media module works on its own, or as a step of a pipeline.

A *module* groups the media kinds that belong together for the person using it (image, song, voice, voice swap,
video, transcript). Each kind lists the inputs it takes. Nothing here is hard-wired to a model: the model, the
quality tier and the parameter overrides a module starts from are *presets* (table ``workshop_presets``), on top of
``models.toml``; a run can override all of them once.

A pipeline is a list of steps submitted together. A step may take the output of the one before it by naming
``"@prev"`` as an input value; it becomes the reference ``job:<id>`` and the step waits until that job completed
(``media_jobs.depends_on``). If a step fails, the steps waiting for it fail too (``dependency_failed``).
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from storyagent.compose.timeline import AssetRef, ShotInput, TimelineInput, build_timeline
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.domain import MediaKind, QualityTier
from storyagent.media.compliance import visible_marks
from storyagent.media.contracts import AssetRecord, HealthState, JobRecord, MediaJob
from storyagent.media.ledger import Ledger, LedgerError
from storyagent.media.library import Library
from storyagent.media.params import resolve_params
from storyagent.media.registry import AdapterRegistry
from storyagent.pipelines import settings_for
from storyagent.util import new_id, now_iso


class WorkshopError(ValueError):
    """The request cannot be turned into jobs (``code`` is an ``api.error.<code>`` key)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Field:
    name: str                 # id in the UI and in a run request
    target: str               # where it lands in the job inputs: "prompt", "voice_ref.audio", "refs[]"
    type: str = "text"        # text | longtext | media | medialist | shots | bool
    media: str = ""           # for type == "media": image | audio | video
    required: bool = False


@dataclass(frozen=True)
class KindSpec:
    kind: MediaKind
    inputs: tuple[Field, ...]


@dataclass(frozen=True)
class Module:
    id: str
    kinds: tuple[KindSpec, ...]


_P = Field("prompt", "prompt", "longtext", required=True)
_NEG = Field("negative_prompt", "negative_prompt", "text")
# A LoRA is a model input in the form: it becomes `params.loras = [{path, scale}]` of the job.
_LORA = (Field("lora", "loras[]", "media", "lora"), Field("lora_scale", "lora_scale", "text"))

MODULES: tuple[Module, ...] = (
    Module("story", (
        KindSpec(MediaKind.TEXT_STORY, (
            Field("idea", "idea", "longtext", required=True),
            Field("genre", "genre", "text"),
            Field("tone", "tone", "text"),
            Field("language", "language", "text"),
            Field("length", "length", "text"),
            Field("notes", "notes", "longtext"),
        )),
    )),
    Module("script", (
        KindSpec(MediaKind.TEXT_SCRIPT, (
            Field("story", "story", "media", "text"),
            Field("story_text", "story_text", "longtext"),
            Field("format", "format", "text"),
            Field("scenes", "scenes", "text"),
            Field("language", "language", "text"),
            Field("notes", "notes", "longtext"),
        )),
    )),
    Module("image", (
        KindSpec(MediaKind.IMAGE_GENERATE, (_P, _NEG, Field("image", "refs[]", "media", "image"), *_LORA)),
        KindSpec(MediaKind.IMAGE_EDIT, (_P, Field("image", "refs[]", "media", "image", required=True), *_LORA)),
    )),
    Module("episode", (
        KindSpec(MediaKind.COMPOSE_ASSEMBLE, (
            Field("shots", "timeline", "shots", "video", required=True),
            Field("bgm", "bgm", "media", "audio"),
            Field("title", "title", "text"),
            Field("profile", "profile", "text"),
        )),
    )),
    Module("lora", (
        KindSpec(MediaKind.TRAIN_LORA, (
            Field("images", "images[]", "medialist", "image", required=True),
            Field("trigger", "trigger", "text", required=True),
            Field("description", "description", "text"),
            Field("likeness_consent", "consent", "bool"),
        )),
    )),
    Module("sound", (
        KindSpec(MediaKind.AUDIO_SFX, (_P,)),
    )),
    Module("song", (
        KindSpec(MediaKind.AUDIO_MUSIC, (
            Field("prompt", "prompt", "longtext", required=True),
            Field("lyrics", "text", "longtext"),
            Field("reference", "refs[]", "media", "audio"),
            Field("source", "source_audio", "media", "audio"),
            Field("parent", "parent_audio", "media", "audio"),
        )),
    )),
    Module("voice", (
        KindSpec(MediaKind.AUDIO_TTS, (
            Field("text", "text", "longtext", required=True),
            Field("voice", "voice_ref.audio", "media", "audio", required=True),
            Field("voice_text", "voice_ref.text", "text"),
            Field("consent", "voice_ref.consent", "bool"),
            Field("instruct", "instruct", "text"),
        )),
        KindSpec(MediaKind.AUDIO_CONVERT, (
            Field("audio", "audio", "media", "audio", required=True),
            Field("voice", "voice_ref.audio", "media", "audio", required=True),
            Field("consent", "voice_ref.consent", "bool"),
        )),
    )),
    Module("separate", (
        KindSpec(MediaKind.AUDIO_SEPARATE, (Field("audio", "audio", "media", "audio", required=True),)),
    )),
    Module("video", (
        KindSpec(MediaKind.VIDEO_AV, (_P, _NEG, Field("image", "refs[]", "media", "image"))),
        KindSpec(MediaKind.VIDEO_GENERATE, (_P, _NEG, Field("image", "refs[]", "media", "image"))),
        KindSpec(MediaKind.VIDEO_AVATAR, (
            Field("image", "image", "media", "image", required=True),
            Field("audio", "audio", "media", "audio", required=True),
            Field("prompt", "prompt", "longtext"),
        )),
        KindSpec(MediaKind.VIDEO_LIPSYNC, (
            Field("image", "refs[]", "media", "image", required=True),
            Field("audio", "refs[]", "media", "audio", required=True),
            Field("prompt", "prompt", "longtext"),
        )),
    )),
    Module("transcript", (
        KindSpec(MediaKind.TEXT_ASR, (Field("audio", "audio", "media", "audio", required=True),)),
    )),
    Module("post", (
        KindSpec(MediaKind.AUDIO_ENHANCE, (Field("audio", "refs[]", "media", "audio", required=True),)),
        KindSpec(MediaKind.VIDEO_ENHANCE, (Field("video", "refs[]", "media", "video", required=True),)),
    )),
)

PREV = "@prev"


def kind_spec(kind: MediaKind | str) -> KindSpec:
    value = MediaKind(kind)
    for m in MODULES:
        for k in m.kinds:
            if k.kind is value:
                return k
    raise WorkshopError("workshop_unknown_kind", str(kind))


def output_media(kind: MediaKind | str) -> str:
    """What a kind produces, for matching it to the next step's media input: image | audio | video | text | lora."""
    value = MediaKind(kind)
    if value is MediaKind.TRAIN_LORA:
        return "lora"
    if value is MediaKind.COMPOSE_ASSEMBLE:
        return "video"
    domain = value.value.split(".")[0]
    return domain if domain in ("image", "audio", "video", "text") else ""


def build_inputs(spec: KindSpec, values: Mapping[str, Any], prev: JobRecord | None = None) -> dict[str, Any]:
    """Job inputs from the form values of one step. ``"@prev"`` becomes ``job:<id>`` of the previous step."""
    out: dict[str, Any] = {}
    for f in spec.inputs:
        raw = values.get(f.name)
        if f.type == "medialist":
            items = [str(x).strip() for x in (raw if isinstance(raw, list) else [raw]) if x and str(x).strip()]
            if not items:
                if f.required:
                    raise WorkshopError("workshop_missing_input", f.name)
                continue
            out.setdefault(f.target[:-2], []).extend(items)
            continue
        if isinstance(raw, str):
            raw = raw.strip()
        if raw in (None, "", False) and not (f.type == "bool" and raw is False):
            if f.required:
                raise WorkshopError("workshop_missing_input", f.name)
            continue
        if raw == PREV:
            if prev is None:
                raise WorkshopError("pipeline_no_previous", f.name)
            if f.type != "media" or output_media(prev.kind) != f.media:
                raise WorkshopError("pipeline_incompatible", f"{prev.kind.value} -> {spec.kind.value}.{f.name}")
            raw = f"job:{prev.id}"
        if f.target.endswith("[]"):
            out.setdefault(f.target[:-2], []).append(raw)
        elif "." in f.target:
            head, tail = f.target.split(".", 1)
            out.setdefault(head, {})[tail] = raw
        else:
            out[f.target] = raw
    return out


class Workshop:
    def __init__(self, settings: Settings, db: Database, ledger: Ledger, registry: AdapterRegistry) -> None:
        self.settings, self.db, self.ledger, self.registry = settings, db, ledger, registry

    # -- presets -------------------------------------------------------------------------------
    def preset(self, kind: str) -> dict[str, Any]:
        row = self.db.conn.execute("SELECT * FROM workshop_presets WHERE kind=?", (kind,)).fetchone()
        if row is None:
            return {"model_ref": "", "tier": "", "params": {}}
        return {"model_ref": row["model_ref"], "tier": row["tier"], "params": json.loads(row["params"] or "{}")}

    def save_preset(self, kind: str, model_ref: str, tier: str, params: Mapping[str, Any]) -> dict[str, Any]:
        kind = MediaKind(kind).value
        if model_ref:
            model = self.settings.model(model_ref)
            if model.kind != kind:
                raise WorkshopError("workshop_wrong_model", f"{model_ref} is {model.kind}, not {kind}")
        if tier:
            QualityTier(tier)
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO workshop_presets(kind, model_ref, tier, params, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(kind) DO UPDATE SET model_ref=excluded.model_ref, tier=excluded.tier, "
                "params=excluded.params, updated_at=excluded.updated_at",
                (kind, model_ref, tier, json.dumps(dict(params), ensure_ascii=False), now_iso()),
            )
        return self.preset(kind)

    def reset_preset(self, kind: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM workshop_presets WHERE kind=?", (MediaKind(kind).value,))

    # -- what the page shows -------------------------------------------------------------------
    def _health(self, provider: str, cache: dict[str, tuple[bool, str]]) -> tuple[bool, str]:
        if provider not in cache:
            adapter = self.registry.get(provider)
            if adapter is None:
                cache[provider] = (False, "no adapter")
            else:
                h = adapter.health()
                cache[provider] = (h.state is HealthState.OK, h.detail or h.state.value)
        return cache[provider]

    def describe(self, pipeline: str = "") -> list[dict[str, Any]]:
        """The modules with their models. With a ``pipeline`` the defaults are that plan's models and every model says
        whether it belongs to the plan: the others can be tried here, but are not for a project (a project runs on
        one complete plan)."""
        cfg = settings_for(self.settings, pipeline)
        in_plan = set(cfg.pipeline(pipeline).models.values()) if pipeline else set()
        cache: dict[str, tuple[bool, str]] = {}
        out: list[dict[str, Any]] = []
        for m in MODULES:
            kinds: list[dict[str, Any]] = []
            for ks in m.kinds:
                kind = ks.kind.value
                preset = self.preset(kind)
                default = cfg.default_models.get(kind, "")
                models = []
                for ref, spec in self.settings.models.items():
                    if spec.kind != kind:
                        continue
                    ok, note = self._health(spec.provider, cache)
                    models.append({
                        "ref": ref, "provider": spec.provider, "version": spec.version, "heavy": spec.heavy,
                        "est_memory_gib": spec.est_memory_gib, "license_tag": spec.license_tag,
                        "commercial_ok": spec.commercial_ok, "available": ok, "note": "" if ok else note,
                        "in_plan": (not pipeline) or ref in in_plan,
                    })
                chosen = preset["model_ref"] if preset["model_ref"] in {x["ref"] for x in models} else default
                tier = preset["tier"] or QualityTier.FINAL.value
                provider = self.settings.model(chosen).provider if chosen else ""
                adapter = self.registry.get(provider) if provider else None
                schema = dict(adapter.capabilities().param_schema) if adapter else {}
                base = resolve_params(cfg, chosen, tier) if chosen else {}
                kinds.append({
                    "kind": kind, "model_ref": chosen, "default_model": default, "tier": tier, "models": models,
                    "base_params": base,
                    "preset_params": preset["params"] if preset["model_ref"] in ("", chosen) else {},
                    "schema": schema, "inputs": [f.__dict__ for f in ks.inputs], "output": output_media(ks.kind),
                })
            out.append({"id": m.id, "kinds": kinds})
        return out

    def params_for(self, kind: str, model_ref: str, tier: str) -> dict[str, Any]:
        """What a run of ``model_ref`` at ``tier`` would use: the model + tier layers (``base``), the saved changes
        that belong to this model (``preset``) and their merge (``effective``), plus the adapter's parameter schema."""
        kind = MediaKind(kind).value
        model = self.settings.model(model_ref)
        if model.kind != kind:
            raise WorkshopError("workshop_wrong_model", f"{model_ref} is {model.kind}, not {kind}")
        base = resolve_params(self.settings, model_ref, QualityTier(tier or QualityTier.FINAL.value))
        preset = self.preset(kind)
        saved = preset["params"] if preset["model_ref"] in ("", model_ref) else {}
        adapter = self.registry.get(model.provider)
        return {"base": base, "preset": saved, "effective": {**base, **saved},
                "schema": dict(adapter.capabilities().param_schema) if adapter else {}}

    # -- episode assembly ----------------------------------------------------------------------
    def _asset(self, ref: object, what: str) -> AssetRecord | None:
        """The stored asset behind ``asset:<id>`` / ``library:<role>/<name>`` / ``job:<id>`` (``None`` = not given)."""
        raw = str(ref or "").strip()
        if not raw:
            return None
        kind, _, rest = raw.partition(":")
        try:
            if kind == "asset":
                return self.ledger.get_asset(rest)
            if kind == "job":
                made = [a for a in self.ledger.list_assets(job_id=rest) if not a.trashed_at]
                if made:
                    return made[-1]
            elif kind == "library":
                role, _, name = rest.partition("/")
                item = Library(self.db, self.settings, self.ledger).find(role, name)
                if item is not None and item.asset is not None:
                    return item.asset
        except LedgerError:
            pass
        raise WorkshopError("workshop_bad_value", f"{what}: {raw}")

    @staticmethod
    def _ref(a: AssetRecord | None, kind: str) -> AssetRef | None:
        return AssetRef(a.id, kind, a.duration_sec) if a else None

    def episode_inputs(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """The timeline of an episode put together by hand: one row per shot (picture, spoken line, its audio).
        The same builder as a project's compose stage: speech is measured first and sets the length of the shot."""
        rows = values.get("shots")
        shots: list[ShotInput] = []
        for n, row in enumerate(rows if isinstance(rows, list) else [], 1):
            if not isinstance(row, Mapping):
                continue
            video, image = self._asset(row.get("video"), "video"), self._asset(row.get("image"), "image")
            speech, text = self._asset(row.get("speech"), "speech"), str(row.get("text") or "").strip()
            narration = str(row.get("narration") or "").strip()
            if not (video or image or text or narration):
                continue
            try:
                seconds = float(row.get("seconds") or 3.0)
            except (TypeError, ValueError):
                raise WorkshopError("workshop_bad_value", f"shots[{n}].seconds") from None
            shots.append(ShotInput(
                shot_id=n, label=str(n), planned_sec=seconds, video=self._ref(video, "video.generate"),
                image=self._ref(image, "image.generate"),
                lines=[(text, self._ref(speech, "audio.tts"))] if (text or speech) else [], narration=narration))
        if not shots:
            raise WorkshopError("workshop_missing_input", "shots")
        title = str(values.get("title") or "").strip()
        profile = self.settings.delivery_profile(str(values.get("profile") or "").strip() or None)
        label, metadata = visible_marks(self.settings, None, self.settings.locale, title)
        timeline = build_timeline(TimelineInput(
            shots, profile, self.settings.delivery["mix"], self.settings.delivery["subtitles"],
            bgm=self._ref(self._asset(values.get("bgm"), "bgm"), "audio.music"), title=title, label=label,
            metadata=metadata))
        return {"timeline": timeline}

    # -- running -------------------------------------------------------------------------------
    def _job(self, spec: KindSpec, step: Mapping[str, Any], prev: JobRecord | None, *, pipeline_id: str | None,
             step_no: int | None) -> tuple[MediaJob, dict[str, Any]]:
        kind = spec.kind.value
        preset = self.preset(kind)
        model_ref = str(step.get("model_ref") or preset["model_ref"] or "")
        model_ref = model_ref or self.settings.default_model(kind).ref
        tier = QualityTier(step.get("tier") or preset["tier"] or QualityTier.FINAL.value)
        # the preset's parameters belong to the model it was saved for
        saved = preset["params"] if preset["model_ref"] in ("", model_ref) else {}
        if spec.kind is MediaKind.COMPOSE_ASSEMBLE:
            inputs = self.episode_inputs(step.get("inputs") or {})
        else:
            inputs = build_inputs(spec, step.get("inputs") or {}, prev)
        depends_on = prev.id if prev is not None and f"job:{prev.id}" in json.dumps(inputs) else None
        params = dict(step.get("params") or {})
        scale = str(inputs.pop("lora_scale", "") or "").strip()
        if inputs.get("loras") and "loras" not in params:
            try:
                weight = float(scale) if scale else 1.0
            except ValueError:
                raise WorkshopError("workshop_bad_value", "lora_scale") from None
            params["loras"] = [{"path": ref, "scale": weight} for ref in inputs["loras"]]
        inputs.pop("loras", None)
        job = MediaJob(
            kind=spec.kind, inputs=inputs, model_ref=model_ref, tier=tier, params=params, seed=step.get("seed"),
            depends_on=depends_on, pipeline_id=pipeline_id, pipeline_step=step_no,
        )
        return job, {"models": {model_ref: saved}}

    def run(self, kind: str, step: Mapping[str, Any], count: int = 1) -> list[JobRecord]:
        """One module on its own: ``count`` candidates (different seeds) of the same request."""
        spec = kind_spec(kind)
        jobs: list[JobRecord] = []
        for i in range(max(1, min(count, 8))):
            job, project_settings = self._job(spec, step, None, pipeline_id=None, step_no=None)
            if i and step.get("seed") is not None:
                job = MediaJob(**{**job.__dict__, "seed": int(step["seed"]) + i})
            rec, _ = self.ledger.enqueue(job, project_settings)
            jobs.append(rec)
        return jobs

    def pipeline(self, steps: Sequence[Mapping[str, Any]]) -> tuple[str, list[JobRecord]]:
        """Submit the steps together. Raises before anything is queued when a step does not fit the one before."""
        if not steps:
            raise WorkshopError("workshop_empty_pipeline")
        pid = new_id()
        planned: list[tuple[KindSpec, Mapping[str, Any]]] = [(kind_spec(s["kind"]), s) for s in steps]
        # dry run on placeholder records: catches an incompatible hand-over before any job exists
        prev_kind: MediaKind | None = None
        for spec, step in planned:
            for f in spec.inputs:
                if (step.get("inputs") or {}).get(f.name) == PREV:
                    if prev_kind is None:
                        raise WorkshopError("pipeline_no_previous", f.name)
                    if f.type != "media" or output_media(prev_kind) != f.media:
                        raise WorkshopError("pipeline_incompatible", f"{prev_kind.value} -> {spec.kind.value}.{f.name}")
            prev_kind = spec.kind
        jobs: list[JobRecord] = []
        prev: JobRecord | None = None
        for n, (spec, step) in enumerate(planned, 1):
            job, project_settings = self._job(spec, step, prev, pipeline_id=pid, step_no=n)
            prev, _ = self.ledger.enqueue(job, project_settings)
            jobs.append(prev)
        return pid, jobs

    # -- history -------------------------------------------------------------------------------
    def history(self, kinds: Sequence[str] | None, limit: int = 40) -> list[tuple[JobRecord, list[Any]]]:
        jobs = self.ledger.list_jobs(standalone=True, kinds=kinds, limit=limit)
        return [(j, self.ledger.list_assets(job_id=j.id)) for j in jobs]
