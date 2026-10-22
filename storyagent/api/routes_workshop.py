"""Workshop: run any media module on its own, configure what it starts from, or chain modules into a pipeline."""
from __future__ import annotations

import shutil
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from storyagent.media.library import import_file
from storyagent.media.registry import default_registry
from storyagent.workshop import Workshop, WorkshopError

from .context import ctx_of
from .errors import ApiError
from .routes_library import _receive
from .schemas import (
    AssetOut,
    JobOut,
    ParamsOut,
    PipelineBody,
    PipelineOut,
    PresetBody,
    RunBody,
    WorkshopHistoryItem,
    WorkshopModuleOut,
)

router = APIRouter(prefix="/api/workshop", tags=["workshop"])


def shop(request: Request) -> Workshop:
    ctx = ctx_of(request)
    return Workshop(ctx.settings, ctx.db, ctx.ledger, default_registry(ctx.settings))


@router.get("", response_model=list[WorkshopModuleOut])
def modules(request: Request, pipeline: str = "") -> list[dict[str, Any]]:
    """Every module with its kinds: the model it starts from (and the alternatives), the effective parameters, the
    parameters the adapter understands, and the inputs it takes. With ``pipeline`` it starts from that plan's models;
    models outside the plan say ``in_plan: false`` (a trial only, not for a project)."""
    return shop(request).describe(pipeline)


@router.get("/params", response_model=ParamsOut)
def params(request: Request, kind: str, model_ref: str, tier: str = "") -> dict[str, Any]:
    """The parameters a run of this model at this tier would use, to show and edit before running."""
    return shop(request).params_for(kind, model_ref, tier)


@router.put("/presets/{kind}", response_model=PresetBody)
def save_preset(kind: str, body: PresetBody, request: Request) -> dict[str, Any]:
    """Make the model, quality tier and parameter overrides the starting point of the module (until changed)."""
    return shop(request).save_preset(kind, body.model_ref, body.tier, body.params)


@router.delete("/presets/{kind}", status_code=204)
def reset_preset(kind: str, request: Request) -> None:
    shop(request).reset_preset(kind)


@router.post("/run", response_model=list[JobOut], status_code=201)
def run(body: RunBody, request: Request) -> list[Any]:
    """One module on its own. ``count`` > 1 queues several candidates (seed, seed+1, ...)."""
    jobs = shop(request).run(body.kind, body.model_dump(), body.count)
    return [JobOut.model_validate(j) for j in jobs]


@router.post("/pipelines", response_model=PipelineOut, status_code=201)
def pipeline(body: PipelineBody, request: Request) -> PipelineOut:
    """Queue the steps together. An input set to ``"@prev"`` takes the previous step's output and waits for it."""
    pid, jobs = shop(request).pipeline([s.model_dump() for s in body.steps])
    return PipelineOut(pipeline_id=pid, jobs=[JobOut.model_validate(j) for j in jobs])


@router.get("/history", response_model=list[WorkshopHistoryItem])
def history(
    request: Request, module: str = "", kind: str = "", limit: Annotated[int, Query(ge=1, le=200)] = 40,
) -> list[WorkshopHistoryItem]:
    """Standalone jobs (no project), newest first, with their assets."""
    kinds: list[str] | None = None
    if kind:
        kinds = [kind]
    elif module:
        from storyagent.workshop import MODULES

        kinds = [k.kind.value for m in MODULES if m.id == module for k in m.kinds]
        if not kinds:
            raise ApiError(404, "not_found", f"module {module}")
    return [
        WorkshopHistoryItem(job=JobOut.model_validate(j), assets=[AssetOut.model_validate(a) for a in assets])
        for j, assets in shop(request).history(kinds, limit)
    ]


@router.post("/upload", response_model=AssetOut, status_code=201)
async def upload(
    request: Request, filename: Annotated[str, Query(min_length=1, max_length=200)], slot: str = "main",
) -> AssetOut:
    """Raw-body upload of an input file (a portrait, a recording) that belongs to no project. Use it as
    ``asset:<id>`` in an input."""
    ctx = ctx_of(request)
    tmp = await _receive(request, ctx, filename)
    try:
        return AssetOut.model_validate(import_file(ctx.settings, ctx.ledger, tmp, project_id=None, slot=slot))
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


__all__ = ["router", "WorkshopError"]
