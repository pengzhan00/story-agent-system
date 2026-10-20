"""Which LLMs are resident in memory, and how to unload them (before a heavy media job).

``ResidentLLMProbe`` is backend-agnostic. R1 ships the Ollama probe; the llama.cpp probe is added
with the LLM layer once its router-mode endpoints have been checked against the installed server.
"""
from __future__ import annotations

import logging
from typing import Protocol

import requests

from storyagent.config import Settings

log = logging.getLogger(__name__)


class ResidentLLMProbe(Protocol):
    name: str

    def resident(self) -> list[str]: ...
    def unload(self, models: list[str]) -> list[str]:
        """Unload the given models; return the ones that were unloaded."""
        ...


class OllamaProbe:
    name = "ollama"

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def resident(self) -> list[str]:
        try:
            r = requests.get(f"{self.base_url}/api/ps", timeout=self.timeout)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except (requests.RequestException, ValueError, KeyError):
            log.warning("ollama resident-model probe failed; assuming none")
            return []

    def unload(self, models: list[str]) -> list[str]:
        done: list[str] = []
        for m in models:
            try:
                # keep_alive=0 makes Ollama evict the model immediately.
                requests.post(
                    f"{self.base_url}/api/generate", json={"model": m, "keep_alive": 0}, timeout=self.timeout * 4
                ).raise_for_status()
                done.append(m)
            except requests.RequestException:
                log.warning("could not unload %s", m)
        return done


def build_probes(settings: Settings) -> list[ResidentLLMProbe]:
    probes: list[ResidentLLMProbe] = []
    for backend in settings.llm_backends.values():
        service = settings.services.get(backend.service) if backend.service else None
        if service is not None and not service.enabled:
            continue
        if backend.kind == "ollama":
            probes.append(OllamaProbe(backend.url))
    return probes
