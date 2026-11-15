"""Before a run that goes all the way to the film: is everything it will need there?

A full run writes the story for many minutes and then spends hours on pictures, voices and clips. Without this look
ahead a picture service that was never started is found out after the story is written, as one failed job per shot.
``preflight`` only reads: it asks each language-model backend and each media adapter the run will use whether it is
ready, and whether the people of the story have a voice. A ``block`` finding stops a full run before it starts (the
answer says what to start or install); a ``warn`` finding is shown and the run goes on.

Findings carry a code (i18n ``preflight.<code>``), the thing it is about and a detail; nothing here starts, stops or
installs anything.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from storyagent.agents.registry import load_specs
from storyagent.config import ConfigError, Settings
from storyagent.domain import MediaKind
from storyagent.llm import LLMError, LLMRouter
from storyagent.media.contracts import HealthState
from storyagent.media.registry import AdapterRegistry
from storyagent.media.specs import SpecRecord

from .state import STAGES

BLOCK, WARN = "block", "warn"
MEDIA_STAGES = ("produce", "compose")
# what a story needs made, when it has no specs yet to tell
USUAL_KINDS = (MediaKind.IMAGE_GENERATE, MediaKind.AUDIO_TTS, MediaKind.AUDIO_MUSIC, MediaKind.AUDIO_SFX,
               MediaKind.VIDEO_GENERATE)
OPTIONAL_KINDS = {MediaKind.AUDIO_SFX.value, MediaKind.AUDIO_MUSIC.value, MediaKind.IMAGE_EDIT.value}


@dataclass(frozen=True)
class Finding:
    code: str
    level: str
    subject: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


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


def check_media(cfg: Settings, registry: AdapterRegistry, specs: Sequence[SpecRecord] | None,
                project_settings: dict[str, Any], *, compose: bool = True) -> list[Finding]:
    """Every adapter the project's models run on says it is ready (its service answers, its tool and weights exist)."""
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
    return out


def check_voices(characters: Sequence[dict[str, Any]], default_voice: str, library_voices: int,
                 speakers: Iterable[str] | None = None) -> list[Finding]:
    """People who speak but have no voice: with library voices to give them (a full run does) it is only told; with
    none and no default voice their lines will be subtitles without sound."""
    names = set(speakers) if speakers is not None else None
    silent = [str(c["name"]) for c in characters if not str(c.get("voice_ref_audio") or "").strip()
              and (names is None or c["name"] in names)]
    if not silent or default_voice:
        return []
    if library_voices:
        return [Finding("voices_auto", WARN, ", ".join(silent), str(library_voices))]
    return [Finding("no_voice", WARN, ", ".join(silent))]


def run_stages(plan: Sequence[tuple[int, str]]) -> list[str]:
    """The stages of a run plan in pipeline order."""
    names = {stage for _, stage in plan}
    return [s for s in STAGES if s in names]


def look_ahead(
    settings: Settings, cfg: Settings, router: LLMRouter, registry: AdapterRegistry, *, project_id: int,
    project_settings: dict[str, Any], stages: Sequence[str], specs: Sequence[SpecRecord] | None,
    characters: Sequence[dict[str, Any]], default_voice: str, library_voices: int,
    gates: Sequence[dict[str, str]] = (),
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
        out += check_media(cfg, registry, specs, project_settings, compose="compose" in stages)
        out += check_voices(characters, default_voice, library_voices)
    elif "compose" in stages:
        out += check_media(cfg, registry, [], project_settings, compose=True)
    return out
