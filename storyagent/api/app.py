"""FastAPI application factory.

    storyagent serve            # uvicorn on [api] host:port from services.toml
    GET /api/docs               # interactive OpenAPI;  GET /api/openapi.json is the client-generation contract

The API and the worker are separate processes that only share the SQLite ledger, so either can be
restarted without the other. The API never runs generation work itself.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import storyagent
from storyagent.config import Settings, get_settings
from storyagent.db import Database, migrate
from storyagent.media.ledger import Ledger
from storyagent.media.library import Library
from storyagent.media.specs import SpecStore
from storyagent.projects import ProjectStore
from storyagent.story import StoryStore
from storyagent.workflow import WorkflowState

from . import (
    errors,
    routes_assets,
    routes_jobs,
    routes_library,
    routes_projects,
    routes_specs,
    routes_story,
    routes_system,
    routes_workflow,
)
from .context import ApiContext


def build_context(settings: Settings) -> ApiContext:
    db = Database(settings.db_path)
    migrate(db)
    ledger = Ledger(db, settings)
    return ApiContext(
        settings, db, ledger, SpecStore(db, settings, ledger), ProjectStore(db, settings),
        Library(db, settings, ledger), StoryStore(db), WorkflowState(db),
    )


def web_root(settings: Settings) -> Path | None:
    """The built web client, if there is one (``[api] web_dir``, default ``web/dist`` beside the package)."""
    raw = str(settings.api.get("web_dir", "")).strip()
    root = Path(raw).expanduser() if raw else Path(storyagent.__file__).resolve().parents[1] / "web" / "dist"
    if not root.is_absolute():
        root = settings.home / root
    return root if (root / "index.html").is_file() else None


def mount_web(app: FastAPI, root: Path) -> None:
    app.mount("/assets", StaticFiles(directory=root / "assets"), name="web-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        target = (root / path).resolve()
        if path and target.is_file() and root.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(root / "index.html")        # client-side routes


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ctx = build_context(settings)
        yield

    app = FastAPI(
        title="Story Agent API",
        version=storyagent.__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    origins = list(settings.api["cors_origins"])
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"],
            expose_headers=["Content-Disposition"],
        )
    errors.install(app)
    for module in (routes_projects, routes_jobs, routes_assets, routes_library, routes_specs, routes_story,
                   routes_workflow, routes_system):
        app.include_router(module.router)
    web = web_root(settings)
    if web is not None:
        mount_web(app, web)
    return app


def serve(settings: Settings, host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        create_app(settings),
        host=host or str(settings.api["host"]),
        port=port or int(settings.api["port"]),
        log_level="info",
    )
