"""What the current request may see of restricted material.

``AuthMiddleware`` puts a :class:`View` in a context variable for every API request; the response models of assets,
jobs and specs ask it (``hidden``) and blank their files, paths, prompts and provenance while the rating says
``mask_ui`` and the caller has no active unlock session. Without a view (code that builds models outside a request)
nothing is hidden. The file routes call :func:`require_viewable`, which refuses (403 ``content_locked``) and audits
every look at restricted material.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from storyagent.config import Settings
from storyagent.content import ContentError, UnlockStore, effective_rating, project_rating
from storyagent.db import Database

from .errors import ApiError


@dataclass
class View:
    settings: Settings
    db: Database
    user_id: int | None
    _unlocked: bool | None = None
    _projects: dict[int, str] = field(default_factory=dict)

    @property
    def unlocked(self) -> bool:
        if self._unlocked is None:
            self._unlocked = UnlockStore(self.db, self.settings).active(self.user_id) is not None
        return self._unlocked

    def rating_of(self, project_id: int | None) -> str:
        if project_id is None:
            return self.settings.content.default_rating
        if project_id not in self._projects:
            self._projects[project_id] = project_rating(self.db, project_id) or self.settings.content.default_rating
        return self._projects[project_id]

    def hides(self, rating: str | None, project_id: int | None) -> bool:
        cfg = self.settings.content
        effective = cfg.higher(rating, self.rating_of(project_id))
        return cfg.policy(effective).mask_ui and not self.unlocked


_view: ContextVar[View | None] = ContextVar("storyagent_view", default=None)


def set_view(view: View | None) -> None:
    _view.set(view)


def current() -> View | None:
    return _view.get()


def hidden(rating: str | None, project_id: int | None) -> bool:
    view = _view.get()
    return view is not None and view.hides(rating, project_id)


def require_viewable(request: Request, rating: str | None, project_id: int | None, *, what: str = "") -> None:
    """403 ``content_locked`` while the material is masked for this caller; an audit record when a restricted
    file is let through."""
    view = _view.get()
    if view is None:
        return
    if view.hides(rating, project_id):
        raise ApiError(403, "content_locked", what)
    cfg = view.settings.content
    if cfg.policy(cfg.higher(rating, view.rating_of(project_id))).mask_ui:
        user: Any = request.scope.get("state", {}).get("user")
        ctx: Any = request.app.state.ctx
        ctx.auth.audit("content.view", user=user, actor="local", path=request.url.path, project_id=project_id,
                       detail={"object": what})


def allow_export(request: Request, project_id: int, confirmed: bool, *, what: str) -> bool:
    """Gate before anything is packaged. Returns ``confirmed`` to pass on to the builder. A project whose rating does
    not allow export needs the explicit confirmation and an active unlock session; the export is audited."""
    ctx: Any = request.app.state.ctx
    rating = effective_rating(ctx.settings, ctx.db, project_id)
    if ctx.settings.content.policy(rating).allow_export:
        return False
    if not confirmed:
        raise ContentError("export_blocked", rating=rating)
    view = _view.get()
    if view is not None and not view.unlocked:
        raise ApiError(403, "content_locked", what)
    user: Any = request.scope.get("state", {}).get("user")
    ctx.auth.audit("content.export", user=user, actor="local", path=request.url.path, project_id=project_id,
                   detail={"what": what, "rating": rating})
    return True
