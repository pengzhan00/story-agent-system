"""Edit interchange with Final Cut Pro: the project as FCPXML + media, what an edit changed, the finished film back."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from storyagent.compose.export import ExportError
from storyagent.compose.interchange import build_fcp_package, diff_edit, episode_manifest, import_final, parse_fcpxml

from .context import ctx_of
from .errors import ApiError
from .routes_library import _receive
from .schemas import AssetOut, EditDiffOut

router = APIRouter(prefix="/api/projects/{project_id}/episodes/{episode}/fcp", tags=["interchange"])


@router.get("")
def export_fcp(project_id: int, episode: int, request: Request, absolute: bool = False) -> FileResponse:
    """A zip with `episode.fcpxml`, the media it points to (relative paths unless `absolute`), the subtitles and
    `interchange.json`. Import the .fcpxml in Final Cut Pro (File > Import > XML) from the unzipped folder."""
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    if absolute:
        raise ApiError(422, "invalid", "absolute media paths name this machine's folders: use the CLI for them")
    work = Path(tempfile.mkdtemp(prefix="fcp-", dir=ctx.settings.home))
    try:
        build_fcp_package(ctx.settings, ctx.ledger, project_id, episode, work / "fcp")
    except ExportError as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise ApiError(409, "episode_not_ready", str(exc)) from exc
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    archive = shutil.make_archive(str(work / f"{project.slug}-ep{episode:02d}-fcp"), "zip", work / "fcp")
    return FileResponse(archive, media_type="application/zip", filename=Path(archive).name,
                        background=BackgroundTask(shutil.rmtree, work, ignore_errors=True))


@router.post("/diff", response_model=EditDiffOut)
async def diff(project_id: int, episode: int, request: Request,
               filename: Annotated[str, Query(min_length=1, max_length=200)] = "episode.fcpxml") -> EditDiffOut:
    """Upload the FCPXML exported back from Final Cut Pro (raw body): per shot kept / trimmed / moved / removed,
    clips that came from elsewhere, and the new length. Changes nothing."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    tmp = await _receive(request, ctx, filename)
    try:
        edited = parse_fcpxml(tmp.read_text("utf-8", errors="replace"))
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    try:
        manifest = episode_manifest(ctx.settings, ctx.ledger, project_id, episode)
    except ExportError as exc:
        raise ApiError(409, "episode_not_ready", str(exc)) from exc
    return EditDiffOut(**diff_edit(manifest, edited))


@router.post("/final", response_model=AssetOut, status_code=201)
async def final(project_id: int, episode: int, request: Request,
                filename: Annotated[str, Query(min_length=1, max_length=200)], note: str = "") -> AssetOut:
    """Upload the film finished in the editor (raw body, .mp4/.mov): it becomes the current version of the episode."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    tmp = await _receive(request, ctx, filename)
    try:
        return AssetOut.model_validate(import_final(ctx.settings, ctx.ledger, project_id, episode, tmp, note=note))
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
