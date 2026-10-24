"""Connect outside software: import a ComfyUI workflow (API format) as a model of the ``comfy`` provider.

Operator only (``/api/tools`` is an admin prefix): saving writes the template and a model entry into the operator's own
config folder, and the running server loads it at once; the worker does so when it is between jobs.
"""
from __future__ import annotations

import subprocess

import anyio
from fastapi import APIRouter, Request

from storyagent.connect import clihelp
from storyagent.connect.comfy_import import ImportResult, import_workflow
from storyagent.connect.snippets import TEMPLATES, Checked, check_snippet, save_snippet, template
from storyagent.connect.store import describe, save_comfy, workflows_dir

from .context import ctx_of, translator_of
from .errors import ApiError
from .reloading import reload_now
from .schemas import (
    CliBuildRequest,
    CliBuilt,
    CliOption,
    CliParsed,
    CliParseRequest,
    ToolBinding,
    ToolChecked,
    ToolConnectorSaved,
    ToolImportPreview,
    ToolImportRequest,
    ToolIssue,
    ToolModel,
    ToolSaved,
    ToolSaveRequest,
    ToolSnippet,
    ToolTemplate,
    ToolWarning,
)

router = APIRouter(prefix="/api/tools", tags=["tools"])


def _preview(request: Request, result: ImportResult) -> ToolImportPreview:
    t = translator_of(request)
    kinds = result.template["output_type"]
    return ToolImportPreview(
        bindings=[ToolBinding(**row) for row in describe(result)],
        warnings=[ToolWarning(code=w.code, message=t(f"connect.warn.{w.code}", **w.params)) for w in result.warnings],
        output_node=str(result.template["output"]),
        output_type=[kinds] if isinstance(kinds, str) else [str(k) for k in kinds],
        defaults=result.defaults,
    )


@router.get("", response_model=list[ToolModel])
def list_tools(request: Request) -> list[ToolModel]:
    """Models that run through a connector (ComfyUI workflow, command line, HTTP service), and whether a ComfyUI
    template is the operator's own."""
    settings = ctx_of(request).settings
    mine = workflows_dir(settings)
    out = []
    for ref, m in sorted(settings.models.items()):
        if m.provider not in ("comfy", "cli", "http"):
            continue
        name = str(m.extra.get("workflow", ""))
        out.append(ToolModel(ref=ref, kind=m.kind, provider=m.provider, family=m.family, workflow=name,
                             mine=bool(name) and (mine / f"{name}.json").is_file()))
    return out


@router.post("/import/comfy", response_model=ToolImportPreview)
def preview_comfy(body: ToolImportRequest, request: Request) -> ToolImportPreview:
    """What would be bound, and what could not be - nothing is written."""
    return _preview(request, import_workflow(body.workflow, body.kind))


@router.post("/comfy", response_model=ToolSaved, status_code=201)
async def save_comfy_tool(body: ToolSaveRequest, request: Request) -> ToolSaved:
    settings = ctx_of(request).settings
    result = import_workflow(body.workflow, body.kind)
    saved = save_comfy(settings, name=body.name, ref=body.ref or body.name, kind=body.kind, result=result,
                       family=body.family, heavy=body.heavy, timeout_sec=body.timeout_sec, force=body.force)
    error = await anyio.to_thread.run_sync(reload_now, request.app, request.app.state.reload_loader)
    return ToolSaved(ref=body.ref or body.name, workflow=str(saved.workflow_path), models_file=str(saved.models_path),
                     reloaded=not error, error=error)


def _checked(request: Request, checked: Checked) -> ToolChecked:
    t = translator_of(request)
    return ToolChecked(
        ref=checked.ref, kind=checked.kind, provider=checked.provider, ok=checked.ok,
        issues=[ToolIssue(level=i.level, code=i.code, message=t(f"connect.issue.{i.code}", **i.params))
                for i in checked.issues])


@router.get("/templates", response_model=list[ToolTemplate])
def templates() -> list[ToolTemplate]:
    """Starting points for a command-line tool and for an HTTP service (text for models.toml / services.toml)."""
    out = []
    for name in TEMPLATES:
        models, services = template(name)
        out.append(ToolTemplate(name=name, models=models, services=services))
    return out


@router.post("/connector/check", response_model=ToolChecked)
def check_connector(body: ToolSnippet, request: Request) -> ToolChecked:
    """Dry run of a ``[models.X]`` (and ``[services.Y]``) snippet: what is wrong with it, and what to look at."""
    return _checked(request, check_snippet(ctx_of(request).settings, body.models, body.services, force=body.force))


@router.post("/connector", response_model=ToolConnectorSaved, status_code=201)
async def save_connector(body: ToolSnippet, request: Request) -> ToolConnectorSaved:
    """Check, add the snippet to the operator's models.toml (and services.toml), and load it."""
    checked = save_snippet(ctx_of(request).settings, body.models, body.services, force=body.force)
    error = await anyio.to_thread.run_sync(reload_now, request.app, request.app.state.reload_loader)
    shown = _checked(request, checked)
    return ToolConnectorSaved(ref=checked.ref, reloaded=not error, error=error, issues=shown.issues)


@router.post("/cli/parse", response_model=CliParsed)
async def parse_cli(body: CliParseRequest) -> CliParsed:
    """The options of a command-line tool, read from its ``--help`` (run without a shell, time-limited, no input) or
    from help text the operator pasted. Nothing is written."""
    text, ran = body.help_text, False
    if not text.strip():
        try:
            text = await anyio.to_thread.run_sync(clihelp.run_help, body.program, body.help_arg)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ApiError(400, "invalid_input", str(exc)[:200]) from exc
        ran = True
    options = clihelp.parse_help(text)
    if not options:
        raise ApiError(400, "invalid_input", "no options found in the help text")
    return CliParsed(program=body.program, options=[CliOption(**o.asdict()) for o in options],
                     roles=list(clihelp.ROLES), ran=ran)


@router.post("/cli/build", response_model=CliBuilt)
def build_cli(body: CliBuildRequest) -> CliBuilt:
    """The connector text for the chosen options, and the command it makes with sample values (nothing is written:
    paste it into the connector editor, check and save)."""
    options = [clihelp.Option(flag=o.flag, flags=o.flags or [o.flag], metavars=o.metavars, description=o.description,
                              kind=o.kind, default=o.default, role=o.role, param=o.param, repeat=o.repeat,
                              include=o.include) for o in body.options]
    text, argv = clihelp.connector_text(body.ref, body.kind, body.program, options, ext=body.ext, cwd=body.cwd,
                                        timeout_sec=body.timeout_sec, license_tag=body.license_tag)
    _, params = clihelp.build_argv(body.program, options)
    return CliBuilt(models=text, command=clihelp.preview(argv, params, body.sample, body.ext), argv=argv)
