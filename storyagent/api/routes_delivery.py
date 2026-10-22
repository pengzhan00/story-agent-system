"""Delivery per platform: the destinations, and an episode re-encoded for one (film, cover, teaser, subtitles)."""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from storyagent.compose.delivery import deliver
from storyagent.llm import LLMError
from storyagent.util import now_iso
from storyagent.workflow import WorkflowEngine

from .context import ctx_of
from .errors import ApiError
from .schemas import DeliveryBody, DeliveryOut, PlatformOut
from .visibility import allow_export

router = APIRouter(prefix="/api", tags=["delivery"])
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.zip$")


@router.get("/platforms", response_model=list[PlatformOut])
def platforms(request: Request) -> list[PlatformOut]:
    cfg = ctx_of(request).settings.platforms
    return [PlatformOut(**p.as_dict(), default=name == cfg.default) for name, p in cfg.items.items()]


@router.post("/projects/{project_id}/episodes/{episode}/delivery", response_model=DeliveryOut, status_code=201)
def make_delivery(project_id: int, episode: int, body: DeliveryBody, request: Request) -> DeliveryOut:
    """Re-encode the assembled episode for a platform (can take minutes; a translation asks the language model).
    Download the result with ``GET /api/deliveries/{name}``."""
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    confirmed = allow_export(request, project_id, body.confirm_restricted, what=f"delivery ep{episode}")
    if body.platform and body.platform not in ctx.settings.platforms.items:
        raise ApiError(404, "not_found", body.platform)
    engine = WorkflowEngine(ctx.settings, ctx.db, spec_store=ctx.specs)
    engine.router.heavy_wait_sec = 0
    out_dir = ctx.settings.home / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="delivery-", dir=out_dir))
    try:
        record = deliver(ctx.settings, ctx.ledger, project_id, episode, body.platform or None, work,
                         languages=body.languages, content_language=project.content_language,
                         confirm_restricted=confirmed,
                         translate=lambda lines, lang: engine.translate_lines(project_id, lines, lang))
        stamp = now_iso().replace(":", "").replace(".", "")
        name = f"{project.slug}-ep{episode:02d}-{record['platform']['name']}-{stamp}.zip"
        shutil.make_archive(str(out_dir / name[:-4]), "zip", work / record["platform"]["name"])
    except LLMError as exc:
        raise ApiError(502, "llm_failed", f"{exc.code}: {exc.message}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return DeliveryOut(name=name, bytes=(out_dir / name).stat().st_size, platform=record["platform"]["name"],
                       warnings=record["warnings"], subtitles=record["subtitles"], result=record["result"],
                       files=record["files"])


@router.get("/deliveries/{name}")
def download(name: str, request: Request) -> FileResponse:
    if not _NAME.match(name):
        raise ApiError(404, "not_found", name)
    path = ctx_of(request).settings.home / "exports" / name
    if not path.is_file():
        raise ApiError(404, "not_found", name)
    return FileResponse(path, media_type="application/zip", filename=name)
