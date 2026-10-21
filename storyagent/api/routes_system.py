from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

import storyagent
from storyagent.db import current_version
from storyagent.domain import JobStatus, MediaKind, QualityTier, SubjectType
from storyagent.i18n import available_locales
from storyagent.media import healthcheck as health
from storyagent.media.registry import default_registry
from storyagent.workflow import STAGES

from .context import ApiContext, Translator, ctx_of, translator_of
from .errors import ApiError
from .routes_jobs import queue_state
from .schemas import (
    DeliveryProfileOut,
    HealthEntry,
    HealthOut,
    JobOut,
    LabeledValue,
    Meta,
    ModelOut,
    Ready,
    RecipeOut,
    RecipeStepOut,
    WorkflowRunOut,
)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/ready", response_model=Ready)
def ready(request: Request) -> Ready:
    """Cheap liveness + schema check (no external probes)."""
    return Ready(schema_version=current_version(ctx_of(request).db))


@router.get("/health", response_model=HealthOut)
def pipeline_health(request: Request) -> HealthOut:
    """Same report as `storyagent media healthcheck` (probes services, tools, models, disk)."""
    ctx = ctx_of(request)
    t = translator_of(request)
    report = health.run_checks(ctx.settings, registry=default_registry(ctx.settings))
    entries = [
        HealthEntry(
            name=e.name, category=e.category, state=e.state.value, label=t(f"health.{e.state.value}"),
            detail=e.detail, missing=list(e.missing), required=e.required, ok=e.ok,
        )
        for e in report.entries
    ]
    return HealthOut(exit_code=report.exit_code, entries=entries)  # type: ignore[arg-type]


def _labeled(t: Translator, prefix: str, values: Any) -> list[LabeledValue]:
    return [LabeledValue(value=v.value, label=t(f"{prefix}.{v.value}")) for v in values]


@router.get("/meta", response_model=Meta)
def meta(request: Request) -> Meta:
    """Everything a UI needs to render pickers and labels without hard-coding any of it."""
    ctx = ctx_of(request)
    s = ctx.settings
    t = translator_of(request)
    return Meta(
        version=storyagent.__version__,
        locale=t.locale,
        locales=available_locales(),
        kinds=_labeled(t, "media.kind", MediaKind),
        tiers=_labeled(t, "media.tier", QualityTier),
        statuses=_labeled(t, "status", JobStatus),
        subject_types=_labeled(t, "spec.subject", SubjectType),
        workflow_stages=[LabeledValue(value=n, label=t(f"workflow.stage.{n}")) for n in STAGES],
        workflow_statuses=[LabeledValue(value=n, label=t(f"workflow.status.{n}"))
                           for n in ("pending", "queued", "running", "completed", "failed", "stale", "cancelled")],
        models=[
            ModelOut(
                ref=m.ref, kind=m.kind, provider=m.provider, version=m.version, family=m.family, heavy=m.heavy,
                est_memory_gib=m.est_memory_gib, license_tag=m.license_tag, commercial_ok=m.commercial_ok,
                attribution=m.attribution, prompt_profile=m.prompt_profile, timeout_sec=m.timeout_sec,
                params=dict(m.params), tiers={k: dict(v) for k, v in m.tiers.items()},
            )
            for m in s.models.values()
        ],
        delivery_profiles=[
            DeliveryProfileOut(
                name=n, label=t(str(p.get("label_key", ""))) if p.get("label_key") else n, width=int(p["width"]),
                height=int(p["height"]), fps=int(p["fps"]), target_sec=p.get("target_sec"),
                min_episode_sec=float(p.get("min_episode_sec", 0)), max_episode_sec=float(p.get("max_episode_sec", 0)))
            for n, p in s.delivery["profiles"].items()
        ],
        delivery_default=str(s.delivery["default"]["profile"]),
        defaults=dict(s.default_models),
        recipes=[
            RecipeOut(
                name=r.name, label=t(r.label_key),
                steps=[
                    RecipeStepOut(
                        kind=st.kind, model=st.model, tier=st.tier, slot=st.slot, when=st.when, params=dict(st.params)
                    )
                    for st in r.steps
                ],
            )
            for r in s.recipes.values()
        ],
        compliance_enabled=s.compliance.enabled,
        llm_unload_policy=s.llm_unload_policy,
        queue=queue_state(request),
    )


@router.get("/i18n/{locale}", response_model=dict[str, str])
def catalog(locale: str) -> dict[str, str]:
    """The flat message catalog, so the web client uses the same keys and texts as the backend."""
    from storyagent.i18n.catalog import LocaleError, get_catalog

    if locale not in available_locales():
        raise ApiError(404, "not_found", f"locale {locale}")
    try:
        cat = get_catalog(locale)
    except LocaleError as exc:  # pragma: no cover - a broken catalog file
        raise ApiError(500, "internal", str(exc)) from exc
    return {**cat.fallback, **cat.messages}


# -- server-sent events -----------------------------------------------------------------------------
def _sig(job: JobOut) -> tuple[Any, ...]:
    return (job.status, job.progress, job.progress_msg, job.attempt, job.error_code, job.cancel_requested)


def _frame(event: str, data: Any, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def collect_changes(
    ctx: ApiContext, last_event_id: int, known: dict[str, tuple[Any, ...]], project_id: int | None
) -> tuple[int, list[JobOut]]:
    """Jobs that changed since the last tick: those that logged events, plus running jobs (progress
    is heartbeated without events). Blocking; runs in a worker thread."""
    high, touched = ctx.ledger.touched_jobs(last_event_id, project_id=project_id)
    by_id = {j.id: j for j in touched}
    for j in ctx.ledger.running():
        if project_id is None or j.project_id == project_id:
            by_id.setdefault(j.id, j)
    changed: list[JobOut] = []
    for rec in by_id.values():
        out = JobOut.model_validate(rec)
        if known.get(out.id) != _sig(out):
            known[out.id] = _sig(out)
            changed.append(out)
    return high, changed


def _run_sig(run: WorkflowRunOut) -> tuple[Any, ...]:
    pr = run.progress
    return (run.status, pr.percent, pr.stage, pr.done, pr.total, pr.message, run.cancel_requested, run.error_code)


def collect_runs(ctx: ApiContext, known: dict[str, tuple[Any, ...]], project_id: int | None) -> list[WorkflowRunOut]:
    """Workflow runs that changed since the last tick: everything queued/running plus runs that ended in the last
    minute (so the last frame, with the final status, always reaches the client). Blocking."""
    changed: list[WorkflowRunOut] = []
    for raw in ctx.workflow.recent_active(project_id):
        run = WorkflowRunOut.model_validate(raw)
        if known.get(run.id) != _run_sig(run):
            known[run.id] = _run_sig(run)
            changed.append(run)
    return changed


async def event_stream(
    request: Request, project_id: int | None, after: int | None, timeout: float
) -> AsyncIterator[str]:
    ctx = ctx_of(request)
    poll = float(ctx.settings.api["sse_poll_seconds"])
    keepalive = float(ctx.settings.api["sse_keepalive_seconds"])
    known: dict[str, tuple[Any, ...]] = {}
    started = last_sent = time.monotonic()

    yield "retry: 3000\n\n"
    # Snapshot of everything still in flight, so a fresh client is never blind.
    last_id = await run_in_threadpool(ctx.ledger.last_event_id) if after is None else after
    active = await run_in_threadpool(
        lambda: [j for j in ctx.ledger.queued(500) + ctx.ledger.running()
                 if project_id is None or j.project_id == project_id]
    )
    for rec in active:
        out = JobOut.model_validate(rec)
        known[out.id] = _sig(out)
        yield _frame("job", out.model_dump(mode="json"), last_id)
    runs_known: dict[str, tuple[Any, ...]] = {}
    for run in await run_in_threadpool(collect_runs, ctx, runs_known, project_id):
        yield _frame("workflow", run.model_dump(mode="json"))
    if after is not None:  # resume: replay what happened while the client was away
        last_id, changed = await run_in_threadpool(collect_changes, ctx, after, known, project_id)
        for out in changed:
            yield _frame("job", out.model_dump(mode="json"), last_id)

    while True:
        if await request.is_disconnected() or (timeout and time.monotonic() - started >= timeout):
            return
        last_id, changed = await run_in_threadpool(collect_changes, ctx, last_id, known, project_id)
        for out in changed:
            last_sent = time.monotonic()
            yield _frame("job", out.model_dump(mode="json"), last_id)
        for run in await run_in_threadpool(collect_runs, ctx, runs_known, project_id):
            last_sent = time.monotonic()
            yield _frame("workflow", run.model_dump(mode="json"))
        if time.monotonic() - last_sent >= keepalive:
            last_sent = time.monotonic()
            yield ": keepalive\n\n"
        await asyncio.sleep(poll)


@router.get("/events", response_class=StreamingResponse)
async def events(
    request: Request, project_id: int | None = None, after: int | None = None, timeout: float = 0
) -> StreamingResponse:
    """SSE feed of job changes (`event: job`, data = JobOut) and story-workflow progress (`event: workflow`, data =
    WorkflowRunOut with its live `progress`). Reconnect with `?after=<last id>` to catch up.
    `timeout` (seconds) closes the stream after that long; 0 = until the client disconnects."""
    return StreamingResponse(
        event_stream(request, project_id, after, timeout),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
