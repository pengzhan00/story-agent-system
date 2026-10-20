from __future__ import annotations

import os
from collections.abc import Callable

from storyagent.config import Settings

from .contracts import Adapter


class AdapterRegistry:
    """provider name -> adapter instance. Real adapters register themselves in ``default_registry``."""

    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {}

    def register(self, adapter: Adapter) -> None:
        self._adapters[adapter.provider] = adapter

    def get(self, provider: str) -> Adapter | None:
        return self._adapters.get(provider)

    def providers(self) -> list[str]:
        return sorted(self._adapters)

    def __contains__(self, provider: str) -> bool:
        return provider in self._adapters


# Real adapters are appended here as they are implemented (R2+): each entry builds one adapter.
def _acestep(settings: Settings) -> Adapter:
    from .adapters._http import ServiceClient
    from .adapters.acestep import AceStepAdapter

    api_models = {
        ref: str(m.extra.get("api_model", "")) for ref, m in settings.models.items() if m.provider == "acestep"
    }
    return AceStepAdapter(ServiceClient(settings.service("acestep")), api_models)


def _phosphene(settings: Settings) -> Adapter:
    from .adapters._http import ServiceClient
    from .adapters.phosphene import PhospheneAdapter

    return PhospheneAdapter(ServiceClient(settings.service("phosphene")))


def _cosyvoice(settings: Settings) -> Adapter:
    from .adapters.cosyvoice import CosyVoiceAdapter

    return CosyVoiceAdapter()


def _whisper(settings: Settings) -> Adapter:
    from .adapters.whisper import WhisperAdapter

    return WhisperAdapter()


def _mflux(settings: Settings) -> Adapter:
    from .adapters.mflux import MfluxAdapter

    return MfluxAdapter()


def _lora_train(settings: Settings) -> Adapter:
    from .adapters.lora_train import LoraTrainAdapter

    return LoraTrainAdapter()


_BUILTIN: list[Callable[[Settings], Adapter]] = [_acestep, _phosphene, _cosyvoice, _whisper, _mflux, _lora_train]


def default_registry(settings: Settings) -> AdapterRegistry:
    reg = AdapterRegistry()
    for build in _BUILTIN:
        reg.register(build(settings))
    if os.environ.get("STORY_AGENT_ENABLE_FAKE") == "1":  # dev/demo only
        from .adapters.fake import FakeAdapter

        reg.register(FakeAdapter())
    return reg
