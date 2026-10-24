from __future__ import annotations

import mimetypes
import re
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from storyagent.agents.runner import AgentRunner, language_name
from storyagent.llm import LLMError, LLMRouter
from storyagent.wiki import FocusNotFound, Page, WikiStore, ask, build_graph
from storyagent.wiki.graph import DEFAULT_LIMIT, MAX_LIMIT

from .context import ctx_of
from .errors import ApiError
from .routes_llm import _llm_error
from .schemas import (
    WikiAskIn,
    WikiAskOut,
    WikiAskSource,
    WikiBrief,
    WikiCount,
    WikiGraphEdge,
    WikiGraphNode,
    WikiGraphOut,
    WikiList,
    WikiPageOut,
    WikiSyncOut,
)
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


@router.get("/graph", response_model=WikiGraphOut)
def graph(
    request: Request,
    q: Annotated[str, Query(max_length=200)] = "",
    source: str = "",
    tag: str = "",
    focus: Annotated[str, Query(max_length=500, description="a page slug, or model:<family>")] = "",
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
    include_tags: bool = False,
    shared_tags: bool = False,
    include_models: bool = False,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> WikiGraphOut:
    """The pages and the links between them. A restricted page the caller may not see is not a node, not the end of an
    edge and not in any count (``hidden`` says how many there are, as the page list does)."""
    store = _store(request)
    everything = store.all()
    visible = [p for p in everything if _visible(p)]
    if focus and not focus.startswith("model:"):
        page = store.get(focus)
        if page is None:
            raise ApiError(404, "not_found", focus)
        if page.rating != "general":
            require_viewable(request, page.rating, None, what=f"wiki:{focus}")
    matches = {p.slug for p, _snip, _score in store.search(q, visible)} if q.strip() else None
    settings = ctx_of(request).settings
    try:
        built = build_graph(
            visible, store.links(), settings.models, source=source, tag=tag, matches=matches, focus=focus, depth=depth,
            include_tags=include_tags, shared_tags=shared_tags, include_models=include_models, limit=limit)
    except FocusNotFound:
        raise ApiError(404, "not_found", focus) from None
    sources: dict[str, int] = {}
    tags: dict[str, int] = {}
    for p in visible:
        sources[p.source] = sources.get(p.source, 0) + 1
        for t in p.tags:
            tags[t] = tags.get(t, 0) + 1
    return WikiGraphOut(
        nodes=[WikiGraphNode(**vars(n)) for n in built.nodes], edges=[WikiGraphEdge(**vars(e)) for e in built.edges],
        total=len(visible), hidden=len(everything) - len(visible), matched=built.matched, truncated=built.truncated,
        limit=limit, focus=built.focus, depth=depth,
        sources=[WikiCount(name=k, count=v) for k, v in sorted(sources.items())],
        tags=[WikiCount(name=k, count=v) for k, v in sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))])


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


@router.post("/ask", response_model=WikiAskOut)
def ask_wiki(body: WikiAskIn, request: Request) -> WikiAskOut:
    """A question answered from the wiki's own pages by the ``knowledge`` language model, with numbered sources.
    Only pages the caller may see are read; a restricted page that was read is audited like opening it, and the
    answer is then made under that rating (a model that never leaves this computer, no text in the call log)."""
    ctx = ctx_of(request)
    pool = [p for p in _store(request).all() if _visible(p) and (not body.source or p.source == body.source)]
    found = ask.passages(pool, ask.terms_of(body.question), limit=body.limit)
    if not found:
        return WikiAskOut(found=False, grounded=False, answer="", reason="no_match", sources=[], backend="", model="",
                          seconds=0.0)
    rating = ""
    for p in found:
        if p.page.rating != "general":
            require_viewable(request, p.page.rating, None, what=f"wiki:{p.page.slug}")
            rating = ctx.settings.content.higher(rating, p.page.rating)
    router_ = LLMRouter(ctx.settings, ctx.db)
    started = time.monotonic()
    try:
        result = AgentRunner(ctx.settings, router_).run(
            "wiki.ask", {"language": language_name(body.language), "question": body.question.strip(),
                         "excerpts": ask.excerpts_text(found)}, rating=rating)
    except LLMError as exc:
        raise _llm_error(exc) from exc
    backend, model, _ = router_.resolve("knowledge", rating=rating)
    cited = {int(n) for n in result.data.get("cited", []) if isinstance(n, int) and 1 <= n <= len(found)}
    # an answer may carry [n] marks the model forgot to list
    marks = re.findall(r"\[(\d{1,2})\]", str(result.data.get("answer", "")))
    cited |= {int(n) for n in marks if 1 <= int(n) <= len(found)}
    is_found = bool(result.data.get("found"))
    return WikiAskOut(
        found=is_found, grounded=is_found and bool(cited), answer=str(result.data.get("answer", "")).strip(), reason="",
        sources=[WikiAskSource(n=p.n, slug=p.page.slug, title=p.page.title, source=p.page.source, rating=p.page.rating,
                               heading=p.heading, snippet=_clip(p.text), cited=p.n in cited) for p in found],
        backend=backend, model=model, seconds=round(time.monotonic() - started, 1))


def _clip(text: str, limit: int = 220) -> str:
    flat = re.sub(r"\s+", " ", re.sub(r"[#>*`|]", "", text)).strip()
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "..."


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
