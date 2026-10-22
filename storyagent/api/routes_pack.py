"""Asset packs over HTTP: build a pack of a project and download it; upload a pack zip into a project."""
from __future__ import annotations

import re
import shutil
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from storyagent.media.pack import export_pack, import_pack
from storyagent.util import now_iso

from .context import ctx_of
from .errors import ApiError
from .routes_library import _receive
from .schemas import PackExportOut, PackReportOut
from .visibility import allow_export

router = APIRouter(prefix="/api", tags=["packs"])
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.zip$")


@router.post("/projects/{project_id}/pack", response_model=PackExportOut, status_code=201)
def export(
    project_id: int, request: Request, scope: Literal["all", "assets", "story"] = "all", include_history: bool = False,
    confirm_restricted: bool = False,
) -> PackExportOut:
    """Write the project to ``<home>/exports/<slug>-<time>.zip``; download it with ``GET /api/packs/{name}``."""
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    confirmed = allow_export(request, project_id, confirm_restricted, what="pack")
    out_dir = ctx.settings.home / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{project.slug}-{now_iso().replace(':', '').replace('.', '')}.zip"
    manifest = export_pack(ctx.settings, ctx.db, project_id, out_dir / name, scope=scope,
                           include_history=include_history, confirm_restricted=confirmed)
    return PackExportOut(name=name, bytes=(out_dir / name).stat().st_size, scope=scope, counts=manifest["counts"],
                         warnings=manifest["warnings"])


@router.get("/packs/{name}")
def download(name: str, request: Request) -> FileResponse:
    if not _NAME.match(name):
        raise ApiError(404, "not_found", name)
    path = ctx_of(request).settings.home / "exports" / name
    if not path.is_file():
        raise ApiError(404, "not_found", name)
    return FileResponse(path, media_type="application/zip", filename=name)


@router.post("/projects/import-pack", response_model=PackReportOut)
async def import_zip(
    request: Request, filename: Annotated[str, Query(min_length=1, max_length=200)], into: int | None = None,
    on_conflict: Literal["skip", "new_version"] = "skip", only: Literal["all", "assets", "story"] = "all",
    dry_run: bool = False,
) -> PackReportOut:
    """Raw-body upload of a pack ``.zip``. ``dry_run=true`` reports what would change and writes nothing; without
    ``into`` a new project is made from the pack's manifest (needs the create-project right)."""
    ctx = ctx_of(request)
    if into is not None:
        ctx.projects.get(into)
    tmp = await _receive(request, ctx, filename if filename.lower().endswith(".zip") else f"{filename}.zip")
    try:
        report = import_pack(ctx.settings, ctx.db, tmp, into=into, policy=on_conflict, only=only, dry_run=dry_run)
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    return PackReportOut(**report.as_dict())
