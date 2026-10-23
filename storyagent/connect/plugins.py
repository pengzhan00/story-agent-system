"""Operator plugins: Python files in ``<config_dir>/plugins/*.py`` that add adapters of their own.

A plugin is a module with ``register(registry, settings)``; it calls ``registry.register(adapter)`` for each adapter
(an object that follows ``media.contracts.Adapter``, with its own ``provider`` name) and models in ``models.toml`` use
that ``provider``. It is the operator's own code, run by the worker and the server with their privileges - put only
files there that you wrote or read. A plugin that fails to load, has no ``register``, raises in it, or names a provider
that is already taken is skipped and reported (the health check lists it); it never stops the system from starting.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from storyagent.config import Settings

if TYPE_CHECKING:
    from storyagent.media.contracts import Adapter
    from storyagent.media.registry import AdapterRegistry

log = logging.getLogger(__name__)
PLUGIN_DIR = "plugins"


@dataclass(frozen=True)
class PluginReport:
    name: str                  # file stem
    path: str
    providers: tuple[str, ...]
    error: str = ""            # empty = loaded


class _Guarded:
    """What a plugin sees as ``registry``: it can add adapters, never replace one that is already there."""

    def __init__(self, registry: AdapterRegistry) -> None:
        self._registry = registry
        self.added: list[str] = []

    def register(self, adapter: Adapter) -> None:
        provider = str(getattr(adapter, "provider", "") or "")
        if not provider:
            raise ValueError("the adapter has no provider name")
        if provider in self._registry:
            raise ValueError(f"the provider {provider!r} is already taken")
        self._registry.register(adapter)
        self.added.append(provider)

    def providers(self) -> list[str]:
        return self._registry.providers()

    def __contains__(self, provider: str) -> bool:
        return provider in self._registry


def plugin_dir(settings: Settings) -> Path:
    return settings.config_dir / PLUGIN_DIR


def load_plugins(registry: AdapterRegistry, settings: Settings) -> list[PluginReport]:
    folder = plugin_dir(settings)
    reports: list[PluginReport] = []
    if not folder.is_dir():
        return reports
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        guard = _Guarded(registry)
        error = ""
        try:
            module = _import(path)
            register: Any = getattr(module, "register", None)
            if not callable(register):
                raise AttributeError("the file has no register(registry, settings) function")
            register(guard, settings)
        except (Exception, SystemExit) as exc:   # the operator's own code must not stop the system starting
            error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("plugin %s skipped: %s", path.name, error)
        reports.append(PluginReport(path.stem, str(path), tuple(guard.added), error))
    return reports


def _import(path: Path) -> Any:
    name = f"storyagent_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except (Exception, SystemExit):
        sys.modules.pop(name, None)
        raise
    return module
