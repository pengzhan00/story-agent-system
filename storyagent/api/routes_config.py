"""Configuration of the running server: load a changed configuration folder without a restart."""
from __future__ import annotations

import anyio
from fastapi import APIRouter, Request

from .context import ctx_of
from .reloading import reload_now
from .schemas import ReloadResult

router = APIRouter(prefix="/api/config", tags=["config"])


@router.post("/reload", response_model=ReloadResult)
async def reload_config(request: Request) -> ReloadResult:
    """Read ``<config>/*.toml``, workflows and plugins afresh and use them from the next request on. The worker
    notices the same change on its own and swaps it in when it is between jobs."""
    app = request.app
    error = await anyio.to_thread.run_sync(reload_now, app, app.state.reload_loader)
    return ReloadResult(reloaded=not error, error=error, models=len(ctx_of(request).settings.models))
