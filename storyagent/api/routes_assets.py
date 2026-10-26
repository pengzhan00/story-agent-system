from __future__ import annotations

import hashlib
import mimetypes
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from storyagent.content import mask_thumbnail
from storyagent.media.ledger import NewAsset
from storyagent.util import home_file, new_id

from .context import ctx_of
from .errors import ApiError
from .schemas import AssetOut, StaleAsset, TextEdit
from .visibility import require_viewable

router = APIRouter(prefix="/api", tags=["assets"])


@router.get("/assets", response_model=list[AssetOut])
def list_assets(
    request: Request,
    project_id: int | None = None,
    shot_id: int | None = None,
    kind: str | None = None,
    slot: str | None = None,
    spec_id: str | None = None,
    current_only: bool = False,
    include_trashed: bool = False,
    kinds: Annotated[str | None, Query(description="comma-separated kinds, e.g. text.story,text.script")] = None,
    standalone: Annotated[bool, Query(description="only assets that belong to no project (workshop results)")] = False,
    newest_first: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[AssetOut]:
    rows = ctx_of(request).ledger.list_assets(
        project_id=project_id, shot_id=shot_id, kind=kind, slot=slot, spec_id=spec_id,
        current_only=current_only, include_trashed=include_trashed, limit=limit, offset=offset,
        kinds=[k for k in (kinds or "").split(",") if k], standalone=standalone, newest_first=newest_first,
    )
    return [AssetOut.model_validate(a) for a in rows]


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: str, request: Request) -> AssetOut:
    return AssetOut.model_validate(ctx_of(request).ledger.get_asset(asset_id))


@router.get("/assets/{asset_id}/file", response_class=FileResponse)
def asset_file(asset_id: str, request: Request) -> FileResponse:
    """Stream the stored file (range requests supported, so <video>/<audio> can seek)."""
    ctx = ctx_of(request)
    asset = ctx.ledger.get_asset(asset_id)
    require_viewable(request, asset.rating, asset.project_id, what=asset.id)
    path = home_file(ctx.settings.home, asset.path)
    if path is None:  # a ledger path must never escape the data home
        raise ApiError(404, "not_found", "asset path outside the data home")
    if not path.is_file():
        raise ApiError(404, "asset_file_missing", asset.path)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


@router.get("/assets/{asset_id}/mask", response_class=FileResponse)
def asset_mask(asset_id: str, request: Request) -> FileResponse:
    """A tiny blurred picture of a restricted image or video, shown instead of the file while it is locked."""
    ctx = ctx_of(request)
    asset = ctx.ledger.get_asset(asset_id)
    if not ctx.settings.content.policy(asset.rating).mask_ui:
        raise ApiError(404, "not_found", asset_id)
    cache = ctx.settings.home / "cache" / "mask" / f"{asset.id}.jpg"
    if not mask_thumbnail(ctx.settings, ctx.settings.home / asset.path, cache):
        raise ApiError(404, "not_found", asset_id)
    return FileResponse(cache, media_type="image/jpeg", content_disposition_type="inline")


@router.put("/assets/{asset_id}/text", response_model=AssetOut)
def edit_text_asset(asset_id: str, body: TextEdit, request: Request) -> AssetOut:
    """Save an edited story / script / transcript as a NEW version of the same slot (the one before stays as a
    candidate, so the edit can be undone by adopting it again)."""
    ctx = ctx_of(request)
    old = ctx.ledger.get_asset(asset_id)
    if not (old.kind.startswith("text.") or old.kind == "upload.text"):
        raise ApiError(422, "not_text_asset", old.kind)
    require_viewable(request, old.rating, old.project_id, what=old.id)
    src = home_file(ctx.settings.home, old.path)
    if src is None:
        raise ApiError(404, "not_found", "asset path outside the data home")
    dest = src.with_name(f"{new_id()}{src.suffix or '.md'}")
    data = body.text.encode("utf-8")
    dest.write_bytes(data)
    user = request.scope.get("state", {}).get("user")
    new = ctx.ledger.register_asset(
        NewAsset(
            kind=old.kind, slot=old.slot, path=str(dest.relative_to(ctx.settings.home)),
            sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), metadata={**old.metadata, "edited_from": old.id},
            provenance={**old.provenance, "edited_by": str(user.username) if user else "local", "edited_from": old.id},
            rating=old.rating),
        project_id=old.project_id, shot_id=old.shot_id)
    return AssetOut.model_validate(new)


@router.post("/assets/{asset_id}/adopt", response_model=AssetOut)
def adopt_asset(asset_id: str, request: Request) -> AssetOut:
    """Make this candidate the current asset of its slot (and resolve the review notes that asked for this redo)."""
    ctx = ctx_of(request)
    asset = ctx.ledger.adopt(asset_id)
    user = request.scope.get("state", {}).get("user")
    ctx.review.adopted(asset, str(user.username) if user else "local")
    return AssetOut.model_validate(asset)


@router.post("/assets/{asset_id}/trash", response_model=AssetOut)
def trash_asset(asset_id: str, request: Request) -> AssetOut:
    """Soft delete: the file stays on disk, the record leaves its slot."""
    ledger = ctx_of(request).ledger
    ledger.get_asset(asset_id)
    return AssetOut.model_validate(ledger.trash_asset(asset_id))


@router.post("/assets/{asset_id}/purge", response_model=AssetOut)
def purge_asset(asset_id: str, request: Request) -> AssetOut:
    """Hard delete: remove the row and its file from disk. Refused while the asset is current."""
    ledger = ctx_of(request).ledger
    ledger.get_asset(asset_id)
    return AssetOut.model_validate(ledger.purge_asset(asset_id))


@router.post("/assets/purge-project")
def purge_project_assets(request: Request, project_id: int, include_current: bool = False) -> dict[str, int]:
    """Hard-delete a project's assets. Trashed ones by default; ``include_current`` also takes the current assets
    (they are soft-deleted first, then hard-deleted). Published library assets are left alone."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    where = "project_id=?" if include_current else "project_id=? AND trashed_at IS NOT NULL"
    rows = ctx.ledger.db.conn.execute(
        f"SELECT id, is_current FROM media_assets WHERE {where}", (project_id,)).fetchall()
    purged = skipped = 0
    for r in rows:
        try:
            if r["is_current"]:
                ctx.ledger.trash_asset(r["id"])
            ctx.ledger.purge_asset(r["id"])
            purged += 1
        except Exception:  # noqa: BLE001 - published/current guards: count and keep going
            skipped += 1
    return {"purged": purged, "skipped": skipped}


@router.post("/assets/{asset_id}/purge-version")
def purge_asset_version(asset_id: str, request: Request) -> dict[str, int]:
    """Hard-delete every asset of the same spec version as this asset. Refused while any is current."""
    ledger = ctx_of(request).ledger
    a = ledger.get_asset(asset_id)
    if a.spec_id is None or a.spec_version is None:
        return {"purged": 0}
    return {"purged": ledger.purge_version(a.spec_id, a.spec_version)}


@router.get("/projects/{project_id}/stale", response_model=list[StaleAsset], tags=["specs"])
def stale_assets(project_id: int, request: Request) -> list[StaleAsset]:
    """Current assets that were generated from an older spec version than the latest."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    return [
        StaleAsset(asset=AssetOut.model_validate(a), latest_spec_version=v)
        for a, v in ctx.specs.stale_assets(project_id)
    ]
