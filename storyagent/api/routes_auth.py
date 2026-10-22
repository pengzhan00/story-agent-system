"""Sign-in, accounts and the audit trail. Who may call what is decided by ``auth_middleware`` from auth.toml: login,
mode and logout are public; ``/api/users`` and ``/api/audit`` need the admin flag."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from storyagent.auth import AuthStore, User
from storyagent.i18n import available_locales

from .context import ctx_of
from .errors import ApiError
from .schemas import (
    AuditOut,
    AuthModeOut,
    LoginBody,
    LoginOut,
    MeOut,
    PasswordBody,
    UserCreateBody,
    UserOut,
    UserPatchBody,
)

router = APIRouter(tags=["auth"])


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name, role=u.role, active=u.active,
                   last_login_at=u.last_login_at, created_at=u.created_at)


def _store(request: Request) -> AuthStore:
    store: AuthStore = ctx_of(request).auth
    return store


def _me(request: Request) -> User | None:
    user: User | None = request.scope.get("state", {}).get("user")
    return user


def _client(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/api/auth/mode", response_model=AuthModeOut)
def mode(request: Request) -> AuthModeOut:
    cfg = ctx_of(request).settings.auth
    first = cfg.enabled and _store(request).count_users() == 0
    return AuthModeOut(mode="token" if cfg.enabled else "off", first_user=first, locales=available_locales())


@router.post("/api/auth/login", response_model=LoginOut)
def login(body: LoginBody, request: Request, response: Response) -> LoginOut:
    ctx = ctx_of(request)
    cfg, store = ctx.settings.auth, _store(request)
    if not cfg.enabled:
        raise ApiError(409, "auth_off")
    try:
        token, user, expires = store.login(body.username, body.password, label=body.label)
    except Exception as exc:
        store.audit("auth.login_failed", actor=body.username[:80], path=request.url.path, status=401,
                    detail={"reason": getattr(exc, "code", "error")}, client=_client(request))
        raise
    store.audit("auth.login", user=user, path=request.url.path, status=200, client=_client(request))
    response.set_cookie(cfg.cookie_name, token, max_age=cfg.session_hours * 3600, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")
    return LoginOut(token=token, expires_at=expires, user=_out(user))


@router.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    cfg, store = ctx_of(request).settings.auth, _store(request)
    token = request.cookies.get(cfg.cookie_name, "")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    user = _me(request)
    if token:
        store.logout(token)
    if user:
        store.audit("auth.logout", user=user, path=request.url.path, status=204, client=_client(request))
    response.delete_cookie(cfg.cookie_name, path="/")


@router.get("/api/auth/me", response_model=MeOut)
def me(request: Request) -> MeOut:
    cfg = ctx_of(request).settings.auth
    user = _me(request)
    if not cfg.enabled:
        return MeOut(mode="off", user=None, can_write=True, can_create_project=True, is_admin=True)
    role = cfg.roles.get(user.role) if user else None
    return MeOut(mode="token", user=_out(user) if user else None, can_write=bool(role and role.write),
                 can_create_project=bool(role and role.create_project), is_admin=bool(role and role.admin))


@router.post("/api/auth/password", status_code=204)
def change_password(body: PasswordBody, request: Request) -> None:
    user = _me(request)
    if user is None:
        raise ApiError(401, "unauthorized")
    store = _store(request)
    store.change_password(user.id, body.old_password, body.new_password)
    store.audit("auth.password_changed", user=user, path=request.url.path, status=204, client=_client(request))


@router.get("/api/users", response_model=list[UserOut])
def list_users(request: Request) -> list[UserOut]:
    return [_out(u) for u in _store(request).list_users()]


@router.post("/api/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreateBody, request: Request) -> UserOut:
    store = _store(request)
    created = store.create_user(body.username, body.password, body.role, body.display_name)
    store.audit("user.create", user=_me(request), actor="local", path=request.url.path, status=201,
                detail={"username": created.username, "role": created.role}, client=_client(request))
    return _out(created)


@router.patch("/api/users/{user_id}", response_model=UserOut)
def patch_user(user_id: int, body: UserPatchBody, request: Request) -> UserOut:
    store = _store(request)
    updated = store.update_user(user_id, role=body.role, active=body.active, display_name=body.display_name,
                                password=body.password)
    changed = sorted(k for k, v in body.model_dump().items() if v is not None and k != "password")
    store.audit("user.update", user=_me(request), actor="local", path=request.url.path, status=200,
                detail={"username": updated.username, "changed": changed + (["password"] if body.password else [])},
                client=_client(request))
    return _out(updated)


@router.get("/api/audit", response_model=list[AuditOut])
def audit(
    request: Request, user: str = "", action: str = "", project_id: int | None = None, since: str = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditOut]:
    rows = _store(request).list_audit(user=user, action=action, project_id=project_id, since=since, limit=limit)
    return [AuditOut(**r) for r in rows]
