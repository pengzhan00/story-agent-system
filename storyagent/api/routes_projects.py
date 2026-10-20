from __future__ import annotations

from fastapi import APIRouter, Request

from .context import ctx_of
from .schemas import ProjectCreate, ProjectOut, ProjectPatch, ProjectSettingsPatch

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(request: Request) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in ctx_of(request).projects.list()]


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
