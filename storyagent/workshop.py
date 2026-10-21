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

from storyagent.config import Settings
from storyagent.db import Database
from storyagent.domain import MediaKind, QualityTier
from storyagent.media.contracts import HealthState, JobRecord, MediaJob
from storyagent.media.ledger import Ledger
from storyagent.media.params import resolve_params
from storyagent.media.registry import AdapterRegistry
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
    type: str = "text"        # text | longtext | media | bool
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

MODULES: tuple[Module, ...] = (
    Module("image", (
        KindSpec(MediaKind.IMAGE_GENERATE, (_P, _NEG, Field("image", "refs[]", "media", "image"))),
        KindSpec(MediaKind.IMAGE_EDIT, (_P, Field("image", "refs[]", "media", "image", required=True))),
    )),
    Module("song", (
        KindSpec(MediaKind.AUDIO_MUSIC, (
            Field("prompt", "prompt", "longtext", required=True),
            Field("lyrics", "text", "longtext"),
            Field("reference", "refs[]", "media", "audio"),
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
    """What a kind produces, for matching it to the next step's media input: image | audio | video | text."""
    domain = MediaKind(kind).value.split(".")[0]
    return domain if domain in ("image", "audio", "video", "text") else ""


def build_inputs(spec: KindSpec, values: Mapping[str, Any], prev: JobRecord | None = None) -> dict[str, Any]:
    """Job inputs from the form values of one step. ``"@prev"`` becomes ``job:<id>`` of the previous step."""
    out: dict[str, Any] = {}
    for f in spec.inputs:
        raw = values.get(f.name)
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

    def describe(self) -> list[dict[str, Any]]:
        cache: dict[str, tuple[bool, str]] = {}
        out: list[dict[str, Any]] = []
        for m in MODULES:
            kinds: list[dict[str, Any]] = []
            for ks in m.kinds:
                kind = ks.kind.value
                preset = self.preset(kind)
                default = self.settings.default_models.get(kind, "")
                models = []
                for ref, spec in self.settings.models.items():
                    if spec.kind != kind:
                        continue
                    ok, note = self._health(spec.provider, cache)
                    models.append({
                        "ref": ref, "provider": spec.provider, "version": spec.version, "heavy": spec.heavy,
                        "est_memory_gib": spec.est_memory_gib, "license_tag": spec.license_tag,
                        "commercial_ok": spec.commercial_ok, "available": ok, "note": "" if ok else note,
                    })
                chosen = preset["model_ref"] if preset["model_ref"] in {x["ref"] for x in models} else default
                tier = preset["tier"] or QualityTier.FINAL.value
                provider = self.settings.model(chosen).provider if chosen else ""
                adapter = self.registry.get(provider) if provider else None
                schema = dict(adapter.capabilities().param_schema) if adapter else {}
                base = resolve_params(self.settings, chosen, tier) if chosen else {}
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
        inputs = build_inputs(spec, step.get("inputs") or {}, prev)
        job = MediaJob(
            kind=spec.kind, inputs=inputs, model_ref=model_ref, tier=tier, params=dict(step.get("params") or {}),
            seed=step.get("seed"), depends_on=prev.id if prev is not None and f"job:{prev.id}" in json.dumps(inputs)
            else None, pipeline_id=pipeline_id, pipeline_step=step_no,
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
