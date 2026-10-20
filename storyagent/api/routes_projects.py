from __future__ import annotations

from fastapi import APIRouter, Request

from storyagent.workflow import STAGES

from .context import ctx_of
from .schemas import ProjectCard, ProjectCreate, ProjectOut, ProjectPatch, ProjectSettingsPatch

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
        done = ctx.db.conn.execute(
            "SELECT COUNT(*) AS n FROM workflow_stages WHERE project_id=? AND status='completed'", (p.id,)).fetchone()
        running = ctx.db.conn.execute(
            "SELECT 1 FROM workflow_runs WHERE project_id=? AND status IN ('queued','running')", (p.id,)).fetchone()
        film = ctx.db.conn.execute(
            "SELECT id, slot, duration_sec FROM media_assets WHERE project_id=? AND kind='compose.assemble'"
            " AND slot LIKE 'episode\\_%' ESCAPE '\\' AND is_current=1 AND trashed_at IS NULL"
            " ORDER BY slot DESC LIMIT 1", (p.id,)).fetchone()
        cards.append(ProjectCard(
            id=p.id, name=p.name, description=p.description, genre=p.genre, status=p.status,
            stages_done=int(done["n"]), stages_total=len(STAGES), running=running is not None,
            episode_asset_id=film["id"] if film else None, episode_seconds=film["duration_sec"] if film else None,
            episode_number=int(str(film["slot"]).split("_")[1]) if film else None, updated_at=p.updated_at))
    return cards


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, request: Request) -> ProjectOut:
    p = ctx_of(request).projects.create(
        body.name, genre=body.genre, description=body.description, content_language=body.content_language
    )
    return ProjectOut.model_validate(p)


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


@router.delete("/{project_id}", response_model=ProjectOut)
def trash_project(project_id: int, request: Request) -> ProjectOut:
    """Move to the recycle bin (soft delete). Files and history are kept; ``restore`` brings it back."""
    return ProjectOut.model_validate(ctx_of(request).projects.trash(project_id))


@router.post("/{project_id}/restore", response_model=ProjectOut)
def restore_project(project_id: int, request: Request) -> ProjectOut:
    return ProjectOut.model_validate(ctx_of(request).projects.restore(project_id))
