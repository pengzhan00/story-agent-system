"""What the current request may see of restricted material.

``AuthMiddleware`` puts a :class:`View` in a context variable for every API request; the response models of assets,
jobs and specs ask it (``hidden``) and blank their files, paths, prompts and provenance while the rating says
``mask_ui`` and the caller has no active unlock session, or while the caller's approved age is below the rating's
``min_age`` (403 ``age_restricted`` on the file routes). Without a view (code that builds models outside a request)
nothing is hidden. The file routes call :func:`require_viewable`, which refuses (403 ``content_locked``) and audits
every look at restricted material.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from storyagent.auth import ADULT_OPERATOR
from storyagent.config import Settings
from storyagent.content import ContentError, UnlockStore, effective_rating, project_rating
from storyagent.db import Database

from .errors import ApiError


@dataclass
class View:
    settings: Settings
    db: Database
    user_id: int | None
    age: int = ADULT_OPERATOR           # approved age of the caller (auth off: the operator is an adult)
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

    def too_young(self, rating: str | None, project_id: int | None) -> bool:
        """The caller's approved age is below what the rating asks (no approved age counts as 0)."""
        cfg = self.settings.content
        return cfg.policy(cfg.higher(rating, self.rating_of(project_id))).min_age > self.age

    def hides(self, rating: str | None, project_id: int | None) -> bool:
        cfg = self.settings.content
        effective = cfg.higher(rating, self.rating_of(project_id))
        return self.too_young(rating, project_id) or (cfg.policy(effective).mask_ui and not self.unlocked)


_view: ContextVar[View | None] = ContextVar("storyagent_view", default=None)


def set_view(view: View | None) -> None:
    _view.set(view)


def current() -> View | None:
    return _view.get()


def hidden(rating: str | None, project_id: int | None) -> bool:
    view = _view.get()
    return view is not None and view.hides(rating, project_id)


def too_young(rating: str | None, project_id: int | None) -> bool:
    view = _view.get()
    return view is not None and view.too_young(rating, project_id)


def viewer_age() -> int | None:
    """The caller's approved age, ``None`` outside a request."""
    view = _view.get()
    return view.age if view is not None else None


def require_viewable(request: Request, rating: str | None, project_id: int | None, *, what: str = "") -> None:
    """403 ``content_locked`` while the material is masked for this caller; an audit record when a restricted
    file is let through."""
    view = _view.get()
    if view is None:
        return
    if view.too_young(rating, project_id):
        raise ApiError(403, "age_restricted", what)
    if view.hides(rating, project_id):
        raise ApiError(403, "content_locked", what)
    cfg = view.settings.content
    effective = cfg.policy(cfg.higher(rating, view.rating_of(project_id)))
    if effective.mask_ui or effective.min_age > 0:
        user: Any = request.scope.get("state", {}).get("user")
        ctx: Any = request.app.state.ctx
        ctx.auth.audit("content.view", user=user, actor="local", path=request.url.path, project_id=project_id,
                       detail={"object": what, "rating": effective.name})


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
