"""FastAPI application factory.

    storyagent serve            # uvicorn on [api] host:port from services.toml
    GET /api/docs               # interactive OpenAPI;  GET /api/openapi.json is the client-generation contract

The API and the worker are separate processes that only share the SQLite ledger, so either can be
restarted without the other. The API never runs generation work itself.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import storyagent
from storyagent.auth import AuthStore
from storyagent.config import Settings, get_settings
from storyagent.config.reload import fingerprint
from storyagent.db import Database, migrate
from storyagent.media.ledger import Ledger
from storyagent.media.library import Library
from storyagent.media.specs import SpecStore
from storyagent.projects import ProjectStore
from storyagent.review import ReviewStore
from storyagent.story import StoryStore
from storyagent.workflow import WorkflowState

from . import (
    errors,
    reloading,
    routes_ai,
    routes_assets,
    routes_auth,
    routes_config,
    routes_content,
    routes_delivery,
    routes_grammar,
    routes_interchange,
    routes_jobs,
    routes_launch,
    routes_library,
    routes_llm,
    routes_pack,
    routes_pipeline,
    routes_projects,
    routes_review,
    routes_specs,
    routes_story,
    routes_system,
    routes_tools,
    routes_wiki,
    routes_workflow,
    routes_workshop,
)
from .auth_middleware import AuthMiddleware
from .context import ApiContext


def build_context(settings: Settings) -> ApiContext:
    db = Database(settings.db_path)
    migrate(db)
    ledger = Ledger(db, settings)
    specs, story = SpecStore(db, settings, ledger), StoryStore(db)
    return ApiContext(
        settings, db, ledger, specs, ProjectStore(db, settings),
        Library(db, settings, ledger), story, WorkflowState(db), AuthStore(db, settings),
        ReviewStore(db, ledger, specs, story),
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


def create_app(settings: Settings | None = None, *, loader: Callable[[], Settings] | None = None) -> FastAPI:
    """``loader`` reads the configuration afresh (default: the process settings, re-read); without it and with explicit
    ``settings`` (tests) the server does not watch the config folder."""
    watching = settings is None or loader is not None
    given = settings is not None
    settings = settings or get_settings()

    def no_loader() -> Settings:
        raise RuntimeError("this app was given settings and no way to read them again")

    reread = loader or (no_loader if given else (lambda: get_settings(reload=True)))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ctx = build_context(settings)
        app.state.reload_error = ""
        app.state.reload_loader = reread
        app.state.seen = fingerprint(settings.config_dir)
        task = asyncio.create_task(reloading.watch(app, reread, float(settings.api.get("reload_seconds", 3)))) \
            if watching else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(
        title="Story Agent API",
        version=storyagent.__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(AuthMiddleware)       # added before CORS, so CORS stays outermost (preflights pass)
    origins = list(settings.api["cors_origins"])
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"],
            expose_headers=["Content-Disposition"],
        )
    errors.install(app)
    for module in (routes_auth, routes_config, routes_review, routes_projects, routes_jobs, routes_assets,
                   routes_library, routes_pack, routes_pipeline, routes_delivery, routes_content, routes_grammar,
                   routes_interchange,
                   routes_specs, routes_story, routes_workflow, routes_system, routes_ai, routes_launch,
                   routes_workshop, routes_llm, routes_wiki, routes_tools):
        app.include_router(module.router)
    web = web_root(settings)
    if web is not None:
        mount_web(app, web)
    return app


def serve(settings: Settings, host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        create_app(settings, loader=lambda: get_settings(reload=True)),    # a changed config folder is read again
        host=host or str(settings.api["host"]),
        port=port or int(settings.api["port"]),
        log_level="info",
    )
