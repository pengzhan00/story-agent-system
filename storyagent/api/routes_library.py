from __future__ import annotations

import mimetypes
import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from storyagent.media.library import ROLES, LibraryItem, import_file

from .context import ApiContext, ctx_of
from .errors import ApiError
from .schemas import AssetOut, LibraryItemOut, LibraryPatch, LibraryPublish
from .visibility import require_viewable

router = APIRouter(prefix="/api", tags=["library"])


def _out(item: LibraryItem) -> LibraryItemOut:
    assert item.asset is not None
    return LibraryItemOut(
        id=item.id, role=item.role, name=item.name, ref=item.ref, description=item.description, tags=item.tags,
        meta=item.meta, asset=AssetOut.model_validate(item.asset), source_asset_id=item.source_asset_id,
        source_project_id=item.source_project_id, created_at=item.created_at,
    )


async def _receive(request: Request, ctx: ApiContext, filename: str) -> Path:
    """Stream the request body into a temp file under the data home (bounded by ``[api] max_upload_mb``)."""
    limit = int(ctx.settings.api["max_upload_mb"]) * 1024 * 1024
    tmp_dir = ctx.settings.home / "work" / "uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    name = Path(filename).name or "upload.bin"
    tmp = Path(tempfile.mkdtemp(dir=tmp_dir)) / name
    size = 0
    with tmp.open("wb") as fh:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                fh.close()
                shutil.rmtree(tmp.parent, ignore_errors=True)
                raise ApiError(413, "too_large", f"limit {limit} bytes")
            fh.write(chunk)
    if size == 0:
        shutil.rmtree(tmp.parent, ignore_errors=True)
        raise ApiError(422, "invalid", "empty upload")
    return tmp


@router.post("/projects/{project_id}/uploads", response_model=AssetOut, status_code=201, tags=["assets"])
async def upload_asset(
    project_id: int, request: Request, filename: Annotated[str, Query(min_length=1, max_length=200)],
    slot: str = "main", shot_id: int | None = None,
) -> AssetOut:
    """Raw-body upload (``Content-Type`` is ignored; the kind comes from the file extension).
    Returns an ``upload.<audio|image|video|model|file>`` asset that jobs can use as ``asset:<id>``."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    tmp = await _receive(request, ctx, filename)
    try:
        return AssetOut.model_validate(
            import_file(ctx.settings, ctx.ledger, tmp, project_id=project_id, slot=slot, shot_id=shot_id)
        )
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


@router.get("/library/roles", response_model=list[str])
def library_roles() -> list[str]:
    """The names an asset can be filed under (``library:<role>/<name>``)."""
    return list(ROLES)


@router.get("/library", response_model=list[LibraryItemOut])
def list_library(
    request: Request, role: str | None = None, tag: str | None = None, q: str | None = None
) -> list[LibraryItemOut]:
    return [_out(i) for i in ctx_of(request).library.list(role=role, tag=tag, q=q)]


@router.post("/library", response_model=LibraryItemOut, status_code=201)
def publish(body: LibraryPublish, request: Request) -> LibraryItemOut:
    """Copy an asset into the cross-project library under ``role/name``."""
    item = ctx_of(request).library.publish(
        body.asset_id, body.role, body.name, description=body.description, tags=body.tags, meta=body.meta,
        replace=body.replace,
    )
    return _out(item)


@router.post("/library/upload", response_model=LibraryItemOut, status_code=201)
async def upload_to_library(
    request: Request, filename: Annotated[str, Query(min_length=1, max_length=200)], role: str,
    name: Annotated[str, Query(min_length=1, max_length=80)], description: str = "", replace: bool = False,
    consent: bool = False, text: str = "",
) -> LibraryItemOut:
    """Raw-body upload straight into the library. For ``role=voice`` pass ``text`` (what the clip says) and
    ``consent=true`` (the speaker agreed to cloning) so jobs can use it as a voice reference."""
    ctx = ctx_of(request)
    tmp = await _receive(request, ctx, filename)
    try:
        meta: dict[str, object] = {}
        if role == "voice":
            meta = {"text": text, "consent": consent}
        return _out(ctx.library.add_file(tmp, role, name, description=description, meta=meta, replace=replace))
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


@router.get("/library/{item_id}", response_model=LibraryItemOut)
def get_item(item_id: str, request: Request) -> LibraryItemOut:
    return _out(ctx_of(request).library.get(item_id))


@router.patch("/library/{item_id}", response_model=LibraryItemOut)
def patch_item(item_id: str, body: LibraryPatch, request: Request) -> LibraryItemOut:
    return _out(ctx_of(request).library.update(item_id, description=body.description, tags=body.tags, meta=body.meta))


@router.delete("/library/{item_id}", response_model=LibraryItemOut)
def remove_item(item_id: str, request: Request) -> LibraryItemOut:
    """Soft delete: the name is free again; the file stays until a purge."""
    return _out(ctx_of(request).library.remove(item_id))


@router.get("/library/{item_id}/file", response_class=FileResponse)
def item_file(item_id: str, request: Request) -> FileResponse:
    ctx = ctx_of(request)
    item = ctx.library.get(item_id)
    assert item.asset is not None
    require_viewable(request, item.asset.rating, None, what=item.asset.id)
    home = ctx.settings.home.resolve()
    path = (home / item.asset.path).resolve()
    if home not in path.parents or not path.is_file():
        raise ApiError(404, "asset_file_missing", item.asset.path)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        filename=path.name, content_disposition_type="inline")
