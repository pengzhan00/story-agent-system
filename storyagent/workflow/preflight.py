"""Before a run that goes all the way to the film: is everything it will need there?

A full run writes the story for many minutes and then spends hours on pictures, voices and clips. Without this look
ahead a picture service that was never started is found out after the story is written, as one failed job per shot.
``preflight`` only reads: it asks each language-model backend and each media adapter the run will use whether it is
ready, and whether the people of the story have a voice. A ``block`` finding stops a full run before it starts (the
answer says what to start or install); a ``warn`` finding is shown and the run goes on.

Findings carry a code (i18n ``preflight.<code>``), the thing it is about, a detail and named values for the message;
nothing here starts, stops or installs anything.

What can keep a run from its film, each kind of thing in one place (no two checks look at the same thing):

==================  =======================================================  =====================================
what is missing     looked at by                                             findings
==================  =======================================================  =====================================
someone to write    ``check_llm``: the role, its backend, its model           ``llm_role`` ``llm_down``
                                                                              ``llm_model_missing``
something to make   ``check_media``: a model for the kind, its adapter, its   ``no_model`` ``no_adapter``
with                service, its weights                                      ``service_down`` ``not_installed``
                                                                              ``degraded``
leave to make it    the content gates of the project's rating                 ``gate``
inputs that fit     ``check_references``: what each model states it takes     ``too_many_refs`` ``refs_dropped``
                    (``media/refs.py``), known before the story is drawn      (``_only``: no other model takes them)
                    ``check_jobs``: the worker's own gate (``media/gate.py``) ``refused``
                    asked about every job that can be built now
voices              ``check_voices``                                          ``voices_auto`` ``voice_shared``
                                                                              ``no_voice``
someone to run it   ``check_worker``: the sign a scheduling worker leaves     ``no_worker`` (told: the run waits in
                    (``media/worker.py``), looked at before a run is queued   the queue until one is started)
==================  =======================================================  =====================================

Every way work starts goes through the same checks: a run to the film and a run of single stages are looked at
before they are queued (the API refuses with ``not_ready``) and again by the worker before the first media job; a
single job (the workshop, a spec made again by hand, the command line) meets ``media/gate.py`` in the worker.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from storyagent.agents.registry import load_specs
from storyagent.config import ConfigError, Settings
from storyagent.db import Database
from storyagent.domain import MediaKind
from storyagent.llm import LLMError, LLMRouter
from storyagent.media import gate, refs
from storyagent.media.assetrefs import Resolver
from storyagent.media.contracts import HealthState, MediaJob
from storyagent.media.ledger import Ledger
from storyagent.media.registry import AdapterRegistry
from storyagent.media.specs import SpecRecord
from storyagent.media.tts import automatic_voice

from .state import STAGES

BLOCK, WARN = "block", "warn"
MEDIA_STAGES = ("produce", "compose")
# what a story needs made, when it has no specs yet to tell
USUAL_KINDS = (MediaKind.IMAGE_GENERATE, MediaKind.AUDIO_TTS, MediaKind.AUDIO_MUSIC, MediaKind.AUDIO_SFX,
               MediaKind.VIDEO_GENERATE)
OPTIONAL_KINDS = {MediaKind.AUDIO_SFX.value, MediaKind.AUDIO_MUSIC.value, MediaKind.IMAGE_EDIT.value}
# a film stands without these: a job of this kind that would be refused is told, it does not stop the run
SPARE_KINDS = {MediaKind.AUDIO_SFX.value, MediaKind.AUDIO_MUSIC.value}
NAMED = 6                   # how many names a finding spells out


@dataclass(frozen=True)
class Finding:
    code: str
    level: str
    subject: str = ""
    detail: str = ""
    values: dict[str, str] = field(default_factory=dict)     # further {placeholders} of the message

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RefUse:
    """One thing to make that carries reference images: which model will make it and how many it is given."""

    spec_id: str
    subject_type: str
    kind: str
    model_ref: str
    count: int
    name: str = ""          # who it is, for a person to read (a character's name)


def blocking(findings: Iterable[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == BLOCK]


def stage_agents(settings: Settings, stages: Sequence[str]) -> dict[str, set[str]]:
    """LLM role -> the agents of ``stages`` that run on it."""
    from .flow import CHAIN

    specs = load_specs(settings)
    out: dict[str, set[str]] = {}
    for step in CHAIN:
        if step["stage"] not in stages:
            continue
        for name in step["agents"]:
            if name in specs:
                out.setdefault(specs[name].role, set()).add(name)
    return out


def check_llm(settings: Settings, router: LLMRouter, project_id: int, stages: Sequence[str]) -> list[Finding]:
    """Every language-model role the story stages of this run use: its backend answers and has the model."""
    out: list[Finding] = []
    seen: dict[tuple[str, str], LLMError | None] = {}
    for role in sorted(stage_agents(settings, stages)):
        try:
            backend, model, _ = router.resolve(role, project_id=project_id)
        except (ConfigError, KeyError) as exc:
            out.append(Finding("llm_role", BLOCK, role, str(exc)))
            continue
        if (backend, model) not in seen:
            try:
                router.check_role(role, project_id=project_id)
                seen[(backend, model)] = None
            except LLMError as exc:
                seen[(backend, model)] = exc
        problem = seen[(backend, model)]
        if problem is not None:
            code = "llm_model_missing" if problem.code == "model_missing" else "llm_down"
            out.append(Finding(code, BLOCK, f"{backend}:{model}", role))
    return out


def models_needed(cfg: Settings, specs: Sequence[SpecRecord] | None, project_settings: dict[str, Any], *,
                  compose: bool = True) -> tuple[dict[str, set[str]], list[Finding]]:
    """provider -> the models of this project's plan that making ``specs`` will use (``None``: the story has no specs
    yet, so what a story usually needs), and the kinds that have no model. A clip that carries its own sound is made
    by the plan's ``video.av`` model; a shot keyframe may be composed by its image-edit model; ``compose`` adds the
    assembler."""
    sound = bool(project_settings.get("video_with_sound"))
    wanted: list[tuple[str, str]] = []
    for s in specs or ():
        if s.subject_type == "shot.video" and sound and s.params.get("mode") != "a2v":
            wanted.append((MediaKind.VIDEO_AV.value, ""))
        else:
            wanted.append((s.kind.value, s.model_ref))
    if specs is None:
        wanted = [((MediaKind.VIDEO_AV if sound and k is MediaKind.VIDEO_GENERATE else k).value, "")
                  for k in USUAL_KINDS]
    if cfg.workflow.get("keyframe_from_references", True) and (
            specs is None or any(s.subject_type == "shot.keyframe" for s in specs)):
        wanted.append((MediaKind.IMAGE_EDIT.value, ""))
    if compose:
        wanted.append((MediaKind.COMPOSE_ASSEMBLE.value, ""))
    providers: dict[str, set[str]] = {}
    missing: list[Finding] = []
    for kind, ref in dict.fromkeys(wanted):
        try:
            model = cfg.model(ref) if ref else cfg.default_model(kind)
        except ConfigError:
            if not ref and not any(f.subject == kind for f in missing):
                missing.append(Finding("no_model", WARN if kind in OPTIONAL_KINDS else BLOCK, kind))
            continue
        providers.setdefault(model.provider, set()).add(model.ref)
    return providers, missing


def tool_states(cfg: Settings) -> dict[str, tuple[HealthState, str]]:
    """provider -> (state, what is missing) for the tools that run as subprocesses (the healthcheck's ``tool.<name>``
    entries): their adapters have no service to ask, so their command and weights are looked at on disk."""
    from storyagent.media.healthcheck import tool_entries

    return {e.name.removeprefix("tool."): (e.state, ", ".join(e.missing) or e.detail) for e in tool_entries(cfg)}


def check_media(cfg: Settings, registry: AdapterRegistry, specs: Sequence[SpecRecord] | None,
                project_settings: dict[str, Any], *, compose: bool = True,
                tools: Mapping[str, tuple[HealthState, str]] | None = None) -> list[Finding]:
    """Every adapter the project's models run on says it is ready (its service answers), and for a tool that runs as
    a subprocess its command and weights exist (``tools`` from ``tool_states``; ``None`` = not looked at)."""
    providers, out = models_needed(cfg, specs, project_settings, compose=compose)
    for provider in sorted(providers):
        models = ", ".join(sorted(providers[provider]))
        adapter = registry.get(provider)
        if adapter is None:
            out.append(Finding("no_adapter", BLOCK, provider, models))
            continue
        try:
            health = adapter.health()
        except Exception as exc:  # noqa: BLE001 - a health probe that throws is a service that is not usable
            out.append(Finding("service_down", BLOCK, provider, f"{models}: {exc}"))
            continue
        if health.state is HealthState.NOT_RUNNING:
            out.append(Finding("service_down", BLOCK, provider, models))
        elif health.state in (HealthState.MISSING_MODEL, HealthState.MISSING_TOOL, HealthState.DISABLED):
            out.append(Finding("not_installed", BLOCK, provider,
                               f"{models}: {', '.join(health.missing) or health.detail}"))
        elif health.state is HealthState.DEGRADED:
            out.append(Finding("degraded", WARN, provider, ", ".join(health.missing) or health.detail))
        else:
            state, missing = (tools or {}).get(provider, (HealthState.OK, ""))
            if state in (HealthState.MISSING_MODEL, HealthState.MISSING_TOOL):
                out.append(Finding("not_installed", BLOCK, provider, f"{models}: {missing}"))
            elif state is HealthState.DEGRADED:
                out.append(Finding("degraded", WARN, provider, missing))
    return out


def check_references(cfg: Settings, registry: AdapterRegistry, uses: Sequence[RefUse]
                     ) -> tuple[list[Finding], set[str]]:
    """Reference images against what each model states it takes (``media/refs.py``): more than it accepts and the job
    can only be refused (``too_many_refs``, stops the run); more than it reads and the rest is silently dropped
    (``refs_dropped``, told). Each finding names the models of the same kind that take that many (``values.models``;
    the ``_only`` code when there is none) and the people whose pictures are too many (``detail``). Also returns the
    specs that can only be refused, so the job check does not say it a second time."""
    groups: dict[tuple[str, str], list[RefUse]] = {}
    for use in uses:
        groups.setdefault((use.model_ref, use.kind), []).append(use)
    out: list[Finding] = []
    refused_ids: set[str] = set()
    for (ref, kind), group in sorted(groups.items()):
        rule = refs.rule_of(cfg, registry, ref, kind)
        if rule is None:
            continue
        refused = [u for u in group if rule.most is not None and u.count > rule.most]
        dropped = [u for u in group if rule.used is not None and u.count > rule.used and u not in refused]
        refused_ids |= {u.spec_id for u in refused}
        for code, level, hit, limit in (("too_many_refs", BLOCK, refused, rule.most),
                                        ("refs_dropped", WARN, dropped, rule.used)):
            if not hit:
                continue
            most = max(u.count for u in hit)
            roomy = refs.roomy_models(cfg, registry, kind, most, exclude=ref)
            people = list(dict.fromkeys(u.name for u in hit if u.name))
            out.append(Finding(
                code if roomy else f"{code}_only", level, ref, ", ".join(people[:NAMED]),
                {"limit": str(limit), "count": str(len(hit)), "most": str(most),
                 "models": ", ".join(m.text() for m in roomy[:4])}))
    return out, refused_ids


def check_jobs(
    settings: Settings, db: Database, ledger: Ledger, registry: AdapterRegistry,
    jobs: Sequence[tuple[SpecRecord, MediaJob]], *, project_settings: Mapping[str, Any], project_rating: str,
    skip_providers: Iterable[str] = (),
) -> list[Finding]:
    """The jobs a run is about to queue, shown to the worker's own gate (``media/gate.py``) without queueing them:
    whatever the worker would refuse (a size the tool does not draw, a parameter out of range, a reference that is
    gone, a licence not acknowledged) is a ``refused`` finding with the reason the worker would give. One job is
    asked for each shape (model, what it is for, how many references): jobs of one shape are refused alike.
    ``skip_providers``: services already known to be down, whose answer would only repeat that."""
    resolver = Resolver(settings, ledger)
    scratch = Path(settings.home) / "tmp" / "preflight"       # never created: a check only reads
    down = set(skip_providers)
    shapes: dict[tuple[str, str, str, int], list[Any]] = {}
    for spec, job in jobs:
        try:
            rec = ledger.draft(job, project_settings)
        except ConfigError:
            continue                                            # no model for the kind: check_media says so
        if rec.provider in down or registry.get(rec.provider) is None:
            continue
        shape = (rec.model_ref, rec.kind.value, spec.subject_type, len(rec.inputs.get("refs") or []))
        shapes.setdefault(shape, []).append(rec)
    out: list[Finding] = []
    for (model_ref, kind, subject, _), recs in sorted(shapes.items()):
        adapter = registry.get(recs[0].provider)
        if adapter is None:
            continue
        level = WARN if kind in SPARE_KINDS else BLOCK
        try:
            issues = gate.check(settings, db, ledger, adapter, resolver, recs[0], scratch,
                                project_settings=project_settings, project_rating=project_rating).issues
        except Exception as exc:  # noqa: BLE001 - a gate that throws would fail the job the same way
            out.append(Finding("refused", level, model_ref, str(exc),
                               {"count": str(len(recs)), "what": subject, "error": "exception"}))
            continue
        if issues:
            first = issues[0]
            out.append(Finding("refused", level, model_ref, first.message,
                               {"count": str(len(recs)), "what": subject, "error": first.code.value}))
    return out


WORKER_QUIET = 60.0         # seconds without a sign after which no worker is taken to be running


def check_worker(age: float | None) -> list[Finding]:
    """``age`` = seconds since a worker last said it is scheduling (``media.worker.worker_age``; ``None`` = never).
    A run queued without a worker is not lost, it waits: told, not refused."""
    if age is not None and age <= WORKER_QUIET:
        return []
    return [Finding("no_worker", WARN, "", "" if age is None else str(int(age)))]


def check_voices(characters: Sequence[dict[str, Any]], default_voice: str, library_voices: int,
                 speakers: Iterable[str] | None = None, *, automatic: bool = False) -> list[Finding]:
    """People who speak but have no voice: with library voices to give them (a full run does) it is only told; with
    none and no default voice they all speak with the speech model's one automatic voice (``automatic``), or, when
    the model has none, their lines will be subtitles without sound."""
    names = set(speakers) if speakers is not None else None
    silent = [str(c["name"]) for c in characters if not str(c.get("voice_ref_audio") or "").strip()
              and (names is None or c["name"] in names)]
    if not silent or default_voice:
        return []
    if library_voices:
        return [Finding("voices_auto", WARN, ", ".join(silent), str(library_voices))]
    return [Finding("voice_shared" if automatic else "no_voice", WARN, ", ".join(silent))]


def speaks_unaided(cfg: Settings) -> bool:
    """Whether the project's speech model reads a line without a reference voice (``media.tts.automatic_voice``)."""
    try:
        return automatic_voice(cfg, "", {})
    except ConfigError:
        return False


def run_stages(plan: Sequence[tuple[int, str]]) -> list[str]:
    """The stages of a run plan in pipeline order."""
    names = {stage for _, stage in plan}
    return [s for s in STAGES if s in names]


def look_ahead(
    settings: Settings, cfg: Settings, router: LLMRouter, registry: AdapterRegistry, *, project_id: int,
    project_settings: dict[str, Any], stages: Sequence[str], specs: Sequence[SpecRecord] | None,
    characters: Sequence[dict[str, Any]], default_voice: str, library_voices: int,
    gates: Sequence[dict[str, str]] = (), tools: Mapping[str, tuple[HealthState, str]] | None = None,
) -> list[Finding]:
    """Everything a run of ``stages`` needs, checked once: the language models of its story stages, the media
    adapters of its ``produce``/``compose`` (``specs`` = what is left to make; ``None`` = not known yet), the voices,
    and the content gates that would stop every job (``gates`` from ``content.plan_gates``). ``settings`` is the
    installation's, ``cfg`` the project's own (its pipeline plan)."""
    out: list[Finding] = []
    story = [s for s in stages if s not in MEDIA_STAGES]
    if story:
        out += check_llm(settings, router, project_id, story)
    if "produce" in stages:
        out += [Finding("gate", BLOCK, g.get("reason", ""), " ".join(x for x in (g.get("name"), g.get("detail")) if x))
                for g in gates]
        out += check_media(cfg, registry, specs, project_settings, compose="compose" in stages, tools=tools)
        out += check_voices(characters, default_voice, library_voices, automatic=speaks_unaided(cfg))
    elif "compose" in stages:
        out += check_media(cfg, registry, [], project_settings, compose=True, tools=tools)
    return out
