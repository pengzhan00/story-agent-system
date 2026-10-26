"""Maintenance buttons: prune orphaned shot specs/assets and old history logs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from storyagent import maintain

from .context import ctx_of

router = APIRouter(prefix="/api/maintain", tags=["maintain"])


@router.post("/prune-orphans")
def prune_orphans(project_id: int, request: Request, dry_run: bool = False) -> dict[str, int]:
    """Drop shot.keyframe/shot.video specs whose shot no longer exists (and their assets). Safe to run."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    return maintain.prune_orphans(ctx.specs, project_id, dry_run=dry_run)


@router.post("/prune-history")
def prune_history(
    request: Request, days: int = Query(30, ge=1, le=3650), project_id: int | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """Delete audit_log / generation_logs / job_events rows older than ``days`` days."""
    ctx = ctx_of(request)
    if project_id is not None:
        ctx.projects.get(project_id)
    return maintain.prune_history(ctx.db, days=days, project_id=project_id, dry_run=dry_run)
