"""Every script of every project in one place (the asset library's "project scripts" tab): what each says, in a form
that can be read, searched, copied and downloaded. Read only; the script is edited in its project."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from storyagent import script_text

from .context import ctx_of, translator_of
from .errors import ApiError
from .routes_library import _out
from .schemas import LibraryItemOut, ScriptBrief, ScriptDoc, ScriptPublish
from .visibility import hidden, require_viewable, too_young

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


def _brief(project: Any, row: dict[str, Any], locked: bool) -> dict[str, Any]:
    scenes, shots, lines = script_text.counts(row["acts"])
    return {
        "project_id": project.id, "project_name": project.name, "project_kind": project.kind, "rating": project.rating,
        "locked": locked, "episode_no": row["episode_no"], "title": "" if locked else row["title"],
        "synopsis": "" if locked else row["synopsis"], "scenes": scenes, "shots": shots, "lines": lines,
        "words": row["word_count"], "status": row["status"], "updated_at": row["updated_at"]}


@router.get("", response_model=list[ScriptBrief])
def list_scripts(
    request: Request, q: Annotated[str, Query(max_length=200)] = "", project_id: int | None = None,
) -> list[ScriptBrief]:
    """Scripts of the live projects the caller may see. A restricted project that is locked is listed (so it is known
    to exist) without its title and synopsis, and its text is never searched. ``q``: every word must occur in the
    title, synopsis or text."""
    ctx, t = ctx_of(request), translator_of(request)
    words = [w.lower() for w in q.split() if w.strip()]
    out: list[ScriptBrief] = []
    for project in ctx.projects.list():
        if (project_id is not None and project.id != project_id) or too_young(project.rating, None):
            continue
        locked = hidden(None, project.id)
        for row in ctx.story.list_scripts(project.id):
            if words:
                if locked:
                    continue
                text = script_text.render(row["title"], row["synopsis"], row["acts"], t, heading=project.name).lower()
                if not all(w in text for w in words):
                    continue
            out.append(ScriptBrief(**_brief(project, row, locked)))
    out.sort(key=lambda s: (s.project_id, s.episode_no))
    return out


@router.get("/{project_id}/{episode_no}", response_model=ScriptDoc)
def get_script(project_id: int, episode_no: int, request: Request) -> ScriptDoc:
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    require_viewable(request, project.rating, project_id, what=f"script:{project_id}/{episode_no}")
    row = ctx.story.get_script(project_id, episode_no)
    if row is None:
        raise ApiError(404, "not_found", f"script:{project_id}/{episode_no}")
    text = script_text.render(row["title"], row["synopsis"], row["acts"], translator_of(request), heading=project.name)
    return ScriptDoc(**_brief(project, row, False), text=text)


@router.post("/{project_id}/{episode_no}/publish", response_model=LibraryItemOut, status_code=201)
def publish_script(project_id: int, episode_no: int, body: ScriptPublish, request: Request) -> LibraryItemOut:
    """File the script as a named item (``library:script/<name>``): a snapshot of its text, usable from any project or
    the workshop, unaffected by later edits of the script. A restricted project's script stays in the vault."""
    ctx = ctx_of(request)
    project = ctx.projects.get(project_id)
    require_viewable(request, project.rating, project_id, what=f"script:{project_id}/{episode_no}")
    row = ctx.story.get_script(project_id, episode_no)
    if row is None:
        raise ApiError(404, "not_found", f"script:{project_id}/{episode_no}")
    text = script_text.render(row["title"], row["synopsis"], row["acts"], translator_of(request), heading=project.name)
    name = body.name.strip() or f"{project.name}-{episode_no}".replace("/", "-").replace("\\", "-")[:80]
    item = ctx.library.add_text(
        text, "script", name, project_id=project_id, slot=f"script_e{episode_no}", description=body.description,
        tags=body.tags, replace=body.replace, meta={"project_id": project_id, "episode_no": episode_no},
        origin={"project_id": project_id, "episode_no": episode_no, "script_id": row["id"]})
    return _out(item)
