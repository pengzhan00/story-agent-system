"""The review loop: the board of shots with their takes, notes on a spec/take, redo from a note, compare, resolve."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from storyagent.review import Note

from .context import ApiContext, ctx_of
from .routes_jobs import project_settings
from .schemas import (
    AssetOut,
    JobOut,
    NoteCompareOut,
    NoteCreate,
    NoteOut,
    NoteRedoBody,
    NoteRedoOut,
    NoteStatusBody,
    ReviewShotOut,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["review"])


def actor_of(request: Request) -> str:
    """Who is writing: the signed-in user, or ``local`` when sign-in is off."""
    user = request.scope.get("state", {}).get("user")
    return str(user.username) if user else "local"


def _out(note: Note) -> NoteOut:
    return NoteOut.model_validate(note, from_attributes=True)


def _note(ctx: ApiContext, project_id: int, note_id: int) -> Note:
    return ctx.review.get(note_id, project_id)


@router.get("/review/board", response_model=list[ReviewShotOut])
def board(project_id: int, request: Request) -> list[ReviewShotOut]:
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    return [ReviewShotOut(**{**row, "assets": [AssetOut.model_validate(a) for a in row["assets"]]})
            for row in ctx.review.board(project_id)]


@router.get("/notes", response_model=list[NoteOut])
def list_notes(
    project_id: int, request: Request, status: str = "", subject_type: str = "", subject_id: str = "",
    shot_id: int | None = None, limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> list[NoteOut]:
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    return [_out(n) for n in ctx.review.notes(project_id, status=status, subject_type=subject_type,
                                              subject_id=subject_id, shot_id=shot_id, limit=limit)]


@router.post("/notes", response_model=NoteOut, status_code=201)
def add_note(project_id: int, body: NoteCreate, request: Request) -> NoteOut:
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    return _out(ctx.review.add(
        project_id, body.subject_type, body.subject_id, body.body, actor_of(request), slot=body.slot,
        shot_id=body.shot_id, asset_id=body.asset_id))


@router.patch("/notes/{note_id}", response_model=NoteOut)
def set_status(project_id: int, note_id: int, body: NoteStatusBody, request: Request) -> NoteOut:
    ctx = ctx_of(request)
    return _out(ctx.review.set_status(note_id, body.status, actor_of(request), body.resolution, project_id))


@router.post("/notes/{note_id}/redo", response_model=NoteRedoOut, status_code=201)
def redo(project_id: int, note_id: int, body: NoteRedoBody, request: Request) -> NoteRedoOut:
    """Apply the requested changes to the spec (a new version that quotes the note) and queue new takes."""
    ctx = ctx_of(request)
    changes = body.model_dump(exclude={"n_candidates", "tier"}, exclude_none=True)
    note, jobs = ctx.review.redo(
        note_id, actor_of(request), changes=changes, n_candidates=body.n_candidates, tier=body.tier,
        project_settings=project_settings(request, project_id), project_id=project_id)
    return NoteRedoOut(note=_out(note), jobs=[JobOut.model_validate(j) for j in jobs])


@router.get("/notes/{note_id}/compare", response_model=NoteCompareOut)
def compare(project_id: int, note_id: int, request: Request) -> NoteCompareOut:
    """The take the note was about next to the takes the redo(s) made (and the jobs still running)."""
    ctx = ctx_of(request)
    note = _note(ctx, project_id, note_id)
    before, after, jobs = ctx.review.compare(note_id, project_id)
    return NoteCompareOut(
        note=_out(note), before=AssetOut.model_validate(before) if before else None,
        after=[AssetOut.model_validate(a) for a in after], jobs=[JobOut.model_validate(j) for j in jobs])
