from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from storyagent.workflow import WorkflowStateError

from .context import ctx_of
from .errors import ApiError
from .schemas import WorkflowOut, WorkflowRunCreate, WorkflowRunOut, WorkflowStageOut

router = APIRouter(prefix="/api", tags=["workflow"])


def run_out(run: dict[str, Any]) -> WorkflowRunOut:
    return WorkflowRunOut.model_validate(run)


@router.get("/projects/{project_id}/workflow", response_model=WorkflowOut)
def get_workflow(project_id: int, request: Request) -> WorkflowOut:
    """Stage states plus the active run (with its live progress) and the last few runs."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    runs = [run_out(r) for r in ctx.workflow.list_runs(project_id, 6)]
    active = next((r for r in runs if r.status in ("queued", "running")), None)
    return WorkflowOut(stages=[WorkflowStageOut.model_validate(s) for s in ctx.workflow.stages(project_id)],
                       active_run=active, recent_runs=runs)


@router.post("/projects/{project_id}/workflow/runs", response_model=WorkflowRunOut, status_code=201)
def start_run(project_id: int, body: WorkflowRunCreate, request: Request) -> WorkflowRunOut:
    """Queue a run; the worker executes it. Without ``stages`` the run resumes where the project stopped."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    stages = ctx.workflow.pending_stages(project_id, body.stages)
    if not stages:
        raise ApiError(409, "nothing_to_do", "every stage is already completed")
    return run_out(ctx.workflow.enqueue(project_id, stages, body.params))


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
