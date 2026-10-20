"""What the language model was asked and answered, the prompts behind each agent, and the plan/style they work from.

Nothing here is a summary: transcripts are the stored conversation, agents are the real prompt files, and the plan and
style are the exact objects the stages read. Edits to prompts apply to the next model call; edits to the plan/style
apply to what is generated from now on (nothing already made is touched).
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, Request

from storyagent.agents.registry import (
    EDITABLE,
    AgentSpec,
    load_specs,
    packaged_spec,
    remove_override,
    user_file,
    write_override,
)
from storyagent.workflow.flow import describe_flow

from .context import ctx_of
from .errors import ApiError
from .schemas import AgentEdit, AgentOut, AiCallDetail, AiCallRow, FlowOut

router = APIRouter(prefix="/api", tags=["transparency"])


def _agent_out(request: Request, spec: AgentSpec) -> AgentOut:
    settings = ctx_of(request).settings
    default = packaged_spec(spec.name)
    return AgentOut(
        name=spec.name, role=spec.role, description=spec.description, system=spec.system, prompt=spec.prompt,
        inputs=list(spec.inputs), temperature=spec.temperature, max_tokens=spec.max_tokens, attempts=spec.attempts,
        model=spec.model, overridden=user_file(settings, spec.name).is_file(), default_system=default.system,
        default_prompt=default.prompt, editable=list(EDITABLE))


@router.get("/agents", response_model=list[AgentOut])
def list_agents(request: Request) -> list[AgentOut]:
    specs = load_specs(ctx_of(request).settings)
    return [_agent_out(request, specs[n]) for n in sorted(specs)]


@router.put("/agents/{name}", response_model=AgentOut)
def edit_agent(name: str, body: AgentEdit, request: Request) -> AgentOut:
    """Save your own wording for an agent; it applies to the next call (the packaged default is kept and restorable)."""
    spec = write_override(ctx_of(request).settings, name, body.model_dump(exclude_unset=True, exclude_none=True))
    return _agent_out(request, spec)


@router.delete("/agents/{name}", response_model=AgentOut)
def reset_agent(name: str, request: Request) -> AgentOut:
    return _agent_out(request, remove_override(ctx_of(request).settings, name))


@router.get("/projects/{project_id}/ai-calls", response_model=list[AiCallRow])
def list_calls(
    project_id: int, request: Request, label: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: int | None = None,
) -> list[AiCallRow]:
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    rows = ctx.db.conn.execute(
        "SELECT id, label, agent, model, tokens_in, tokens_out, duration_ms, created_at,"
        " length(prompt) AS prompt_chars, length(response) AS response_chars FROM generation_logs"
        " WHERE project_id=? AND (? IS NULL OR label=?) AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
        (project_id, label, label, before, before, limit)).fetchall()
    return [AiCallRow.model_validate(dict(r)) for r in rows]


@router.get("/ai-calls/{call_id}", response_model=AiCallDetail)
def get_call(call_id: int, request: Request) -> AiCallDetail:
    row = ctx_of(request).db.conn.execute("SELECT * FROM generation_logs WHERE id=?", (call_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", f"ai call {call_id}")
    return AiCallDetail.model_validate(dict(row))


_DATA_KEYS = ("plan", "style")


@router.get("/projects/{project_id}/data/{key}")
def get_data(project_id: int, key: str, request: Request) -> dict[str, Any]:
    """The production ``plan`` (genre, tone, acts, cast and place lists) or the visual ``style`` guide."""
    if key not in _DATA_KEYS:
        raise ApiError(404, "not_found", f"unknown data {key!r}; one of {list(_DATA_KEYS)}")
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    value = ctx.workflow.get_data(project_id, key, {})
    return dict(value) if isinstance(value, dict) else {}


@router.put("/projects/{project_id}/data/{key}")
def put_data(project_id: int, key: str, request: Request, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Replace the plan/style. Later generation reads it; finished assets are not touched."""
    if key not in _DATA_KEYS:
        raise ApiError(404, "not_found", f"unknown data {key!r}; one of {list(_DATA_KEYS)}")
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    with ctx.db.transaction():
        ctx.workflow.put_data(project_id, key, body)
    return body


@router.get("/flow", response_model=FlowOut)
def flow(request: Request, project_id: int | None = None) -> FlowOut:
    """The production chain: per step the agents (and their language models), the media models and their parameters
    per quality tier, what it reads and writes; with ``project_id`` also the step's state and what is made so far."""
    ctx = ctx_of(request)
    if project_id is not None:
        ctx.projects.get(project_id)
    return FlowOut(**describe_flow(ctx.settings, ctx.db, ctx.workflow, ctx.ledger, project_id))
