"""The pipeline of a project: the plan's recommended models, the ones changed by hand, and the way back to the
defaults."""
from __future__ import annotations

from fastapi import APIRouter, Request

from storyagent.pipelines import SETTINGS_KEY, describe

from .context import ctx_of
from .errors import ApiError
from .schemas import PipelineChoices, PipelineView

router = APIRouter(prefix="/api/projects/{project_id}/pipeline", tags=["pipeline"])


def _view(request: Request, project_id: int) -> PipelineView:
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    if not project.pipeline:
        raise ApiError(409, "no_pipeline")
    return PipelineView(**describe(ctx.settings, project.pipeline, project.settings.get(SETTINGS_KEY), project.rating))


@router.get("", response_model=PipelineView)
def get_pipeline(project_id: int, request: Request) -> PipelineView:
    return _view(request, project_id)


@router.put("", response_model=PipelineView)
def set_pipeline(project_id: int, body: PipelineChoices, request: Request) -> PipelineView:
    """Replace the hand-picked models (kind -> model). Models of a kind other than the plan's are refused; a changed
    picture / video model pairs its edit / audio-video companion again (see the notes in the answer)."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    ctx.projects.set_settings(project_id, {SETTINGS_KEY: dict(body.models) or None})
    return _view(request, project_id)


@router.delete("", response_model=PipelineView)
def reset_pipeline(project_id: int, request: Request) -> PipelineView:
    """Back to the plan's recommended models, in one go."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    ctx.projects.set_settings(project_id, {SETTINGS_KEY: None})
    return _view(request, project_id)
