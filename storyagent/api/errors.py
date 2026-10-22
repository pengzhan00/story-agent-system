"""One error envelope for the whole API: ``{"error": {"code", "message", "remediation", "detail"}}``.

``message``/``remediation`` are localised (``api.error.<code>`` / ``.remediation``); ``detail`` is the
developer-facing text. HTTP status carries the class of failure, ``code`` the specific one.
"""
from __future__ import annotations

import logging
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from storyagent.agents.registry import AgentSpecError
from storyagent.auth import AuthError
from storyagent.config import ConfigError
from storyagent.launcher import LaunchError
from storyagent.media.ledger import LedgerError
from storyagent.media.library import LibraryError
from storyagent.media.specs import SpecError, SpecLocked
from storyagent.projects import ProjectNotFound, ProjectStateError
from storyagent.story import ShotLocked, StoryError, StoryNotFound
from storyagent.workflow import WorkflowStateError
from storyagent.workshop import WorkshopError

from .context import translator_of

log = logging.getLogger(__name__)

_NOT_FOUND = re.compile(r"^(unknown|no) ", re.IGNORECASE)


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.status, self.code, self.detail = status, code, detail


def respond(request: Request, status: int, code: str, detail: object = None) -> JSONResponse:
    t = translator_of(request)
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": t(f"api.error.{code}"),
                "remediation": t(f"api.error.{code}.remediation"),
                "detail": detail,
            }
        },
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError) -> JSONResponse:
        return respond(request, exc.status, exc.code, exc.detail)

    @app.exception_handler(AuthError)
    async def _auth(request: Request, exc: AuthError) -> JSONResponse:
        return respond(request, exc.status, exc.code, exc.detail)

    @app.exception_handler(LaunchError)
    async def _launch(request: Request, exc: LaunchError) -> JSONResponse:
        status = {"launch_unknown": 404, "launch_not_startable": 409, "launch_missing": 424}.get(exc.code, 500)
        return respond(request, status, exc.code, exc.detail)

    @app.exception_handler(WorkshopError)
    async def _workshop(request: Request, exc: WorkshopError) -> JSONResponse:
        return respond(request, 422, exc.code, exc.detail)

    @app.exception_handler(ProjectNotFound)
    async def _project(request: Request, exc: ProjectNotFound) -> JSONResponse:
        return respond(request, 404, "not_found", f"project {exc}")

    @app.exception_handler(ProjectStateError)
    async def _project_state(request: Request, exc: ProjectStateError) -> JSONResponse:
        return respond(request, 409, "conflict", str(exc))

    @app.exception_handler(SpecLocked)
    async def _locked(request: Request, exc: SpecLocked) -> JSONResponse:
        return respond(request, 409, "spec_locked", str(exc))

    @app.exception_handler(SpecError)
    async def _spec(request: Request, exc: SpecError) -> JSONResponse:
        msg = str(exc)
        if _NOT_FOUND.match(msg):
            return respond(request, 404, "not_found", msg)
        if "already exists" in msg:
            return respond(request, 409, "conflict", msg)
        return respond(request, 422, "invalid", msg)

    @app.exception_handler(LedgerError)
    async def _ledger(request: Request, exc: LedgerError) -> JSONResponse:
        msg = str(exc)
        if _NOT_FOUND.match(msg):
            return respond(request, 404, "not_found", msg)
        return respond(request, 409, "conflict", msg)

    @app.exception_handler(LibraryError)
    async def _library(request: Request, exc: LibraryError) -> JSONResponse:
        msg = str(exc)
        if _NOT_FOUND.match(msg):
            return respond(request, 404, "not_found", msg)
        if "already exists" in msg:
            return respond(request, 409, "conflict", msg)
        return respond(request, 422, "invalid", msg)

    @app.exception_handler(StoryNotFound)
    async def _story_missing(request: Request, exc: StoryNotFound) -> JSONResponse:
        return respond(request, 404, "not_found", str(exc))

    @app.exception_handler(ShotLocked)
    async def _shot_locked(request: Request, exc: ShotLocked) -> JSONResponse:
        return respond(request, 409, "shot_locked", str(exc))

    @app.exception_handler(StoryError)
    async def _story(request: Request, exc: StoryError) -> JSONResponse:
        return respond(request, 422, "invalid", str(exc))

    @app.exception_handler(WorkflowStateError)
    async def _workflow(request: Request, exc: WorkflowStateError) -> JSONResponse:
        msg = str(exc)
        if _NOT_FOUND.match(msg):
            return respond(request, 404, "not_found", msg)
        return respond(request, 409, "conflict", msg)

    @app.exception_handler(AgentSpecError)
    async def _agent_spec(request: Request, exc: AgentSpecError) -> JSONResponse:
        msg = str(exc)
        return respond(request, 404 if msg.startswith("unknown agent") else 422, "not_found" if msg.startswith(
            "unknown agent") else "invalid", msg)

    @app.exception_handler(ConfigError)
    async def _config(request: Request, exc: ConfigError) -> JSONResponse:
        return respond(request, 422, "invalid", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return respond(request, 422, "invalid", errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "invalid", 409: "conflict"}.get(exc.status_code, "internal")
        return respond(request, exc.status_code, code, exc.detail)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return respond(request, 500, "internal", type(exc).__name__)
