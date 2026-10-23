from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from storyagent.wiki import Page, WikiStore

from .context import ctx_of
from .errors import ApiError
from .schemas import WikiBrief, WikiCount, WikiList, WikiPageOut, WikiSyncOut
from .visibility import hidden, require_viewable

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


def _brief(page: Page, snippet: str = "") -> WikiBrief:
    return WikiBrief(slug=page.slug, title=page.title, source=page.source, path=page.path, rating=page.rating,
                     tags=page.tags, summary=page.summary, snippet=snippet, updated=page.source_mtime)


def _store(request: Request) -> WikiStore:
    ctx = ctx_of(request)
    return WikiStore(ctx.db, ctx.settings)


def _visible(page: Page) -> bool:
    """A restricted note is hidden like a restricted picture: while locked, and for a caller below the age."""
    return page.rating == "general" or not hidden(page.rating, None)


@router.get("/pages", response_model=WikiList)
def list_pages(
    request: Request,
    q: Annotated[str, Query(max_length=200)] = "",
    source: str = "",
    tag: str = "",
) -> WikiList:
    store = _store(request)
    everything = store.all()
    visible = [p for p in everything if _visible(p)]
    pool = [p for p in visible if (not source or p.source == source) and (not tag or tag in p.tags)]
    if q.strip():
        rows = [_brief(p, snip) for p, snip, _score in store.search(q, pool)]
    else:
        rows = [_brief(p) for p in pool]
    sources: dict[str, int] = {}
    tags: dict[str, int] = {}
    for p in visible:
        sources[p.source] = sources.get(p.source, 0) + 1
        for t in p.tags:
            tags[t] = tags.get(t, 0) + 1
    return WikiList(
        pages=rows, total=len(visible), hidden=len(everything) - len(visible),
        sources=[WikiCount(name=k, count=v) for k, v in sorted(sources.items())],
        tags=[WikiCount(name=k, count=v) for k, v in sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))],
        synced_at=store.last_sync(), configured=bool(ctx_of(request).settings.wiki.sources))


@router.get("/pages/{slug:path}", response_model=WikiPageOut)
def get_page(slug: str, request: Request) -> WikiPageOut:
    store = _store(request)
    page = store.get(slug)
    if page is None:
        raise ApiError(404, "not_found", slug)
    if page.rating != "general":
        require_viewable(request, page.rating, None, what=f"wiki:{slug}")
    return WikiPageOut(
        page=_brief(page), body=page.body, meta=page.meta,
        backlinks=[_brief(p) for p in store.backlinks(slug) if _visible(p)],
        links=[_brief(p) for p in store.outgoing(slug) if _visible(p)])


@router.post("/sync", response_model=WikiSyncOut)
def sync(request: Request) -> WikiSyncOut:
    """Read the sources again (only the notes the include lists allow) and bring the pages up to date."""
    return WikiSyncOut(**_store(request).sync().as_dict())


@router.get("/assets/{name}")
def asset(name: str, request: Request) -> FileResponse:
    root = _store(request).assets_dir.resolve()
    path = (root / Path(name).name).resolve()
    if not path.is_file() or root not in path.parents:
        raise ApiError(404, "not_found", name)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")
