from __future__ import annotations

from fastapi import APIRouter, Request

from storyagent.brief import estimate, resolve_brief

from .context import ctx_of
from .errors import ApiError
from .schemas import (
    BriefAnswers,
    EstimateOut,
    EstimateRequest,
    ProjectCard,
    ProjectCreate,
    ProjectFromText,
    ProjectFromTextOut,
    ProjectOut,
    ProjectPatch,
    ProjectSettingsPatch,
    WorkflowRunOut,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(request: Request, trashed: bool = False) -> list[ProjectOut]:
    """Live projects; ``trashed=true`` lists the recycle bin instead."""
    return [ProjectOut.model_validate(p) for p in ctx_of(request).projects.list(trashed=trashed)]


@router.get("/overview", response_model=list[ProjectCard])
def overview(request: Request, trashed: bool = False) -> list[ProjectCard]:
    """For the list page: per project the step progress and the newest finished episode (open it to preview)."""
    ctx = ctx_of(request)
    cards: list[ProjectCard] = []
    for p in ctx.projects.list(trashed=trashed):
        states = [s for s in ctx.workflow.stages(p.id, 1) if s["stage"] != "outline" or s["status"] != "pending"]
        running = ctx.db.conn.execute(
            "SELECT 1 FROM workflow_runs WHERE project_id=? AND status IN ('queued','running')", (p.id,)).fetchone()
        film = ctx.db.conn.execute(
            "SELECT id, slot, duration_sec FROM media_assets WHERE project_id=? AND kind='compose.assemble'"
            " AND slot LIKE 'episode\\_%' ESCAPE '\\' AND is_current=1 AND trashed_at IS NULL"
            " ORDER BY slot DESC LIMIT 1", (p.id,)).fetchone()
        cards.append(ProjectCard(
            id=p.id, name=p.name, description=p.description, genre=p.genre, status=p.status, kind=p.kind,
            pipeline=p.pipeline,
            stages_done=sum(s["status"] == "completed" for s in states), stages_total=len(states),
            running=running is not None,
            episode_asset_id=film["id"] if film else None, episode_seconds=film["duration_sec"] if film else None,
            episode_number=int(str(film["slot"]).split("_")[1]) if film else None, updated_at=p.updated_at))
    return cards


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, request: Request) -> ProjectOut:
    """A project with its plan: the kind of work, the pipeline plan (one complete set of models, picture frame) and the
    brief (seasons, episodes, episode length) are decided here, once."""
    p = ctx_of(request).projects.create(
        body.name, genre=body.genre, description=body.description, content_language=body.content_language,
        delivery_profile=body.delivery_profile, kind=body.kind, pipeline=body.pipeline,
        brief=body.brief.model_dump(exclude_none=True) if body.brief else None,
    )
    return ProjectOut.model_validate(p)


@router.post("/estimate", response_model=EstimateOut)
def estimate_project(body: EstimateRequest, request: Request) -> EstimateOut:
    """How big the planned work is (tasks, machine time) before anything is created - for the creation wizard."""
    ctx = ctx_of(request)
    brief = resolve_brief(ctx.settings, body.kind, body.brief.model_dump(exclude_none=True) if body.brief else None)
    plan = ctx.projects.plan_of(body.pipeline, body.kind)
    return EstimateOut(brief=brief, **estimate(ctx.settings, ctx.ledger, brief, plan))


TEXT_KINDS = ("text.story", "text.script", "text.asr", "upload.text")
MAX_SOURCE_CHARS = 24000          # what the script stage reads of a source text


@router.post("/from-text", response_model=ProjectFromTextOut, status_code=201)
def create_project_from_text(body: ProjectFromText, request: Request) -> ProjectFromTextOut:
    """A project from a written story or script (e.g. the workshop's): the text becomes the premise and the source the
    script stage adapts. With ``start`` the story stages (not the media) are queued; the media are confirmed later in
    the production plan."""
    ctx = ctx_of(request)
    text = body.text.strip()
    if body.asset_id:
        asset = ctx.ledger.get_asset(body.asset_id)
        if asset.kind not in TEXT_KINDS:
            raise ApiError(422, "invalid", f"asset {asset.id} is {asset.kind}, not a text")
        path = (ctx.settings.home / asset.path).resolve()
        if ctx.settings.home.resolve() not in path.parents or not path.is_file():
            raise ApiError(404, "asset_file_missing", asset.path)
        text = path.read_text("utf-8", errors="replace").strip()
    if not text:
        raise ApiError(422, "invalid", "no text: give asset_id or text")
    premise = " ".join(text.split())[:400]
    project = ctx.projects.create(body.name, genre=body.genre, description=premise,
                                  content_language=body.content_language, delivery_profile=body.delivery_profile,
                                  kind=body.kind, pipeline=body.pipeline)
    run = None
    if body.start:
        run = ctx.workflow.enqueue(project.id, ctx.workflow.pending_stages(project.id, None),
                                   {"source_text": text[:MAX_SOURCE_CHARS], "premise": premise})
    return ProjectFromTextOut(project=ProjectOut.model_validate(project),
                              run=WorkflowRunOut.model_validate(run) if run else None)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, request: Request) -> ProjectOut:
    return ProjectOut.model_validate(ctx_of(request).projects.get(project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(project_id: int, body: ProjectPatch, request: Request) -> ProjectOut:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    return ProjectOut.model_validate(ctx_of(request).projects.update(project_id, **changes))


@router.patch("/{project_id}/settings", response_model=ProjectOut)
def patch_project_settings(project_id: int, body: ProjectSettingsPatch, request: Request) -> ProjectOut:
    """Per-project overrides: ``compliance`` (on/off, labels) and ``models.<ref>`` parameter overrides."""
    patch = body.model_dump(exclude_unset=True)
    return ProjectOut.model_validate(ctx_of(request).projects.set_settings(project_id, patch))


@router.patch("/{project_id}/brief", response_model=ProjectOut)
def patch_project_brief(project_id: int, body: BriefAnswers, request: Request) -> ProjectOut:
    """Change the plan made up front: more seasons/episodes, another episode length (seasons are only added to)."""
    return ProjectOut.model_validate(
        ctx_of(request).projects.set_brief(project_id, body.model_dump(exclude_unset=True, exclude_none=True)))


@router.delete("/{project_id}", response_model=ProjectOut)
def trash_project(project_id: int, request: Request) -> ProjectOut:
    """Move to the recycle bin (soft delete). Files and history are kept; ``restore`` brings it back."""
    return ProjectOut.model_validate(ctx_of(request).projects.trash(project_id))


@router.post("/{project_id}/restore", response_model=ProjectOut)
def restore_project(project_id: int, request: Request) -> ProjectOut:
    return ProjectOut.model_validate(ctx_of(request).projects.restore(project_id))
