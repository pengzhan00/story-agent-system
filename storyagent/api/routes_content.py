"""Content ratings: the status of the vault and the unlock session, and the unlock / lock actions.

Unlocking is the explicit act that lets restricted material be shown (files, prompts, thumbnails) for
``unlock_ttl_sec``; it needs the confirmation that the viewer is an adult and, with accounts, the user's password.
Every unlock, lock and look at a restricted file is written to the audit trail."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from storyagent.auth import AuthStore, User, verify_password
from storyagent.content import ContentError, UnlockStore, vault_ready

from .context import ctx_of
from .errors import ApiError
from .schemas import ContentStatusOut, RatingOut, UnlockBody

router = APIRouter(prefix="/api/content", tags=["content"])


def _user(request: Request) -> User | None:
    user: User | None = request.scope.get("state", {}).get("user")
    return user


def _status(request: Request) -> ContentStatusOut:
    ctx = ctx_of(request)
    cfg = ctx.settings.content
    user = _user(request)
    active = UnlockStore(ctx.db, ctx.settings).active(user.id if user else None)
    return ContentStatusOut(
        unlocked=active is not None, expires_at=active["expires_at"] if active else None, ttl_sec=cfg.unlock_ttl_sec,
        needs_password=ctx.settings.auth.enabled, vault_ready=vault_ready(ctx.settings), encryption=cfg.encryption,
        default_rating=cfg.default_rating,
        ratings=[RatingOut(name=p.name, label_key=p.label_key, restricted=p.restricted, mask_ui=p.mask_ui,
                           vault=p.vault, allow_export=p.allow_export)
                 for p in sorted(cfg.ratings.values(), key=lambda p: p.rank)])


@router.get("/status", response_model=ContentStatusOut)
def status(request: Request) -> ContentStatusOut:
    return _status(request)


@router.post("/unlock", response_model=ContentStatusOut)
def unlock(body: UnlockBody, request: Request) -> ContentStatusOut:
    ctx = ctx_of(request)
    user = _user(request)
    auth: AuthStore = ctx.auth
    if not body.confirm_adult:
        raise ContentError("confirm_adult")
    if ctx.settings.auth.enabled:
        row: Any = ctx.db.conn.execute("SELECT password_hash FROM users WHERE id=?",
                                       (user.id if user else 0,)).fetchone()
        if row is None or not verify_password(body.password, row["password_hash"]):
            auth.audit("content.unlock_failed", user=user, actor="local", path=request.url.path, status=403)
            raise ApiError(403, "content_unlock_denied")
    UnlockStore(ctx.db, ctx.settings).start(user.id if user else None, user.username if user else "local")
    auth.audit("content.unlock", user=user, actor="local", path=request.url.path, status=200,
               detail={"ttl_sec": ctx.settings.content.unlock_ttl_sec})
    return _status(request)


@router.delete("/unlock", response_model=ContentStatusOut)
def lock(request: Request) -> ContentStatusOut:
    ctx = ctx_of(request)
    user = _user(request)
    if UnlockStore(ctx.db, ctx.settings).stop(user.id if user else None):
        ctx.auth.audit("content.lock", user=user, actor="local", path=request.url.path, status=200)
    return _status(request)
