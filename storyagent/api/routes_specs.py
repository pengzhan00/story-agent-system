from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from storyagent.media.specs import SpecRecord, SpecStore

from .context import ApiContext, ctx_of
from .errors import ApiError
from .routes_jobs import project_settings
from .schemas import (
    JobOut,
    RegenerateRequest,
    ReviewItemOut,
    SpecCreate,
    SpecDiffItem,
    SpecEdit,
    SpecLockRequest,
    SpecOut,
    SpecRevert,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["specs"])
Slot = Annotated[str, Query(description="variant of the same subject, e.g. 'main'")]


def spec_out(ctx: ApiContext, rec: SpecRecord) -> SpecOut:
    out = SpecOut.model_validate(rec)
    out.locked = ctx.specs.is_locked(rec.project_id, rec.subject_type, rec.subject_id, rec.slot)
    return out


def require_project(ctx: ApiContext, project_id: int) -> None:
    ctx.projects.get(project_id)


def current_or_404(ctx: ApiContext, project_id: int, subject_type: str, subject_id: str, slot: str) -> SpecRecord:
    rec = ctx.specs.current(project_id, subject_type, subject_id, slot)
    if rec is None:
        raise ApiError(404, "not_found", f"no spec {subject_type}/{subject_id}/{slot}")
    return rec


@router.get("/specs", response_model=list[SpecOut])
def list_specs(project_id: int, request: Request, subject_type: str | None = None) -> list[SpecOut]:
    ctx = ctx_of(request)
    require_project(ctx, project_id)
    return [spec_out(ctx, r) for r in ctx.specs.list_current(project_id, subject_type)]


@router.post("/specs", response_model=SpecOut, status_code=201)
def create_spec(project_id: int, body: SpecCreate, request: Request) -> SpecOut:
    ctx = ctx_of(request)
    require_project(ctx, project_id)
    rec = ctx.specs.create(
        project_id, body.subject_type, body.subject_id, body.kind, slot=body.slot, model_ref=body.model_ref,
        prompt=body.prompt, negative_prompt=body.negative_prompt, text_payload=body.text_payload,
        params=body.params, seed=body.seed, refs=body.refs, depends_on=body.depends_on, note=body.note,
    )
    return spec_out(ctx, rec)


@router.get("/specs/review", response_model=list[ReviewItemOut])
def needs_review(project_id: int, request: Request) -> list[ReviewItemOut]:
    """Specs whose dependencies (e.g. the character in a shot) changed since they were written."""
    ctx = ctx_of(request)
    require_project(ctx, project_id)
    return [
        ReviewItemOut(spec=spec_out(ctx, item.spec), changed_dependencies=item.changed_dependencies)
        for item in ctx.specs.needs_review(project_id)
    ]


@router.get("/specs/{subject_type}/{subject_id}", response_model=SpecOut)
def get_spec(project_id: int, subject_type: str, subject_id: str, request: Request, slot: Slot = "main") -> SpecOut:
    ctx = ctx_of(request)
    return spec_out(ctx, current_or_404(ctx, project_id, subject_type, subject_id, slot))


@router.get("/specs/{subject_type}/{subject_id}/versions", response_model=list[SpecOut])
def spec_versions(
    project_id: int, subject_type: str, subject_id: str, request: Request, slot: Slot = "main"
) -> list[SpecOut]:
    ctx = ctx_of(request)
    current_or_404(ctx, project_id, subject_type, subject_id, slot)
    return [spec_out(ctx, r) for r in ctx.specs.versions(project_id, subject_type, subject_id, slot)]


@router.patch("/specs/{subject_type}/{subject_id}", response_model=SpecOut)
def edit_spec(
    project_id: int, subject_type: str, subject_id: str, body: SpecEdit, request: Request, slot: Slot = "main"
) -> SpecOut:
    """Creates a new version with only the sent fields changed (no-op when nothing differs)."""
    ctx = ctx_of(request)
    changes = body.model_dump(exclude_unset=True)
    note = changes.pop("note", "")
    patch = changes.pop("params_patch", None)
    rec = ctx.specs.edit(project_id, subject_type, subject_id, slot, note=note, params_patch=patch, **changes)
    return spec_out(ctx, rec)


@router.get("/specs/{subject_type}/{subject_id}/diff", response_model=list[SpecDiffItem])
def spec_diff(
    project_id: int, subject_type: str, subject_id: str, v1: int, v2: int, request: Request, slot: Slot = "main"
) -> list[SpecDiffItem]:
    ctx = ctx_of(request)
    by_version = {v.version: v for v in ctx.specs.versions(project_id, subject_type, subject_id, slot)}
    if v1 not in by_version or v2 not in by_version:
        raise ApiError(404, "not_found", f"versions {v1}/{v2}")
    diff = SpecStore.diff(by_version[v1], by_version[v2])
    return [SpecDiffItem(field=f, before=a, after=b) for f, (a, b) in diff.items()]


@router.post("/specs/{subject_type}/{subject_id}/revert", response_model=SpecOut)
def revert_spec(
    project_id: int, subject_type: str, subject_id: str, body: SpecRevert, request: Request, slot: Slot = "main"
) -> SpecOut:
    """History is never rewritten: reverting creates a new version copying the old content."""
    ctx = ctx_of(request)
    return spec_out(ctx, ctx.specs.revert_to(project_id, subject_type, subject_id, body.version, slot))


@router.put("/specs/{subject_type}/{subject_id}/lock", response_model=SpecOut)
def lock_spec(
    project_id: int, subject_type: str, subject_id: str, request: Request,
    body: SpecLockRequest | None = None, slot: Slot = "main",
) -> SpecOut:
    ctx = ctx_of(request)
    current_or_404(ctx, project_id, subject_type, subject_id, slot)
    ctx.specs.lock(project_id, subject_type, subject_id, slot, body.note if body else "")
    return spec_out(ctx, current_or_404(ctx, project_id, subject_type, subject_id, slot))


@router.delete("/specs/{subject_type}/{subject_id}/lock", response_model=SpecOut)
def unlock_spec(
    project_id: int, subject_type: str, subject_id: str, request: Request, slot: Slot = "main"
) -> SpecOut:
    ctx = ctx_of(request)
    current_or_404(ctx, project_id, subject_type, subject_id, slot)
    ctx.specs.unlock(project_id, subject_type, subject_id, slot)
    return spec_out(ctx, current_or_404(ctx, project_id, subject_type, subject_id, slot))


@router.post("/specs/{subject_type}/{subject_id}/acknowledge", response_model=SpecOut)
def acknowledge_dependencies(
    project_id: int, subject_type: str, subject_id: str, request: Request, slot: Slot = "main"
) -> SpecOut:
    """Mark the changed dependencies as reviewed (clears the 'needs review' flag)."""
    ctx = ctx_of(request)
    rec = current_or_404(ctx, project_id, subject_type, subject_id, slot)
    return spec_out(ctx, ctx.specs.acknowledge_dependencies(rec))


@router.post("/specs/{subject_type}/{subject_id}/regenerate", response_model=list[JobOut], status_code=201)
def regenerate(
    project_id: int, subject_type: str, subject_id: str, body: RegenerateRequest, request: Request,
    slot: Slot = "main",
) -> list[JobOut]:
    """Queue ``n_candidates`` jobs from the current spec version; adopt the winner via /assets/{id}/adopt."""
    ctx = ctx_of(request)
    jobs = ctx.specs.regenerate(
        project_id, subject_type, subject_id, slot, n_candidates=body.n_candidates, vary_seed=body.vary_seed,
        tier=body.tier, shot_id=body.shot_id, priority=body.priority,
        project_settings=project_settings(request, project_id),
    )
    return [JobOut.model_validate(j) for j in jobs]
