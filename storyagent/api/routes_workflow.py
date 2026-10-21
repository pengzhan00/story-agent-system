from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from storyagent.compose.export import ExportError, build_package
from storyagent.domain import QualityTier
from storyagent.llm import LLMError
from storyagent.workflow import STAGES, WorkflowEngine, WorkflowStateError
from storyagent.workflow.plan import produce_plan, totals, video_summary
from storyagent.workflow.series import series_board
from storyagent.workflow.tiering import policy_from

from .context import ctx_of
from .errors import ApiError
from .schemas import (
    PlannedTaskOut,
    PlanTotals,
    ProducePlanOut,
    ScriptReviewOut,
    SeriesBoardOut,
    VideoSummary,
    WorkflowOut,
    WorkflowRunCreate,
    WorkflowRunOut,
    WorkflowStageOut,
)

router = APIRouter(prefix="/api", tags=["workflow"])


def run_out(run: dict[str, Any]) -> WorkflowRunOut:
    return WorkflowRunOut.model_validate(run)


@router.get("/projects/{project_id}/workflow", response_model=WorkflowOut)
def get_workflow(project_id: int, request: Request, episode: int = 1) -> WorkflowOut:
    """Stage states of one episode (series-wide stages are shared) plus the active run and the last few runs."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    runs = [run_out(r) for r in ctx.workflow.list_runs(project_id, 6)]
    active = next((r for r in runs if r.status in ("queued", "running")), None)
    return WorkflowOut(stages=[WorkflowStageOut.model_validate(s) for s in ctx.workflow.stages(project_id, episode)],
                       active_run=active, recent_runs=runs, episode=episode)


@router.post("/projects/{project_id}/workflow/runs", response_model=WorkflowRunOut, status_code=201)
def start_run(project_id: int, body: WorkflowRunCreate, request: Request) -> WorkflowRunOut:
    """Queue a run; the worker executes it. Without ``stages`` the run resumes where the project stopped."""
    ctx = ctx_of(request)
    if ctx.projects.get(project_id).status == "trashed":
        raise ApiError(409, "conflict", "the project is in the recycle bin; restore it first")
    params = dict(body.params)
    try:
        plan = ctx.workflow.plan(project_id, body.stages, params)
    except WorkflowStateError as exc:
        raise ApiError(422, "invalid", str(exc)) from exc
    if not plan:
        raise ApiError(409, "nothing_to_do", "every stage is already completed")
    if body.stages is None:
        params["resume"] = True                 # every episode picks up where it stopped when the worker gets to it
    stages = [s for s in STAGES if any(s == name for _, name in plan)]
    return run_out(ctx.workflow.enqueue(project_id, stages, params))


@router.get("/projects/{project_id}/series", response_model=SeriesBoardOut)
def get_series(project_id: int, request: Request) -> SeriesBoardOut:
    """The dashboard: every season and episode with stage states and how much media is made (counts reconcile with the
    shot and asset lists)."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    return SeriesBoardOut.model_validate(series_board(ctx.story, ctx.workflow, ctx.specs, ctx.ledger, project_id))


@router.get("/projects/{project_id}/produce/plan", response_model=ProducePlanOut)
def get_produce_plan(project_id: int, request: Request, tier: str = "draft", force: bool = False) -> ProducePlanOut:
    """What producing the media would make, task by task, with models and estimates - to confirm before it runs.
    Start the confirmed part with ``POST .../workflow/runs {"stages": ["produce"], "params": {"only": [spec ids],
    "tier": ...}}``."""
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    try:
        level = QualityTier(tier)
    except ValueError:
        raise ApiError(422, "invalid", f"unknown tier {tier!r}") from None
    tasks = produce_plan(ctx.settings, ctx.story, ctx.specs, ctx.ledger, project_id, level, force=force,
                         project_settings=project.settings)
    budget = policy_from(ctx.settings.workflow, project.settings, 1.0).budget_sec
    return ProducePlanOut(tier=level.value, tasks=[PlannedTaskOut.model_validate(t.__dict__) for t in tasks],
                          totals=PlanTotals.model_validate(totals(tasks)),
                          video=VideoSummary.model_validate(video_summary(tasks, budget)))


@router.get("/projects/{project_id}/workflow/runs", response_model=list[WorkflowRunOut])
def list_runs(project_id: int, request: Request, limit: int = 20) -> list[WorkflowRunOut]:
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    return [run_out(r) for r in ctx.workflow.list_runs(project_id, min(max(limit, 1), 100))]


@router.get("/workflow/runs/{run_id}", response_model=WorkflowRunOut)
def get_run(run_id: str, request: Request) -> WorkflowRunOut:
    return run_out(ctx_of(request).workflow.get_run(run_id))


@router.post("/workflow/runs/{run_id}/cancel", response_model=WorkflowRunOut)
def cancel_run(run_id: str, request: Request) -> WorkflowRunOut:
    """Queued runs are cancelled at once; a running one stops between two LLM calls (what is saved stays saved)."""
    wf = ctx_of(request).workflow
    try:
        wf.request_cancel(run_id)
    except WorkflowStateError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    return run_out(wf.get_run(run_id))


@router.get("/projects/{project_id}/episodes/{episode}/package")
def episode_package(project_id: int, episode: int, request: Request) -> FileResponse:
    """The delivery package of an assembled episode as one zip (film, subtitles, stems, manifest, provenance)."""
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    work = Path(tempfile.mkdtemp(prefix="package-", dir=ctx.settings.home))
    try:
        build_package(ctx.settings, ctx.ledger, project_id, episode, work / "package",
                      dict(ctx.settings.delivery_profile(project.delivery_profile)))
    except ExportError as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise ApiError(409, "episode_not_ready", str(exc)) from exc
    archive = shutil.make_archive(str(work / f"{project.slug}-ep{episode:02d}"), "zip", work / "package")
    return FileResponse(archive, media_type="application/zip", filename=Path(archive).name,
                        background=BackgroundTask(shutil.rmtree, work, ignore_errors=True))


@router.post("/projects/{project_id}/review", response_model=ScriptReviewOut)
def review_script(project_id: int, request: Request, episode: int = 1) -> ScriptReviewOut:
    """Ask the script editor agent for a critique (a language-model call: can take a minute; changes nothing)."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    engine = WorkflowEngine(ctx.settings, ctx.db, spec_store=ctx.specs)
    engine.router.heavy_wait_sec = 0          # a request must not hang for minutes behind a video model
    try:
        data = engine.review_script(project_id, episode)
    except LLMError as exc:
        if exc.code == "heavy_busy":
            raise ApiError(409, "heavy_busy", exc.message) from exc
        raise ApiError(502, "llm_failed", f"{exc.code}: {exc.message}") from exc
    return ScriptReviewOut.model_validate(data)
