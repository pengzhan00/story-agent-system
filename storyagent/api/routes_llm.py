"""Which language model each role uses (chosen on the screen, applied to the next call), and the AI buttons that sit
beside prompts: a suggestion for a better prompt and a translation of it."""
from __future__ import annotations

import time

from fastapi import APIRouter, Query, Request

from storyagent.agents.runner import AgentRunner, language_name
from storyagent.llm import LLMError, LLMRouter, overrides

from .context import ctx_of
from .errors import ApiError
from .schemas import (
    LlmBackendOut,
    LlmOut,
    LlmRoleOut,
    LlmRoleSet,
    LlmTest,
    LlmTestOut,
    PromptAssistIn,
    PromptAssistOut,
)

router = APIRouter(prefix="/api", tags=["llm"])

ROLE_GROUPS = {
    "director": "story", "character": "story", "scene": "story", "writer": "script", "reviewer": "script",
    "prompter": "prompts", "sound": "sound", "composer": "sound",
}
_GROUP_ORDER = ["story", "script", "prompts", "sound", "other"]


def _scope(project_id: int | None) -> str:
    return overrides.project_scope(project_id) if project_id is not None else overrides.GLOBAL


def _llm_error(exc: LLMError) -> ApiError:
    code = {"unreachable": "llm_unreachable", "timeout": "llm_unreachable", "model_missing": "llm_model_missing",
            "no_backend": "llm_unknown_backend"}.get(exc.code, "llm_failed")
    return ApiError(502, code, exc.message)


def _backends(request: Request, router_: LLMRouter) -> list[LlmBackendOut]:
    settings = ctx_of(request).settings
    out: list[LlmBackendOut] = []
    for name, spec in settings.llm_backends.items():
        svc = settings.services.get(spec.service) if spec.service else None
        if svc is not None and not svc.enabled:
            out.append(LlmBackendOut(name=name, kind=spec.kind, url=spec.url, state="disabled", models=[], error=""))
            continue
        try:
            models = sorted(router_.available(name, refresh=True))
            out.append(LlmBackendOut(name=name, kind=spec.kind, url=spec.url, state="ok", models=models, error=""))
        except LLMError as exc:
            out.append(LlmBackendOut(name=name, kind=spec.kind, url=spec.url, state="unreachable", models=[],
                                     error=exc.message))
    return out


@router.get("/llm", response_model=LlmOut)
def llm_state(request: Request, project_id: int | None = Query(default=None)) -> LlmOut:
    ctx = ctx_of(request)
    router_ = LLMRouter(ctx.settings, ctx.db)
    if project_id is not None:
        ctx.projects.get(project_id)
    glob = overrides.listing(ctx.db, overrides.GLOBAL)
    mine = overrides.listing(ctx.db, overrides.project_scope(project_id)) if project_id is not None else {}
    roles: list[LlmRoleOut] = []
    for name, cfg in ctx.settings.llm_roles.items():
        chosen, source = (mine.get(name), "project") if name in mine else (glob.get(name), "global")
        if chosen is None or chosen.backend not in ctx.settings.llm_backends:
            chosen, source = None, "config"
        roles.append(LlmRoleOut(
            name=name, group=ROLE_GROUPS.get(name, "other"), backend=chosen.backend if chosen else cfg.backend,
            model=chosen.model if chosen else cfg.model, source=source, config_backend=cfg.backend,
            config_model=cfg.model))
    roles.sort(key=lambda r: (_GROUP_ORDER.index(r.group), r.name))
    return LlmOut(backends=_backends(request, router_), roles=roles, project_id=project_id)


@router.put("/llm/roles/{role}", response_model=LlmOut)
def set_role(role: str, body: LlmRoleSet, request: Request) -> LlmOut:
    ctx = ctx_of(request)
    if role not in ctx.settings.llm_roles:
        raise ApiError(404, "llm_unknown_role", role)
    if body.backend not in ctx.settings.llm_backends:
        raise ApiError(422, "llm_unknown_backend", body.backend)
    model = body.model.strip()
    if not model:
        raise ApiError(422, "llm_model_required")
    if body.project_id is not None:
        ctx.projects.get(body.project_id)
    router_ = LLMRouter(ctx.settings, ctx.db)
    try:
        have = router_.available(body.backend, refresh=True)
    except LLMError:
        have = []                                   # the backend is down: accept the name, the run will say
    if have and model not in have:
        raise ApiError(422, "llm_model_not_installed", model)
    overrides.put(ctx.db, _scope(body.project_id), role, body.backend, model)
    return llm_state(request, body.project_id)


@router.delete("/llm/roles/{role}", response_model=LlmOut)
def reset_role(role: str, request: Request, project_id: int | None = Query(default=None)) -> LlmOut:
    overrides.remove(ctx_of(request).db, _scope(project_id), role)
    return llm_state(request, project_id)


@router.post("/llm/test", response_model=LlmTestOut)
def test_model(body: LlmTest, request: Request) -> LlmTestOut:
    """One short question to a model: does it answer, and how long does the first answer take."""
    ctx = ctx_of(request)
    if body.backend not in ctx.settings.llm_backends:
        raise ApiError(422, "llm_unknown_backend", body.backend)
    started = time.monotonic()
    try:
        res = LLMRouter(ctx.settings, ctx.db).client(body.backend).chat(
            body.model, "", "Reply with the single word: ok", temperature=0.0, max_tokens=16)
    except LLMError as exc:
        raise _llm_error(exc) from exc
    reply = res.text.strip()
    return LlmTestOut(ok=bool(reply), seconds=round(time.monotonic() - started, 1), reply=reply[:80])


@router.post("/ai/prompt", response_model=PromptAssistOut)
def prompt_assist(body: PromptAssistIn, request: Request) -> PromptAssistOut:
    """The AI buttons beside a prompt: ``suggest`` rewrites it (following the author's instruction), ``translate``
    puts it in another language. Nothing is saved: the screen shows the result and the author takes it or not. The
    model is the one chosen for the ``prompter`` role (of the project, when one is given)."""
    ctx = ctx_of(request)
    if body.project_id is not None:
        ctx.projects.get(body.project_id)
    router_ = LLMRouter(ctx.settings, ctx.db)
    runner = AgentRunner(ctx.settings, router_)
    agent = "prompt.suggest" if body.action == "suggest" else "prompt.translate"
    variables = {"purpose": body.purpose, "text": body.text}
    if body.action == "suggest":
        variables |= {"style": body.style or "plain", "instruction": body.instruction.strip(),
                      "language": language_name(body.target)}
    else:
        variables |= {"source": language_name(body.source) if body.source else "the language it is written in",
                      "target": language_name(body.target)}
    try:
        result = runner.run(agent, variables, project_id=body.project_id)
    except LLMError as exc:
        raise _llm_error(exc) from exc
    backend, model, _ = router_.resolve("prompter", project_id=body.project_id)
    return PromptAssistOut(text=str(result.data.get("text", "")).strip(), action=body.action, backend=backend,
                           model=model)
