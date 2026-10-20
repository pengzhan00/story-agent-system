"""FastAPI application factory.

    storyagent serve            # uvicorn on [api] host:port from services.toml
    GET /api/docs               # interactive OpenAPI;  GET /api/openapi.json is the client-generation contract

The API and the worker are separate processes that only share the SQLite ledger, so either can be
restarted without the other. The API never runs generation work itself.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    return app


def serve(settings: Settings, host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        create_app(settings),
        host=host or str(settings.api["host"]),
        port=port or int(settings.api["port"]),
        log_level="info",
    )
