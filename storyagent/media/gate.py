"""Everything that can refuse a job before any work is done on it, in one place.

The worker runs this as the first step of every job, whatever queued it (a workflow run, the workshop, a spec made
again by hand, the command line). The look ahead of a run (``workflow/preflight.py``) runs the very same function
on the jobs the run is about to queue, without queueing them, so what the worker would refuse is known before the
run starts and not found one failed job at a time.

In order: references (``asset:`` / ``library:`` / ``job:``) become files; compliance (voice consent, restricted
licences, remote services); the content gates of the project's rating; the prompt language the model reads; the
parameters against the adapter's schema; the adapter's own ``validate``. Nothing here writes, starts or downloads.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from storyagent import content
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.domain.errors import ErrorCode

from . import compliance
from .assetrefs import RefError, Resolver
from .contracts import Adapter, JobContext, JobRecord, ValidationIssue
from .ledger import Ledger
from .params import validate_params
from .prompts import language_issues

Log = Callable[..., None]


def quiet(event: str, level: str = "info", **data: Any) -> None:
    """The log of a check that only looks."""


@dataclass
class Checked:
    job: JobRecord                  # with its references resolved to files
    ctx: JobContext
    rating: str                     # the rating the result will carry (the project's, the model's, the references')
    issues: list[ValidationIssue]   # empty = the job may start


def check(
    settings: Settings, db: Database, ledger: Ledger, adapter: Adapter, resolver: Resolver, job: JobRecord,
    work_dir: Path, *, project_settings: Mapping[str, Any], project_rating: str, log: Log = quiet,
) -> Checked:
    rating = content.job_rating(settings, project_rating, job.model_ref)
    try:  # asset:/library: references become plain paths; adapters never see them
        inputs, params, resolved = resolver.resolve(job.inputs, job.params)
    except RefError as exc:
        issue = ValidationIssue(ErrorCode.INVALID_INPUT, str(exc), {"detail": str(exc)})
        return Checked(job, JobContext(settings, job, work_dir, log, project_settings), rating, [issue])
    if resolved:
        job = replace(job, inputs=inputs, params=params)
        log("refs_resolved", refs=resolved.assets, sha256=resolved.sha256)
        for aid in resolved.assets.values():
            rating = settings.content.higher(rating, ledger.get_asset(aid).rating)
    ctx = JobContext(settings, job, work_dir, log, project_settings)
    issues = compliance.check_job(settings, job, project_settings)
    issues += compliance.check_remote(settings, job, project_settings)
    notice = compliance.license_notice(settings, job, project_settings)
    if notice:
        log("license_notice", "warning", **notice)
    issues += content.gate_issues(settings, db, job, job.project_id, rating)
    picked = settings.models.get(job.model_ref)
    if picked is not None:
        issues += language_issues(picked.prompt_language, job.kind.value, job.inputs)
    p_issues, warnings = validate_params(job.params, adapter.capabilities().param_schema,
                                         strict=settings.strict_params)
    issues += p_issues + adapter.validate(job, ctx)
    for w in warnings:
        log("param_warning", "warning", message=w)
    return Checked(job, ctx, rating, issues)
