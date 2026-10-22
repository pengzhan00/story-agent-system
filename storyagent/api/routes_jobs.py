from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response

from storyagent.domain import JobStatus
from storyagent.media.contracts import MediaJob

from .context import ctx_of
from .schemas import CancelResult, JobCreate, JobCreated, JobEvent, JobOut, PriorityRequest, QueueState, RetryRequest

router = APIRouter(prefix="/api", tags=["jobs"])


def project_settings(request: Request, project_id: int | None) -> dict[str, Any]:
    if project_id is None:
        return {}
    return dict(ctx_of(request).projects.get(project_id).settings)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    request: Request,
    project_id: int | None = None,
    shot_id: int | None = None,
    spec_id: str | None = None,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[JobOut]:
    jobs = ctx_of(request).ledger.list_jobs(
        project_id=project_id, shot_id=shot_id, spec_id=spec_id, status=status, limit=limit)
    return [JobOut.model_validate(j) for j in jobs]


@router.post("/jobs", response_model=JobCreated, status_code=201)
def create_job(body: JobCreate, request: Request, response: Response) -> JobCreated:
    ctx = ctx_of(request)
    job = MediaJob(
        kind=body.kind, inputs=body.inputs, project_id=body.project_id, shot_id=body.shot_id,
        model_ref=body.model_ref, tier=body.tier, slot=body.slot, params=body.params, seed=body.seed,
        priority=body.priority,
    )
    rec, created = ctx.ledger.enqueue(job, project_settings(request, body.project_id))
    if not created:
        response.status_code = 200
    return JobCreated(job=JobOut.model_validate(rec), created=created)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, request: Request) -> JobOut:
    return JobOut.model_validate(ctx_of(request).ledger.get_job(job_id))


@router.get("/jobs/{job_id}/events", response_model=list[JobEvent])
def job_events(job_id: str, request: Request, after: int = 0) -> list[JobEvent]:
    ledger = ctx_of(request).ledger
    ledger.get_job(job_id)  # 404 for unknown ids instead of an empty list
    return [JobEvent(**e) for e in ledger.events(job_id, after)]


@router.post("/jobs/{job_id}/cancel", response_model=CancelResult)
def cancel_job(job_id: str, request: Request) -> CancelResult:
    return CancelResult(status=ctx_of(request).ledger.request_cancel(job_id))


@router.post("/jobs/priority", response_model=list[JobOut])
def prioritize_jobs(body: PriorityRequest, request: Request) -> list[JobOut]:
    """Move queued jobs ahead of / behind the rest of the queue (``move``) or give them a number (``priority``)."""
    jobs = ctx_of(request).ledger.reprioritize(body.ids, move=body.move, priority=body.priority)
    return [JobOut.model_validate(j) for j in jobs]


@router.post("/jobs/{job_id}/retry", response_model=JobOut, status_code=201)
def retry_job(job_id: str, request: Request, body: RetryRequest | None = None) -> JobOut:
    return JobOut.model_validate(ctx_of(request).ledger.retry(job_id, new_seed=bool(body and body.new_seed)))


@router.get("/queue", response_model=QueueState)
def queue(request: Request, project_id: int | None = None) -> QueueState:
    return queue_state(request, project_id)


def queue_state(request: Request, project_id: int | None = None) -> QueueState:
    """Counts of active jobs, all projects or one. They are the sizes of ``GET /api/jobs?status=...`` lists."""
    rows = ctx_of(request).db.conn.execute(
        "SELECT status, progress_msg, COUNT(*) AS n FROM media_jobs WHERE status IN ('queued','running') "
        "AND (? IS NULL OR project_id = ?) GROUP BY status, progress_msg", (project_id, project_id)
    ).fetchall()
    queued = running = 0
    waiting: dict[str, int] = {}
    for r in rows:
        if r["status"] == "running":
            running += r["n"]
        else:
            queued += r["n"]
            if str(r["progress_msg"]).startswith("wait."):
                waiting[r["progress_msg"]] = waiting.get(r["progress_msg"], 0) + r["n"]
    return QueueState(queued=queued, running=running, waiting=waiting)
