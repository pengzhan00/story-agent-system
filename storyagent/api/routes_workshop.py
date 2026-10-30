"""Workshop: run any media module on its own, configure what it starts from, or chain modules into a pipeline."""
from __future__ import annotations

import shutil
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from storyagent.agents.runner import language_name
from storyagent.llm import LLMError, LLMRouter
from storyagent.media.library import import_file
from storyagent.media.registry import default_registry
from storyagent.wiki import ask
from storyagent.wiki.ask import Passage
from storyagent.wiki.store import WikiStore
from storyagent.workshop import Workshop, WorkshopError
from storyagent.workshop_assist import Assist

from .context import ctx_of
from .errors import ApiError
from .routes_library import _receive
from .routes_llm import _llm_error as llm_error
from .routes_review import actor_of
from .routes_wiki import _visible
from .schemas import (
    AssetOut,
    AssistDropped,
    AssistIn,
    AssistInputChange,
    AssistOut,
    AssistParamChange,
    AssistRequirementsBody,
    AssistRequirementsOut,
    AssistSource,
    JobOut,
    ParamsOut,
    PipelineBody,
    PipelineOut,
    PresetBody,
    RunBody,
    WorkshopHistoryItem,
    WorkshopModuleOut,
)
from .visibility import require_viewable

router = APIRouter(prefix="/api/workshop", tags=["workshop"])


def shop(request: Request) -> Workshop:
    ctx = ctx_of(request)
    return Workshop(ctx.settings, ctx.db, ctx.ledger, default_registry(ctx.settings))


def assist_of(request: Request) -> Assist:
    ctx = ctx_of(request)
    return Assist(ctx.settings, ctx.db, shop(request))


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


@router.get("/assist/{module}", response_model=AssistRequirementsOut)
def assist_requirements(module: str, request: Request) -> dict[str, str]:
    """The standing requirements the author wrote for the AI assistant of this module."""
    return assist_of(request).requirements(module)


@router.put("/assist/{module}", response_model=AssistRequirementsOut)
def save_assist_requirements(module: str, body: AssistRequirementsBody, request: Request) -> dict[str, str]:
    """Replace them (an empty text removes them). The assistant reads them on every call for this module."""
    return assist_of(request).save_requirements(module, body.requirements, actor_of(request))


@router.post("/assist", response_model=AssistOut)
def assist(body: AssistIn, request: Request) -> AssistOut:
    """The AI assistant of a module designs the form: prompt, lyrics, text and parameters, from the standing
    requirements, this request and the wiki. It only proposes; the page applies the proposal to the form. Only wiki
    pages the caller may see are read; a restricted page that was read is audited like opening it and the call is
    then made under that rating (a model that never leaves this computer)."""
    ctx = ctx_of(request)
    found: list[Passage] = []
    rating = ""
    if body.use_wiki:
        reqs = assist_of(request).requirements(body.module)["requirements"]
        text = " ".join([body.model_ref, body.kind.value, body.module, body.request, reqs[:300]])
        pool = [p for p in WikiStore(ctx.db, ctx.settings).all() if _visible(p)]
        found = ask.passages(pool, ask.terms_of(text), limit=6)
        for p in found:
            if p.page.rating != "general":
                require_viewable(request, p.page.rating, None, what=f"wiki:{p.page.slug}")
                rating = ctx.settings.content.higher(rating, p.page.rating)
    router_ = LLMRouter(ctx.settings, ctx.db)
    try:
        out = assist_of(request).design(
            module=body.module, kind=body.kind.value, model_ref=body.model_ref, tier=body.tier, values=body.inputs,
            current_params=body.params, request=body.request, language=language_name(body.language), found=found,
            rating=rating, router=router_)
    except LLMError as exc:
        raise llm_error(exc) from exc
    backend, model, _ = router_.resolve("prompter", rating=rating)
    return AssistOut(
        inputs=[AssistInputChange(**i) for i in out["inputs"]],
        params=[AssistParamChange(**p) for p in out["params"]],
        dropped=[AssistDropped(**d) for d in out["dropped"]], notes=out["notes"],
        sources=[AssistSource(n=p.n, slug=p.page.slug, title=p.page.title, source=p.page.source,
                              rating=p.page.rating, heading=p.heading, cited=p.n in out["cited"]) for p in found],
        backend=backend, model=model, seconds=out["seconds"])


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
