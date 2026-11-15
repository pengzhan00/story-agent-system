"""Reference-free speech capability shared by production planning and dispatch."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from storyagent.config import Settings


def automatic_voice(settings: Settings, model_ref: str, params: Mapping[str, Any]) -> bool:
    model = settings.model(model_ref or settings.default_model("audio.tts").ref)
    if model.provider == "cosyvoice":
        return bool({**model.params, **params}.get("auto_reference", True))
    return model.provider == "say" or bool(model.extra.get("reference_free_tts", False))
