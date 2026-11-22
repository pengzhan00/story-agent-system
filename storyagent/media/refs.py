"""How many reference images a model takes: one statement per model, read by everything that matches a job to a model.

Who says it, most specific first:

1. the model entry in models.toml: ``max_refs`` (more than this is refused) and ``used_refs`` (only this many are
   read). This is where a ComfyUI workflow or a ``cli`` / ``http`` connector says it, because only the person who
   wired it knows;
2. model-specific adapter metadata: ComfyUI derives capacity from its template bindings/reference chain;
3. the adapter (``Adapter.reference_rule(kind)``): Draw Things takes one, H3-Image sixteen, mflux reads the first
   when it generates and all of them when it edits;
4. nobody: the rule is unknown. An unknown model is never reported as too small and never offered as one that takes
   several.

Who reads it: the look ahead of a run (``workflow/preflight.py``: a job that can only be refused is found before
anything is queued, with the models that would take it), and through it the readiness check of the web client.
The adapter's own ``validate`` stays the last word on a single job (``media/gate.py``).
"""
from __future__ import annotations

from dataclasses import dataclass

from storyagent.config import ConfigError, Settings
from storyagent.domain import MediaKind

from .contracts import ReferenceRule
from .registry import AdapterRegistry

IMAGE_KINDS = (MediaKind.IMAGE_GENERATE.value, MediaKind.IMAGE_EDIT.value)


def _count(value: object) -> int | None:
    try:
        return int(str(value)) if value not in (None, "") else None
    except ValueError:
        return None


def rule_of(settings: Settings, registry: AdapterRegistry, model_ref: str, kind: str) -> ReferenceRule | None:
    """The rule of one model for one kind of job (``None`` = unknown)."""
    try:
        model = settings.model(model_ref)
    except ConfigError:
        return None
    most, used = _count(model.extra.get("max_refs")), _count(model.extra.get("used_refs"))
    if most is not None or used is not None:
        return ReferenceRule(most=most, used=used)
    adapter = registry.get(model.provider)
    model_ask = getattr(adapter, "model_reference_rule", None)
    ask = getattr(adapter, "reference_rule", None)
    try:
        media_kind = MediaKind(kind)
        if callable(model_ask):
            found = model_ask(model_ref, media_kind)
            if isinstance(found, ReferenceRule):
                return found
        found = ask(media_kind) if callable(ask) else None
    except ValueError:
        return None
    return found if isinstance(found, ReferenceRule) else None


@dataclass(frozen=True)
class Roomy:
    """A model that takes several reference images: ``room`` = how many (``None`` = any number)."""

    ref: str
    room: int | None

    def text(self) -> str:
        return f"{self.ref} ({self.room})" if self.room is not None else self.ref


def roomy_models(settings: Settings, registry: AdapterRegistry, kind: str, needed: int,
                 exclude: str = "") -> list[Roomy]:
    """The models of ``kind`` on this installation that state they read at least ``needed`` reference images and whose
    adapter is there, the ones that state a number first (smallest that fits), then the ones that read any number."""
    out: list[Roomy] = []
    for ref, model in sorted(settings.models.items()):
        if model.kind != kind or ref == exclude or registry.get(model.provider) is None:
            continue
        rule = rule_of(settings, registry, ref, kind)
        if rule is None or (rule.room is not None and rule.room < needed):
            continue
        out.append(Roomy(ref, rule.room))
    return sorted(out, key=lambda m: (m.room is None, m.room or 0, m.ref))
