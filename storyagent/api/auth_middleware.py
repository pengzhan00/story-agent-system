"""Sign-in check and audit trail for every request (a pure ASGI middleware, so streaming responses are untouched).

With ``[auth] mode = "off"`` nothing is checked and the operator is the local machine; with ``"token"`` the caller must
bring a session token (``Authorization: Bearer`` or the session cookie) and ``storyagent.auth.decide`` rules on the
request. Every write (anything but GET/HEAD/OPTIONS) is audited with the user, path, project and the HTTP status;
request bodies are never recorded. The login routes audit themselves.
"""
from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Any

import anyio
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from storyagent.auth import SAFE_METHODS, User, decide, project_of

from .errors import respond
from .visibility import View, set_view


def _token(scope: Scope, cookie_name: str) -> str:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if "cookie" in headers:
        jar: SimpleCookie = SimpleCookie()
        jar.load(headers["cookie"])
        if cookie_name in jar:
            return jar[cookie_name].value
    return ""


def _client(scope: Scope) -> str:
    c = scope.get("client")
    return str(c[0]) if c else ""


class AuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        ctx: Any = scope["app"].state.ctx
        cfg, store = ctx.settings.auth, ctx.auth
        method, path = scope["method"], scope["path"]

        user: User | None = None
        if cfg.enabled:
            token = _token(scope, cfg.cookie_name)
            if token:
                user = await anyio.to_thread.run_sync(store.authenticate, token)
        verdict = decide(cfg, user, method, path)
        if verdict is not None:
            if verdict == "forbidden":
                await anyio.to_thread.run_sync(lambda: store.audit(
                    "auth.denied", user=user, path=path, project_id=project_of(path), status=403,
                    detail={"method": method}, client=_client(scope)))
            status = 401 if verdict == "unauthorized" else 403
            await respond(Request(scope, receive), status, verdict)(scope, receive, send)
            return
        scope.setdefault("state", {})["user"] = user
        view = View(ctx.settings, ctx.db, user.id if user else None, age=store.age_of(user))
        set_view(view)
        project_id = project_of(path)
        if project_id is not None and await anyio.to_thread.run_sync(view.too_young, None, project_id):
            await anyio.to_thread.run_sync(lambda: store.audit(
                "content.age_denied", user=user, path=path, project_id=project_id, status=403,
                detail={"method": method, "rating": view.rating_of(project_id), "age": view.age},
                client=_client(scope)))
            await respond(Request(scope, receive), 403, "age_restricted")(scope, receive, send)
            return

        status_seen = 0

        async def capture(message: Message) -> None:
            nonlocal status_seen
            if message["type"] == "http.response.start":
                status_seen = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            if method not in SAFE_METHODS and not path.startswith("/api/auth/"):
                actor = "" if user else "local"
                await anyio.to_thread.run_sync(lambda: store.audit(
                    f"http.{method}", user=user, actor=actor, path=path, project_id=project_of(path),
                    status=status_seen or None, client=_client(scope)))
