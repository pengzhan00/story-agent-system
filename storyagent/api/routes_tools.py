"""Connect outside software: import a ComfyUI workflow (API format) as a model of the ``comfy`` provider.

Operator only (``/api/tools`` is an admin prefix): saving writes the template and a model entry into the operator's own
config folder. The running server and worker read the configuration once, so a saved model is usable after they restart.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from storyagent.connect.comfy_import import ImportResult, import_workflow
from storyagent.connect.store import describe, save_comfy, workflows_dir

from .context import ctx_of, translator_of
from .schemas import (
    ToolBinding,
    ToolImportPreview,
    ToolImportRequest,
    ToolModel,
    ToolSaved,
    ToolSaveRequest,
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
def save_comfy_tool(body: ToolSaveRequest, request: Request) -> ToolSaved:
    settings = ctx_of(request).settings
    result = import_workflow(body.workflow, body.kind)
    saved = save_comfy(settings, name=body.name, ref=body.ref or body.name, kind=body.kind, result=result,
                       family=body.family, heavy=body.heavy, timeout_sec=body.timeout_sec, force=body.force)
    return ToolSaved(ref=body.ref or body.name, workflow=str(saved.workflow_path), models_file=str(saved.models_path),
                     restart_needed=True)
