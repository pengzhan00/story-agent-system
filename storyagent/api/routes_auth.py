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
    AgeClaimBody,
    AgeReviewBody,
    AuditOut,
    AuthModeOut,
    LoginBody,
    LoginOut,
    MeOut,
    PasswordBody,
    RegisterBody,
    SetupBody,
    UserCreateBody,
    UserOut,
    UserPatchBody,
)

router = APIRouter(tags=["auth"])


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name, role=u.role, active=u.active,
                   last_login_at=u.last_login_at, created_at=u.created_at, age=u.age, age_status=u.age_status,
                   age_claim=u.age_claim, birth_date=u.birth_date, age_note=u.age_note,
                   age_reviewed_by=u.age_reviewed_by, age_reviewed_at=u.age_reviewed_at)


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
    return AuthModeOut(mode="token" if cfg.enabled else "off", first_user=first, locales=available_locales(),
                       allow_registration=cfg.enabled and cfg.allow_registration)


def _open_session(request: Request, response: Response, token: str) -> None:
    cfg = ctx_of(request).settings.auth
    response.set_cookie(cfg.cookie_name, token, max_age=cfg.session_hours * 3600, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")


@router.post("/api/auth/register", response_model=LoginOut, status_code=201)
def register(body: RegisterBody, request: Request, response: Response) -> LoginOut:
    """Ask for an account: it is created with the registration role and the birth date as a claim, and the person is
    signed in at once. What the age unlocks waits for an admin's approval (Team page)."""
    cfg, store = ctx_of(request).settings.auth, _store(request)
    if not cfg.enabled:
        raise ApiError(409, "auth_off")
    try:
        created = store.register(body.username, body.password, body.birth_date, body.display_name)
    except Exception as exc:
        store.audit("auth.register_failed", actor=body.username[:80], path=request.url.path, status=422,
                    detail={"reason": getattr(exc, "code", "error")}, client=_client(request))
        raise
    store.audit("auth.register", user=created, path=request.url.path, status=201,
                detail={"role": created.role, "age_claim": created.age_claim}, client=_client(request))
    token, user, expires = store.login(body.username, body.password, label=body.label)
    _open_session(request, response, token)
    return LoginOut(token=token, expires_at=expires, user=_out(user))


@router.post("/api/auth/setup", response_model=LoginOut, status_code=201)
def setup(body: SetupBody, request: Request, response: Response) -> LoginOut:
    """First run with accounts on: the first person creates the administrator account here instead of on the command
    line. Works only while no account exists (afterwards ``409 setup_done``)."""
    cfg, store = ctx_of(request).settings.auth, _store(request)
    if not cfg.enabled:
        raise ApiError(409, "auth_off")
    created = store.setup_admin(body.username, body.password, body.birth_date, body.display_name)
    store.audit("auth.setup", user=created, path=request.url.path, status=201, client=_client(request))
    token, user, expires = store.login(body.username, body.password)
    _open_session(request, response, token)
    return LoginOut(token=token, expires_at=expires, user=_out(user))


@router.post("/api/auth/age", response_model=UserOut)
def claim_age(body: AgeClaimBody, request: Request) -> UserOut:
    """The signed-in person gives (again) their birth date; it waits for an admin."""
    user = _me(request)
    if user is None:
        raise ApiError(401, "unauthorized")
    store = _store(request)
    updated = store.claim_age(user.id, body.birth_date)
    store.audit("auth.age_claimed", user=user, path=request.url.path, status=200,
                detail={"age_claim": updated.age_claim}, client=_client(request))
    return _out(updated)


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
    _open_session(request, response, token)
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
    created = store.create_user(body.username, body.password, body.role, body.display_name, body.birth_date)
    store.audit("user.create", user=_me(request), actor="local", path=request.url.path, status=201,
                detail={"username": created.username, "role": created.role}, client=_client(request))
    return _out(created)


@router.post("/api/users/{user_id}/age", response_model=UserOut)
def review_age(user_id: int, body: AgeReviewBody, request: Request) -> UserOut:
    """An admin approves (optionally correcting the birth date) or rejects the age of an account. The age is worked
    out from the approved birth date every time, so it grows by itself."""
    store, admin = _store(request), _me(request)
    before = store.get_user(user_id)
    updated = store.review_age(user_id, approve=body.approve, reviewer=admin.username if admin else "local",
                               birth_date=body.birth_date, note=body.note)
    store.audit("user.age_approved" if body.approve else "user.age_rejected", user=admin, actor="local",
                path=request.url.path, status=200,
                detail={"username": updated.username, "claim": before.age_claim, "birth_date": updated.birth_date,
                        "age": updated.age, "note": updated.age_note}, client=_client(request))
    return _out(updated)


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
