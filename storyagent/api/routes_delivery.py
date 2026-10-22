"""Delivery per platform: the destinations, a delivery queued as a background job, and the finished deliveries."""
from __future__ import annotations

from fastapi import APIRouter, Request

from storyagent.compose.export import ExportError, find_episode
from storyagent.domain import MediaKind, QualityTier
from storyagent.media.contracts import MediaJob

from .context import ctx_of
from .errors import ApiError
from .schemas import AssetOut, DeliveryBody, DeliveryJobOut, DeliveryOut, PlatformOut
from .visibility import allow_export

router = APIRouter(prefix="/api", tags=["delivery"])


@router.get("/platforms", response_model=list[PlatformOut])
def platforms(request: Request) -> list[PlatformOut]:
    cfg = ctx_of(request).settings.platforms
    return [PlatformOut(**p.as_dict(), default=name == cfg.default) for name, p in cfg.items.items()]


@router.post("/projects/{project_id}/episodes/{episode}/delivery", response_model=DeliveryJobOut, status_code=202)
def make_delivery(project_id: int, episode: int, body: DeliveryBody, request: Request) -> DeliveryJobOut:
    """Queue the re-encode of an assembled episode for a platform (a ``compose.deliver`` job: progress, cancel and retry
    like any job; a translation asks the language model). The result is a zip asset listed by
    ``GET /api/projects/{id}/deliveries``."""
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    platform = body.platform or ctx.settings.platforms.default
    if platform not in ctx.settings.platforms.items:
        raise ApiError(404, "not_found", platform)
    confirmed = allow_export(request, project_id, body.confirm_restricted, what=f"delivery ep{episode}")
    try:
        find_episode(ctx.ledger, project_id, episode)
    except ExportError as exc:
        raise ApiError(409, "episode_not_ready", str(exc)) from exc
    inputs = {"episode": episode, "platform": platform, "languages": sorted(set(body.languages)),
              "content_language": project.content_language, "confirm_restricted": confirmed}
    rec, created = ctx.ledger.enqueue(
        MediaJob(kind=MediaKind.COMPOSE_DELIVER, inputs=inputs, project_id=project_id, tier=QualityTier.FINAL,
                 slot=f"{platform}_ep{episode:02d}"), project.settings)
    return DeliveryJobOut(job_id=rec.id, created=created)


@router.get("/projects/{project_id}/deliveries", response_model=list[DeliveryOut])
def deliveries(project_id: int, request: Request, limit: int = 50) -> list[DeliveryOut]:
    """Finished deliveries, newest first. Download one with ``GET /api/assets/{asset.id}/file``."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    out: list[DeliveryOut] = []
    for a in ctx.ledger.list_assets(project_id=project_id, kind=MediaKind.COMPOSE_DELIVER.value, newest_first=True,
                                    limit=min(max(limit, 1), 200)):
        asset = AssetOut.model_validate(a)
        rec = asset.metadata                                     # blank while the project is locked
        slot_platform, _, slot_ep = a.slot.rpartition("_ep")
        out.append(DeliveryOut(
            asset=asset, platform=slot_platform, episode=int(slot_ep) if slot_ep.isdigit() else 0,
            warnings=list(rec.get("warnings", [])), subtitles=list(rec.get("subtitles", [])),
            result=dict(rec.get("result", {}))))
    return out
