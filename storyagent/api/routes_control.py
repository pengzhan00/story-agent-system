"""Unified plans and manual publications; no HTTP handler starts a generation process."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import Field

from storyagent.control.catalog import ControlError, catalog, descriptor, digest
from storyagent.control.graph import GraphStore, asset_ids
from storyagent.control.publications import Publications
from storyagent.domain import QualityTier
from storyagent.util import new_id, now_iso

from .context import ctx_of
from .errors import ApiError
from .routes_review import actor_of
from .schemas import Strict
from .visibility import hidden, require_viewable

router = APIRouter(prefix="/api", tags=["control-center"])


class GraphNodeIn(Strict):
    id: str = Field(min_length=1, max_length=80)
    kind: str
    model_ref: str = ""
    tier: QualityTier = QualityTier.FINAL
    inputs: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    review: bool = False
    skip: bool = False
    depends_on: list[str] = Field(default_factory=list)


class PlanIn(Strict):
    name: str = Field(default="", max_length=200)
    project_id: int | None = None
    nodes: list[GraphNodeIn] = Field(min_length=1, max_length=200)
    request_key: str | None = Field(default=None, min_length=1, max_length=200)
    expected_digest: str | None = None
    template_version: int | None = Field(default=None, ge=1)


class ActionIn(Strict):
    action: str
    node_id: str | None = None
    revision: int | None = None
    external_id: str = Field(default="", max_length=500)
    evidence: str = Field(default="", max_length=10000)


class TargetIn(Strict):
    platform: str = Field(min_length=1, max_length=100)
    account: str = Field(min_length=1, max_length=200)
    channel: str = Field(default="", max_length=200)
    name: str = Field(default="", max_length=200)


class CategoryIn(Strict):
    name: str = Field(min_length=1, max_length=100)


class PublicationItemIn(Strict):
    asset_id: str = Field(min_length=32, max_length=32)
    role: str = Field(default="main", min_length=1, max_length=80)


class PublicationClassification(Strict):
    content_type: str = Field(default="", max_length=100)
    purpose: str = Field(default="", max_length=100)
    series: str = Field(default="", max_length=200)
    episode: str = Field(default="", max_length=100)


class PublicationIn(Strict):
    target_id: str
    project_id: int | None = None
    category_id: str | None = None
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=50000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    items: list[PublicationItemIn] = Field(min_length=1, max_length=100)
    scheduled_at: str | None = None
    timezone: str = "UTC"
    request_key: str | None = Field(default=None, min_length=1, max_length=200)
    revision: int | None = None
    classification: PublicationClassification = Field(default_factory=PublicationClassification)


class PublicationStateIn(Strict):
    status: str
    revision: int
    external_id: str = Field(default="", max_length=500)
    url: str = Field(default="", max_length=4000)
    actual_published_at: str = ""
    evidence: str = Field(default="", max_length=10000)
    scheduled_at: str = ""


class SelectionIn(Strict):
    owner_type: str = Field(min_length=1, max_length=80)
    owner_id: str = Field(min_length=1, max_length=100, pattern=r"^[0-9]+$")
    slot: str = Field(default="main", min_length=1, max_length=80)
    asset_id: str
    revision: int = Field(ge=0)


class ControlNodeOut(Strict):
    run_id: str
    node_id: str
    status: str
    attempt: int
    job_id: str | None
    outputs: dict[str, str]
    reason: str
    revision: int


class ControlEventOut(Strict):
    id: int
    run_id: str
    node_id: str | None
    event: str
    data: str
    actor: str
    created_at: str


class ControlRunOut(Strict):
    id: str
    project_id: int | None
    name: str
    plan: dict[str, Any]
    digest: str
    request_key: str | None
    status: str
    cancel_requested: bool
    actor: str
    revision: int
    created_at: str
    updated_at: str
    nodes: list[ControlNodeOut]
    events: list[ControlEventOut]


class ControlRunListOut(Strict):
    total: int
    items: list[ControlRunOut]


class TargetOut(Strict):
    id: str
    platform: str
    account: str
    channel: str
    name: str
    enabled: bool


class CategoryOut(Strict):
    id: str
    name: str
    enabled: bool


class PublicationItemOut(Strict):
    asset_id: str
    role: str
    position: int
    sha256: str
    kind: str
    duration_sec: float | None
    width: int | None
    height: int | None


class PublicationContentOut(Strict):
    publication_id: str
    revision: int
    title: str
    body: str
    tags: list[str]
    items: list[PublicationItemOut]
    actor: str
    created_at: str
    classification: PublicationClassification


class PublicationEventOut(Strict):
    id: int
    publication_id: str
    revision: int
    event: str
    detail: str
    actor: str
    created_at: str


class PublicationOut(Strict):
    id: str
    target_id: str
    project_id: int | None
    category_id: str | None
    status: str
    revision: int
    external_id: str | None
    url: str
    actual_published_at: str | None
    scheduled_at: str | None
    timezone: str
    confirmation: str
    actor: str
    request_key: str | None
    created_at: str
    updated_at: str
    revisions: list[PublicationContentOut]
    content: PublicationContentOut
    events: list[PublicationEventOut]


class PublicationListOut(Strict):
    total: int
    items: list[PublicationOut]


class ProjectPlanIn(Strict):
    script_id: int = Field(gt=0)
    source_audio: str = ""
    assemble: bool = False
    separate_vocals: bool = True
    tail_policy: str = "reject"


def graph(request: Request) -> GraphStore:
    ctx = ctx_of(request)
    return GraphStore(ctx.settings, ctx.db, ctx.ledger)


def pubs(request: Request) -> Publications:
    ctx = ctx_of(request)
    return Publications(ctx.db, ctx.ledger)


def project_access(request: Request, pid: int | None, *, historical: bool = False) -> None:
    if pid is None:
        return
    ctx = ctx_of(request)
    row = ctx.db.conn.execute(
        "SELECT rating FROM projects WHERE id=?" + ("" if historical else " AND status != 'trashed'"), (pid,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "not_found", pid)
    require_viewable(request, row["rating"], pid)


def asset_access(request: Request, aid: str, *, historical: bool = False) -> None:
    ctx = ctx_of(request)
    a = ctx.ledger.get_asset(aid)
    require_viewable(request, a.rating, a.project_id, what=a.id)
    if a.trashed_at and not historical:
        raise ApiError(409, "control_conflict", "Asset is trashed")


def referenced_access(request: Request, value: Any) -> None:
    """References in parameters obey the same access rules as visible input controls."""
    ctx = ctx_of(request)
    if isinstance(value, dict):
        for child in value.values():
            referenced_access(request, child)
    elif isinstance(value, list):
        for child in value:
            referenced_access(request, child)
    elif isinstance(value, str):
        if value.startswith("asset:"):
            asset_access(request, value[6:], historical=True)
        elif value.startswith("library:"):
            role, sep, name = value[8:].partition("/")
            item = ctx.library.find(role, name) if sep else None
            if item is None or item.asset is None:
                raise ApiError(404, "not_found", value)
            asset_access(request, item.asset.id, historical=True)
        elif value.startswith("job:"):
            job = ctx.ledger.get_job(value[4:])
            project_access(request, job.project_id)
            from storyagent.content import job_rating, project_rating

            rating = job_rating(ctx.settings, project_rating(ctx.db, job.project_id), job.model_ref)
            require_viewable(request, rating, job.project_id, what=job.id)
            for a in ctx.ledger.list_assets(job_id=job.id):
                asset_access(request, a.id, historical=True)


def run_access(request: Request, run: dict[str, Any]) -> None:
    project_access(request, run["project_id"], historical=True)
    ids = asset_ids(run["plan"])
    ids |= {a for n in run["nodes"] for a in n["outputs"].values()}
    for aid in ids:
        asset_access(request, aid, historical=True)
    referenced_access(request, run["plan"])


def publication_access(request: Request, record: dict[str, Any]) -> None:
    project_access(request, record["project_id"], historical=True)
    for rev in record["revisions"]:
        for item in rev["items"]:
            asset_access(request, item["asset_id"], historical=True)


def admin(request: Request) -> None:
    ctx = ctx_of(request)
    if ctx.settings.auth.enabled:
        user = getattr(request.state, "user", None)
        role = ctx.settings.auth.roles.get(user.role) if user else None
        if role is None or not role.admin:
            raise ApiError(403, "forbidden")


@router.get("/capabilities")
def capabilities(request: Request, project_id: int | None = None) -> list[dict[str, Any]]:
    project_access(request, project_id)
    return catalog(ctx_of(request).ledger.settings_for(project_id))


def plan_access(request: Request, body: PlanIn) -> dict[str, Any]:
    project_access(request, body.project_id)
    for node in body.nodes:
        cap = descriptor(ctx_of(request).settings, node.kind)
        for field in cap["inputs"]:
            value = node.inputs.get(field["name"])
            if field["type"] in {"media", "medialist"} and value:
                values = value if isinstance(value, list) else [value]
                for v in values:
                    if isinstance(v, dict) and "$node" in v:
                        continue
                    if not isinstance(v, str) or not v.startswith("asset:"):
                        raise ApiError(422, "control_invalid", "Use an asset reference for media inputs")
        for aid in asset_ids(node.inputs) | asset_ids(node.params):
            asset_access(request, aid)
        referenced_access(request, node.params)
        for key in ("audio_stem", "source_audio", "parent_audio"):
            v = node.params.get(key)
            if (
                v
                and not (isinstance(v, dict) and "$node" in v)
                and not (isinstance(v, str) and v.startswith(("asset:", "library:", "job:")))
            ):
                raise ApiError(422, "control_invalid", "Media parameters require an asset reference")
    return body.model_dump()


@router.post("/control/plans")
def preview(body: PlanIn, request: Request) -> dict[str, Any]:
    plan = graph(request).compile(plan_access(request, body))
    return {**plan, "digest": digest(plan)}


@router.post("/projects/{project_id}/control/plan", response_model=PlanIn)
def compile_project(project_id: int, body: ProjectPlanIn, request: Request) -> dict[str, Any]:
    from storyagent.control.project import project_plan

    project_access(request, project_id)
    if body.source_audio:
        if not body.source_audio.startswith("asset:"):
            raise ApiError(422, "control_invalid", "Source audio must be an asset reference")
        asset_access(request, body.source_audio[6:])
    definition = project_plan(ctx_of(request), project_id, **body.model_dump())
    plan_access(request, PlanIn(**definition))
    graph(request).compile(definition)
    return definition


@router.post("/control/runs", status_code=201, response_model=ControlRunOut)
def submit(body: PlanIn, request: Request) -> dict[str, Any]:
    definition = plan_access(request, body)
    return graph(request).submit(definition, actor_of(request), body.request_key, body.expected_digest)


@router.get("/control/runs", response_model=ControlRunListOut)
def runs(request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    # Filter visibility before pagination. Plans may contain standalone restricted inputs.
    visible = []
    for row in ctx_of(request).db.conn.execute("SELECT id FROM control_runs ORDER BY created_at DESC,id"):
        record = graph(request).get(row[0])
        try:
            run_access(request, record)
        except ApiError as exc:
            if exc.status in {403, 409}:
                continue
            raise
        visible.append(record)
    return {"total": len(visible), "items": visible[offset : offset + limit]}


@router.get("/control/runs/{rid}", response_model=ControlRunOut)
def run(rid: str, request: Request) -> dict[str, Any]:
    record = graph(request).get(rid)
    run_access(request, record)
    return record


@router.post("/control/runs/{rid}/actions", response_model=ControlRunOut)
def run_action(rid: str, body: ActionIn, request: Request) -> dict[str, Any]:
    run_access(request, graph(request).get(rid))
    return graph(request).action(
        rid, body.action, actor_of(request), body.node_id, body.revision, body.external_id, body.evidence
    )


@router.get("/control/templates")
def templates(request: Request) -> list[dict[str, Any]]:
    result = []
    for row in ctx_of(request).db.conn.execute("SELECT * FROM control_templates ORDER BY created_at DESC"):
        data = json.loads(row["definition"])
        try:
            plan_access(request, PlanIn(**data))
        except ApiError:
            continue
        result.append({**dict(row), "definition": data})
    return result


@router.post("/control/templates", status_code=201)
def save_template(body: PlanIn, request: Request) -> dict[str, Any]:
    definition = plan_access(request, body)
    graph(request).compile(definition)
    ident = new_id()
    ctx_of(request).db.conn.execute(
        "INSERT INTO control_templates VALUES (?,?,?,?,?)", (ident, 1, body.name, json.dumps(definition), now_iso())
    )
    return {"id": ident, "version": 1}


@router.put("/control/templates/{ident}")
def revise_template(ident: str, body: PlanIn, request: Request) -> dict[str, Any]:
    definition = plan_access(request, body)
    graph(request).compile(definition)
    ctx = ctx_of(request)
    with ctx.db.transaction() as c:
        row = c.execute(
            "SELECT version,definition FROM control_templates WHERE id=? ORDER BY version DESC LIMIT 1", (ident,)
        ).fetchone()
        if row is None:
            raise ApiError(404, "not_found", ident)
        plan_access(request, PlanIn(**json.loads(row["definition"])))
        if body.template_version != row["version"]:
            raise ApiError(409, "control_conflict", "Stale template version")
        version = row["version"] + 1
        c.execute(
            "INSERT INTO control_templates VALUES (?,?,?,?,?)",
            (ident, version, body.name, json.dumps(definition), now_iso()),
        )
    return {"id": ident, "version": version}


@router.get("/publications/targets", response_model=list[TargetOut])
def targets(request: Request) -> list[dict[str, Any]]:
    return pubs(request).targets()


@router.post("/publications/targets", status_code=201, response_model=TargetOut)
def target(body: TargetIn, request: Request) -> dict[str, Any]:
    admin(request)
    return pubs(request).target(**body.model_dump())


@router.get("/publications/categories", response_model=list[CategoryOut])
def categories(request: Request) -> list[dict[str, Any]]:
    return pubs(request).categories()


@router.post("/publications/categories", status_code=201, response_model=CategoryOut)
def category(body: CategoryIn, request: Request) -> dict[str, Any]:
    admin(request)
    return pubs(request).category(body.name)


@router.get("/publications", response_model=PublicationListOut)
def publications(
    request: Request,
    target_id: str = "",
    status: str = "",
    category_id: str = "",
    project_id: int | None = None,
    asset_id: str = "",
    platform: str = "",
    account: str = "",
    content_type: str = "",
    purpose: str = "",
    series: str = "",
    episode: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    # Shared visibility predicate keeps list counts and drill-down consistent.
    result = []
    start = 0
    while True:
        batch = pubs(request).listing(
            target_id=target_id,
            status=status,
            category_id=category_id,
            project_id=project_id,
            asset_id=asset_id,
            platform=platform,
            account=account,
            content_type=content_type,
            purpose=purpose,
            series=series,
            episode=episode,
            offset=start,
            limit=100,
        )
        for record in batch["items"]:
            try:
                publication_access(request, record)
            except ApiError as exc:
                if exc.status in {403, 409}:
                    continue
                raise
            result.append(record)
        start += 100
        if start >= batch["total"]:
            break
    return {"total": len(result), "items": result[offset : offset + limit]}


@router.post("/publications", status_code=201, response_model=PublicationOut)
def create_publication(body: PublicationIn, request: Request) -> dict[str, Any]:
    project_access(request, body.project_id)
    for item in body.items:
        asset_access(request, item.asset_id)
    return pubs(request).save(body.model_dump(), actor_of(request))


@router.get("/publications/{ident}", response_model=PublicationOut)
def publication(ident: str, request: Request) -> dict[str, Any]:
    record = pubs(request).get(ident)
    publication_access(request, record)
    return record


@router.put("/publications/{ident}", response_model=PublicationOut)
def edit_publication(ident: str, body: PublicationIn, request: Request) -> dict[str, Any]:
    publication_access(request, pubs(request).get(ident))
    project_access(request, body.project_id)
    for item in body.items:
        asset_access(request, item.asset_id)
    return pubs(request).save(body.model_dump(), actor_of(request), ident, body.revision)


@router.post("/publications/{ident}/state", response_model=PublicationOut)
def publication_state(ident: str, body: PublicationStateIn, request: Request) -> dict[str, Any]:
    publication_access(request, pubs(request).get(ident))
    return pubs(request).transition(ident, actor=actor_of(request), **body.model_dump())


@router.get("/assets/{aid}/relations")
def relations(aid: str, request: Request) -> dict[str, Any]:
    asset_access(request, aid)
    ctx = ctx_of(request)
    result = []
    for row in ctx.db.conn.execute("SELECT * FROM asset_relations WHERE parent_id=? OR child_id=?", (aid, aid)):
        parent, child = ctx.ledger.get_asset(row["parent_id"]), ctx.ledger.get_asset(row["child_id"])
        if not hidden(parent.rating, parent.project_id) and not hidden(child.rating, child.project_id):
            result.append(dict(row))
    selections = []
    for row in ctx.db.conn.execute("SELECT * FROM asset_selections WHERE asset_id=?", (aid,)):
        pid = int(row["owner_id"]) if row["owner_type"] == "project" else None
        if row["owner_type"] == "shot":
            shot = ctx.db.conn.execute("SELECT project_id FROM shots WHERE id=?", (row["owner_id"],)).fetchone()
            if shot is None:
                continue
            pid = shot[0]
        try:
            project_access(request, pid, historical=True)
        except ApiError as exc:
            if exc.status in {403, 404, 409}:
                continue
            raise
        selections.append(dict(row))
    return {"relations": result, "selections": selections}


@router.put("/control/selections")
def selection(body: SelectionIn, request: Request) -> dict[str, Any]:
    asset_access(request, body.asset_id)
    ctx = ctx_of(request)
    asset = ctx.ledger.get_asset(body.asset_id)
    # Selection owners are real project/shot objects; arbitrary IDs cannot create cross-project adoption.
    if body.owner_type == "project":
        project_access(request, int(body.owner_id))
        if asset.project_id not in {None, int(body.owner_id)}:
            raise ApiError(422, "control_invalid", "Project mismatch")
    elif body.owner_type == "shot":
        row = ctx.db.conn.execute("SELECT project_id FROM shots WHERE id=?", (body.owner_id,)).fetchone()
        if row is None or asset.project_id not in {None, row[0]}:
            raise ApiError(422, "control_invalid", "Shot mismatch")
        project_access(request, row[0])
        if body.slot not in {"shot.keyframe", "shot.video"}:
            raise ApiError(422, "control_invalid", "Unknown shot selection slot")
        wanted = "image" if body.slot == "shot.keyframe" else "video"
        from storyagent.workshop import output_media

        media = asset.kind.split(".", 1)[1] if asset.kind.startswith("upload.") else output_media(asset.kind)
        if media != wanted:
            raise ApiError(422, "control_invalid", "Asset media does not match the selection slot")
    else:
        raise ApiError(422, "control_invalid", "Unknown selection owner")
    with ctx.db.transaction() as c:
        key = (body.owner_type, body.owner_id, body.slot)
        old = c.execute(
            "SELECT revision FROM asset_selections WHERE owner_type=? AND owner_id=? AND slot=?", key
        ).fetchone()
        if body.revision != (old[0] if old else 0):
            raise ApiError(409, "control_conflict", "Stale selection revision")
        c.execute(
            "INSERT INTO asset_selections VALUES (?,?,?,?,?,?,?) ON CONFLICT(owner_type,owner_id,slot) "
            "DO UPDATE SET asset_id=excluded.asset_id,revision=excluded.revision,actor=excluded.actor,"
            "updated_at=excluded.updated_at",
            (*key, body.asset_id, body.revision + 1, actor_of(request), now_iso()),
        )
        ctx.ledger.adopt(asset.id)
    return {"asset_id": asset.id, "revision": body.revision + 1}


@router.get("/control/selections")
def get_selection(request: Request, owner_type: str, owner_id: int, slot: str) -> dict[str, Any]:
    ctx = ctx_of(request)
    if owner_type == "shot":
        shot = ctx.story.get_shot(owner_id)
        project_access(request, shot["project_id"])
    elif owner_type == "project":
        project_access(request, owner_id)
    else:
        raise ApiError(422, "control_invalid", "Unknown selection owner")
    row = ctx.db.conn.execute(
        "SELECT * FROM asset_selections WHERE owner_type=? AND owner_id=? AND slot=?", (owner_type, str(owner_id), slot)
    ).fetchone()
    if row:
        asset_access(request, row["asset_id"], historical=True)
        return dict(row)
    return {"asset_id": None, "revision": 0}


__all__ = ["router", "ControlError", "sqlite3"]
