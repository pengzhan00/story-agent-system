"""The storyboard check: violations of the shot grammar, and the mechanical repairs."""
from __future__ import annotations

from fastapi import APIRouter, Request

from storyagent.workflow.grammar import apply_fixes, run_check

from .context import ctx_of
from .schemas import GrammarFixBody, GrammarFixOut, GrammarReportOut

router = APIRouter(prefix="/api/projects/{project_id}/grammar", tags=["grammar"])


@router.get("", response_model=GrammarReportOut)
def report(project_id: int, request: Request, episode: int | None = None) -> GrammarReportOut:
    """Every shot checked against `grammar.toml`; `episode` limits the check to one episode."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    return GrammarReportOut(**run_check(ctx.story, project_id, ctx.settings.grammar, episode).as_dict())


@router.post("/fix", response_model=GrammarFixOut)
def fix(project_id: int, body: GrammarFixBody, request: Request) -> GrammarFixOut:
    """Repair the fixable violations (all, or only the given rules / shots / episode). Locked shots are left alone.
    A repaired shot's image and video prompts are not rewritten: redo its specs when the repair changes the picture."""
    ctx = ctx_of(request)
    ctx.projects.get(project_id)
    fixed, left, after = apply_fixes(ctx.story, project_id, ctx.settings.grammar, episode=body.episode,
                                     rules=body.rules, shot_ids=body.shot_ids)
    return GrammarFixOut(fixed=fixed, left=left, report=GrammarReportOut(**after.as_dict()))
