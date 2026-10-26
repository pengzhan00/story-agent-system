from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from storyagent.config import Settings

from .contracts import Adapter


class AdapterRegistry:
    """provider name -> adapter instance. Real adapters register themselves in ``default_registry``."""

    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {}
        self.plugin_reports: list[Any] = []       # what the operator's plugins did (connect/plugins.py)

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


def _drawthings(settings: Settings) -> Adapter:
    from .adapters._http import ServiceClient
    from .adapters.drawthings import DrawThingsAdapter

    api_models = {
        ref: str(m.extra.get("api_model", "")) for ref, m in settings.models.items() if m.provider == "drawthings"
    }
    return DrawThingsAdapter(ServiceClient(settings.service("drawthings")), api_models)


def _comfy(settings: Settings) -> Adapter:
    from .adapters._http import ServiceClient
    from .adapters.comfy import ComfyAdapter

    workflows = {ref: str(m.extra.get("workflow", "")) for ref, m in settings.models.items() if m.provider == "comfy"}
    return ComfyAdapter(ServiceClient(settings.service("comfy")), workflows, user_dir=settings.config_dir / "workflows")


def _phosphene(settings: Settings) -> Adapter:
    from .adapters._http import ServiceClient
    from .adapters.phosphene import PhospheneAdapter

    return PhospheneAdapter(ServiceClient(settings.service("phosphene")))


def _cosyvoice(settings: Settings) -> Adapter:
    from .adapters.cosyvoice import CosyVoiceAdapter

    return CosyVoiceAdapter()


def _say(settings: Settings) -> Adapter:
    from .adapters.say import SayAdapter

    return SayAdapter()


def _demucs(settings: Settings) -> Adapter:
    from .adapters.demucs import DemucsAdapter

    return DemucsAdapter()


def _llm(settings: Settings) -> Adapter:
    from .adapters.llm import LlmAdapter

    return LlmAdapter()


def _remote_video(settings: Settings) -> Adapter:
    from .adapters.remote_video import RemoteVideoAdapter

    return RemoteVideoAdapter(settings)


def _whisper(settings: Settings) -> Adapter:
    from .adapters.whisper import WhisperAdapter

    return WhisperAdapter()


def _mflux(settings: Settings) -> Adapter:
    from .adapters.mflux import MfluxAdapter

    return MfluxAdapter()


def _seedvc(settings: Settings) -> Adapter:
    from .adapters.seedvc import SeedVcAdapter

    return SeedVcAdapter()


def _lora_train(settings: Settings) -> Adapter:
    from .adapters.lora_train import LoraTrainAdapter

    return LoraTrainAdapter()


def _ffmpeg(settings: Settings) -> Adapter:
    from .adapters.assemble import AssembleAdapter

    return AssembleAdapter()


def _delivery(settings: Settings) -> Adapter:
    from .adapters.delivery import DeliveryAdapter

    return DeliveryAdapter()


def _h3(settings: Settings) -> Adapter:
    from .adapters.h3 import H3Adapter

    return H3Adapter()


def _longcat(settings: Settings) -> Adapter:
    from .adapters.longcat import LongCatAdapter

    return LongCatAdapter()


def _cli(settings: Settings) -> Adapter:
    from .adapters.generic_cli import CliAdapter

    return CliAdapter(settings)


def _http(settings: Settings) -> Adapter:
    from .adapters.generic_http import HttpAdapter

    return HttpAdapter(settings)


_BUILTIN: list[Callable[[Settings], Adapter]] = [
    _acestep, _comfy, _drawthings, _phosphene, _cosyvoice, _say, _whisper, _llm, _remote_video, _mflux, _lora_train,
    _seedvc, _demucs, _ffmpeg, _delivery, _h3, _longcat, _cli, _http,
]


def default_registry(settings: Settings) -> AdapterRegistry:
    reg = AdapterRegistry()
    for build in _BUILTIN:
        reg.register(build(settings))
    from storyagent.connect.plugins import load_plugins

    reg.plugin_reports = load_plugins(reg, settings)
    if os.environ.get("STORY_AGENT_ENABLE_FAKE") == "1":  # dev/demo only
        from .adapters.fake import FakeAdapter

        reg.register(FakeAdapter())
    return reg
