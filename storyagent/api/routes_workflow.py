from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from storyagent import script_text
from storyagent import voices as voices_mod
from storyagent.agents import AgentRunner
from storyagent.compose.export import ExportError, build_package
from storyagent.content import plan_gates, project_rating
from storyagent.domain import QualityTier
from storyagent.llm import LLMError, LLMRouter
from storyagent.media.registry import default_registry
from storyagent.media.worker import worker_age
from storyagent.pipelines import project_settings_for
from storyagent.workflow import STAGES, WorkflowEngine, WorkflowStateError, preflight, revisions
from storyagent.workflow.media import default_voice_of, inputs_fit, to_make, uploaded_reference_uses
from storyagent.workflow.plan import produce_plan, totals, video_summary
from storyagent.workflow.series import series_board
from storyagent.workflow.stages import Ctx, StageError
from storyagent.workflow.tiering import policy_from

from .context import ApiContext, ctx_of, translator_of
from .errors import ApiError
from .schemas import (
    AutoVoicesOut,
    PlanGate,
    PlannedTaskOut,
    PlanTotals,
    PreflightFinding,
    PreflightOut,
    ProducePlanOut,
    RevisionDecided,
    RevisionDecision,
    RevisionOut,
    RevisionsOut,
    ScriptReviewOut,
    SeriesBoardOut,
    VideoSummary,
    VoiceAssignedOut,
    WorkflowOut,
    WorkflowRunCreate,
    WorkflowRunOut,
    WorkflowRunRow,
    WorkflowStageOut,
)
from .visibility import allow_export, too_young

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


def look_ahead(ctx: ApiContext, project_id: int, stages: list[str] | None, params: dict[str, Any]
               ) -> tuple[list[str], list[preflight.Finding]]:
    """What this run of the project would do now (``stages`` ``None`` = a resume; with ``params.full`` to the film),
    and whether everything it needs is there. The same look for a run to the film and a run of single stages; read
    only."""
    project = ctx.projects.get(project_id)
    planned = preflight.run_stages(ctx.workflow.plan(project_id, stages, params))
    cfg = project_settings_for(ctx.settings, ctx.db, project_id)    # the models of the project's pipeline plan
    registry = default_registry(cfg)
    tier = QualityTier(str(params.get("tier") or cfg.workflow.get("tier", "draft")))
    # the produce stage as the worker will see it, to ask what it would make (nothing is run through it)
    stage = Ctx(settings=cfg, db=ctx.db, store=ctx.story, state=ctx.workflow, agents=cast(AgentRunner, None),
                projects=ctx.projects, project=project, params=dict(params), spec_store=ctx.specs)
    todo = None
    if not any(s not in preflight.MEDIA_STAGES for s in planned):   # the story stands: what is left is known
        todo = []
        if "produce" in planned:
            try:
                todo = to_make(stage, ctx.ledger, ctx.specs.list_current(project_id), tier)
            except StageError:
                todo = []                                           # the stage itself says what it is missing
    rating = project_rating(ctx.db, project_id)
    people = ctx.story.list_characters(project_id)
    found = preflight.look_ahead(
        ctx.settings, cfg, LLMRouter(ctx.settings, ctx.db), registry, project_id=project_id,
        project_settings=project.settings, stages=planned, specs=todo,
        characters=people, default_voice=default_voice_of(cfg, project.settings),
        library_voices=sum(1 for v in ctx.library.list(role="voice") if v.meta.get("consent")),
        gates=plan_gates(ctx.settings, ctx.db, project_id, rating), tools=preflight.tool_states(cfg))
    if "produce" in planned and todo is None:
        # no specs yet: the pictures people uploaded for the characters are already known
        found += preflight.check_references(cfg, registry, uploaded_reference_uses(cfg, people))[0]
    elif todo:
        found += inputs_fit(stage, ctx.ledger, registry, todo, tier,
                            down={f.subject for f in preflight.blocking(found)})
    found += preflight.check_worker(worker_age(ctx.settings))
    return planned, found


@router.get("/projects/{project_id}/preflight", response_model=PreflightOut)
def get_preflight(project_id: int, request: Request, episode: int = 1, episodes: str = "", stages: str = "",
                  tier: str = "") -> PreflightOut:
    """Before a run: the stages it would do and what is missing for them (a language model that is not installed, a
    media service that is not running, a model that does not take the reference images it would be given, people
    without a voice). Without ``stages`` it is the run to the film; ``stages=produce`` (comma separated) is a run of
    those stages only. Read only."""
    ctx = ctx_of(request)
    names = [s.strip() for s in stages.split(",") if s.strip()]
    params: dict[str, Any] = {"episodes": episodes} if episodes.strip() else {"episode": episode}
    if tier:
        params["tier"] = tier
    if not names:
        params["full"] = True
    try:
        planned, found = look_ahead(ctx, project_id, names or None, params)
    except (WorkflowStateError, ValueError) as exc:
        raise ApiError(422, "invalid", str(exc)) from exc
    return PreflightOut(ready=not preflight.blocking(found), stages=planned,
                        findings=[PreflightFinding(**f.to_dict()) for f in found])


@router.post("/projects/{project_id}/workflow/runs", response_model=WorkflowRunOut, status_code=201)
def start_run(project_id: int, body: WorkflowRunCreate, request: Request) -> WorkflowRunOut:
    """Queue a run; the worker executes it. Without ``stages`` the run resumes where the project stopped; with
    ``params.full`` such a run goes on to the media and the film (``produce``, ``compose``). Any run, to the film or
    of single stages, is refused (409 ``not_ready``, the findings in ``detail``) while something its stages need is
    missing, unless ``params.skip_preflight`` (or ``[workflow] preflight = false``)."""
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
    if ctx.settings.workflow.get("preflight", True) and not params.get("skip_preflight"):
        try:
            stopped = preflight.blocking(look_ahead(ctx, project_id, body.stages, params)[1])
        except ValueError as exc:                   # e.g. a tier that does not exist
            raise ApiError(422, "invalid", str(exc)) from exc
        if stopped:
            raise ApiError(409, "not_ready", [f.to_dict() for f in stopped])
    if body.stages is None:
        params["resume"] = True                 # every episode picks up where it stopped when the worker gets to it
    stages = [s for s in STAGES if any(s == name for _, name in plan)]
    return run_out(ctx.workflow.enqueue(project_id, stages, params))


@router.get("/projects/{project_id}/revisions", response_model=RevisionsOut)
def list_revisions(project_id: int, request: Request, episode: int = 1) -> RevisionsOut:
    """The rewrites of written scenes of one episode, newest first, each with the shots it replaces and the shots it
    proposes as text: the ones that wait to be confirmed (``pending``) and the ones already in the script
    (``applied``; a run to the film applies the logic reader's rewrites itself)."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    t = translator_of(request)
    return RevisionsOut(episode=episode, items=[
        RevisionOut(id=r["id"], episode=episode, scene=r["scene"], source=r["source"], status=r["status"],
                    notes=r["notes"], before_text=script_text.shots_text(r["before"], t),
                    after_text=script_text.shots_text(r["after"], t), created_at=r["created_at"],
                    decided_at=r.get("decided_at"))
        for r in revisions.listed(ctx.workflow, project_id, episode)])


@router.post("/projects/{project_id}/revisions/decide", response_model=RevisionDecided)
def decide_revisions(project_id: int, body: RevisionDecision, request: Request) -> RevisionDecided:
    """Put confirmed rewrites into the script (``accept``), drop them (``discard``) or take an applied one back
    (``revert``). A scene that was edited since the rewrite was written is not overwritten (``conflicts``). When the
    script changed, its stage becomes stale: the next resume writes the shot cards of the changed scenes and does the
    stages built on the script again. 409 ``conflict`` while a run of the project is active."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    if any(r["status"] in ("queued", "running") for r in ctx.workflow.list_runs(project_id, 6)):
        raise ApiError(409, "conflict", "a run of this project is active; decide when it has finished")
    done = revisions.decide(ctx.story, ctx.workflow, project_id, body.episode, accept=set(body.accept),
                            discard=set(body.discard), revert=set(body.revert))
    if done["applied"] or done["reverted"]:
        ctx.workflow.set_stage(project_id, "script", "stale", episode=body.episode)
    return RevisionDecided(**done)


@router.get("/projects/{project_id}/series", response_model=SeriesBoardOut)
def get_series(project_id: int, request: Request) -> SeriesBoardOut:
    """The dashboard: every season and episode with stage states and how much media is made (counts reconcile with the
    shot and asset lists)."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    return SeriesBoardOut.model_validate(series_board(
        ctx.story, ctx.workflow, ctx.specs, ctx.ledger, project_id,
        project_settings_for(ctx.settings, ctx.db, project_id)))


@router.get("/projects/{project_id}/produce/plan", response_model=ProducePlanOut)
def get_produce_plan(project_id: int, request: Request, tier: str = "", force: bool = False) -> ProducePlanOut:
    """What producing the media would make, task by task, with models and estimates - to confirm before it runs.
    Start the confirmed part with ``POST .../workflow/runs {"stages": ["produce"], "params": {"only": [spec ids],
    "tier": ...}}``."""
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    cfg = project_settings_for(ctx.settings, ctx.db, project_id)    # the models of the project's pipeline plan
    tier = tier or str(cfg.workflow.get("tier", "draft"))           # no tier asked: the plan's own
    try:
        level = QualityTier(tier)
    except ValueError:
        raise ApiError(422, "invalid", f"unknown tier {tier!r}") from None
    rating = project_rating(ctx.db, project_id)
    gates = plan_gates(ctx.settings, ctx.db, project_id, rating)
    tasks = produce_plan(cfg, ctx.story, ctx.specs, ctx.ledger, project_id, level, force=force,
                         project_settings=project.settings, gated=ctx.settings.content.policy(rating).gates)
    budget = policy_from(cfg.workflow, project.settings, 1.0).budget_sec
    return ProducePlanOut(tier=level.value, tasks=[PlannedTaskOut.model_validate(t.__dict__) for t in tasks],
                          gates=[PlanGate(**g) for g in gates], totals=PlanTotals.model_validate(totals(tasks)),
                          video=VideoSummary.model_validate(video_summary(tasks, budget)))


@router.post("/projects/{project_id}/voices/auto", response_model=AutoVoicesOut)
def auto_voices(project_id: int, request: Request) -> AutoVoicesOut:
    """Give every character that has no voice one from the library (by gender, one each, consent-flagged voices only)
    and put it on that character's spoken lines. A character whose gender cannot be told, or that has no voice of its
    gender left, is listed in ``unresolved``; the voice of a character that already has one is never changed."""
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    done = voices_mod.auto_assign(ctx.story, ctx.library, ctx.specs, project_id,
                                  adults_only=ctx.settings.content.policy(project.rating).gates)
    return AutoVoicesOut(assigned=[VoiceAssignedOut(**vars(a)) for a in done.assigned],
                         unresolved=[VoiceAssignedOut(**vars(a)) for a in done.unresolved])


@router.get("/projects/{project_id}/workflow/runs", response_model=list[WorkflowRunOut])
def list_runs(project_id: int, request: Request, limit: int = 20) -> list[WorkflowRunOut]:
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    return [run_out(r) for r in ctx.workflow.list_runs(project_id, min(max(limit, 1), 100))]


@router.get("/workflow/runs", response_model=list[WorkflowRunRow])
def list_all_runs(request: Request, active: bool = False, limit: int = 30) -> list[WorkflowRunRow]:
    """The story runs (plan, characters, script, shots, ...) of every project, for the queue page: the waiting and
    running ones first, then the newest. A project the caller is too young for is left out."""
    ctx = ctx_of(request)
    names = {p.id: p.name for p in ctx.projects.list() if not too_young(p.rating, None)}
    runs = ctx.workflow.list_all(min(max(limit, 1), 100) * 3, active_only=active)
    return [WorkflowRunRow(**run_out(r).model_dump(), project_name=names[r["project_id"]])
            for r in runs if r["project_id"] in names][:min(max(limit, 1), 100)]


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
def episode_package(project_id: int, episode: int, request: Request, confirm_restricted: bool = False
                    ) -> FileResponse:
    """The delivery package of an assembled episode as one zip (film, subtitles, stems, manifest, provenance).
    A project of a rating that does not allow export needs ``confirm_restricted=true`` and an unlocked session."""
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    confirmed = allow_export(request, project_id, confirm_restricted, what=f"package ep{episode}")
    work = Path(tempfile.mkdtemp(prefix="package-", dir=ctx.settings.home))
    try:
        build_package(ctx.settings, ctx.ledger, project_id, episode, work / "package",
                      dict(ctx.settings.delivery_profile(project.delivery_profile)), confirm_restricted=confirmed)
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
