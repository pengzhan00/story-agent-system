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
_BUILTIN: list[Callable[[Settings], Adapter]] = []


def default_registry(settings: Settings) -> AdapterRegistry:
    reg = AdapterRegistry()
    for build in _BUILTIN:
        reg.register(build(settings))
    if os.environ.get("STORY_AGENT_ENABLE_FAKE") == "1":  # dev/demo only
        from .adapters.fake import FakeAdapter

        reg.register(FakeAdapter())
    return reg
