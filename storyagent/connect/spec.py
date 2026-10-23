"""The ``[models.X.connector]`` table of a ``cli`` / ``http`` model: reading it and the kinds these providers serve."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from storyagent.config import ConfigError, Settings
from storyagent.domain import MediaKind

# everything a tool can make or change for an episode; the language-model, assembly and training kinds have their own
GENERIC_KINDS = tuple(
    k for k in MediaKind if not k.value.startswith(("text.", "compose.", "train."))
)


def connector_of(settings: Settings, model_ref: str, provider: str) -> Mapping[str, Any]:
    """The connector table of a model, or ``ConfigError`` saying what is wrong with it."""
    model = settings.model(model_ref)
    if model.provider != provider:
        raise ConfigError(f"model {model_ref!r} runs on {model.provider!r}, not {provider!r}")
    spec = model.extra.get("connector")
    if not isinstance(spec, Mapping) or not spec:
        raise ConfigError(f"model {model_ref!r} has no [models.\"{model_ref}\".connector] table")
    return spec


def connector_models(settings: Settings, provider: str) -> list[str]:
    return sorted(ref for ref, m in settings.models.items() if m.provider == provider)
