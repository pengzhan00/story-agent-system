"""Per-app services and per-request helpers (locale, translation)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from fastapi import Request

from storyagent.config import Settings
from storyagent.db import Database
from storyagent.i18n import available_locales
from storyagent.i18n.catalog import Catalog, get_catalog
from storyagent.media.ledger import Ledger
from storyagent.media.library import Library
from storyagent.media.specs import SpecStore
from storyagent.projects import ProjectStore
from storyagent.story import StoryStore
from storyagent.workflow import WorkflowState


@dataclass
class ApiContext:
    settings: Settings
    db: Database
    ledger: Ledger
    specs: SpecStore
    projects: ProjectStore
    library: Library
    story: StoryStore
    workflow: WorkflowState


def ctx_of(request: Request) -> ApiContext:
    ctx: ApiContext = request.app.state.ctx
    return ctx


@lru_cache(maxsize=8)
def _catalog(locale: str) -> Catalog:
    return get_catalog(locale)


def resolve_locale(request: Request, default: str) -> str:
    """``?lang=`` wins, then ``Accept-Language`` (``zh`` -> ``zh-CN``), then the configured locale."""
    supported = available_locales()
    wanted: list[str] = []
    if request.query_params.get("lang"):
        wanted.append(request.query_params["lang"])
    for part in request.headers.get("accept-language", "").split(","):
        tag = part.split(";")[0].strip()
        if tag:
            wanted.append(tag)
    for tag in wanted:
        if tag in supported:
            return tag
        base = tag.split("-")[0].lower()
        for s in supported:
            if s.split("-")[0].lower() == base:
                return s
    return default if default in supported else supported[0]


class Translator:
    """Request-scoped ``t()``; never touches the process-wide locale, so concurrent requests cannot interfere."""

    def __init__(self, locale: str) -> None:
        self.locale = locale
        self._cat = _catalog(locale)

    def __call__(self, key: str, **params: object) -> str:
        return self._cat.t(key, **params)


def translator_of(request: Request) -> Translator:
    return Translator(resolve_locale(request, ctx_of(request).settings.locale))
