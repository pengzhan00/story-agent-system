"""Update this checkout and restart worker and server: ``GET /api/update`` (state), ``POST /check``, ``POST /run``,
``POST /restart``, ``DELETE /restart``. Admin only (``/api/update`` is an admin prefix, and checked again here).

The update runs in a background thread of the server and is polled; the restart is a request that the worker and the
server answer by leaving with exit code 75 for ``scripts/start.sh`` to start them again (``storyagent.restart``)."""
from __future__ import annotations

import time

import anyio
from fastapi import APIRouter, Request

from storyagent.restart import RestartMode
from storyagent.update import Commit, RunningWork, RunState, Snapshot, UpdateError, Updater

from .context import Translator, ctx_of, translator_of
from .errors import ApiError
from .schemas import (
    UpdateCommitOut,
    UpdateJobOut,
    UpdateOut,
    UpdateRestartBody,
    UpdateRestartOut,
    UpdateRunBody,
    UpdateRunOfWorkOut,
    UpdateRunOut,
    UpdateStepOut,
    UpdateWorkOut,
)

router = APIRouter(prefix="/api/update", tags=["update"])


def updater_of(request: Request) -> Updater:
    updater: Updater = request.app.state.updater
    return updater


def _admin(request: Request) -> str:
    """The name to audit under; with accounts on, only an admin passes (even if auth.toml lost the prefix)."""
    ctx = ctx_of(request)
    cfg = ctx.settings.auth
    user = request.scope.get("state", {}).get("user")
    if not cfg.enabled:
        return "local"
    role = cfg.roles.get(user.role) if user else None
    if user is None or role is None or not role.admin:
        raise ApiError(403, "forbidden")
    return str(user.username)


def _commit(c: Commit | None) -> UpdateCommitOut | None:
    return UpdateCommitOut(sha=c.sha, short=c.short, subject=c.subject) if c else None


def _run(t: Translator, r: RunState | None) -> UpdateRunOut | None:
    if r is None:
        return None
    return UpdateRunOut(
        id=r.id, status=r.status, label=t(f"update.run.{r.status}"), by=r.by, started_at=r.started_at,
        finished_at=r.finished_at, before=_commit(r.before), after=_commit(r.after),
        behind=[c for c in (_commit(x) for x in r.behind) if c], failed_step=r.failed_step,
        steps=[UpdateStepOut(
            id=s.id, label=t(f"update.step.{s.id}"), status=s.status, status_label=t(f"update.state.{s.status}"),
            note=t(s.note) if s.note else "", detail=s.detail, exit_code=s.exit_code, started_at=s.started_at,
            finished_at=s.finished_at) for s in r.steps],
        restart=r.restart, restart_label=t(f"update.restart.{r.restart}") if r.restart != "none" else "",
        restart_mode=r.restart_mode, log_file=r.log_file)


def _work(w: RunningWork) -> UpdateWorkOut:
    return UpdateWorkOut(
        jobs=[UpdateJobOut(**j) for j in w.jobs], runs=[UpdateRunOfWorkOut(**r) for r in w.runs], queued=w.queued,
        heavy=sum(1 for j in w.jobs if j["heavy"]))


def out(request: Request, snap: Snapshot | None = None) -> UpdateOut:
    t = translator_of(request)
    updater = updater_of(request)
    snap = snap or updater.snapshot()
    i, r = snap.inspection, snap.restart
    can = (i.available.enabled and i.clean and not updater.busy() and not i.tools_missing
           and not (i.ahead_count and i.behind_count))
    return UpdateOut(
        enabled=i.available.enabled, reason=i.available.reason,
        reason_label=t(f"update.reason.{i.available.reason}") if i.available.reason else "",
        boot_id=snap.boot_id, started_at=snap.started_at, now=time.time(), current=_commit(i.current),
        branch=i.branch, remote_ref=i.remote_ref, remote_known=i.remote_known, clean=i.clean, dirty=i.dirty,
        behind=[c for c in (_commit(x) for x in i.behind) if c], behind_count=i.behind_count,
        ahead_count=i.ahead_count, checked_at=i.checked_at, tools_missing=i.tools_missing, busy=updater.busy(),
        can_update=can, run=_run(t, snap.run), log_tail=snap.log_tail, work=_work(_running(request)),
        restart=UpdateRestartOut(
            supervised=r.supervised, phase=r.phase, phase_label=t(f"update.phase.{r.phase}"), mode=r.mode,
            requested_at=r.requested_at, running=r.running))


def _running(request: Request) -> RunningWork:
    from storyagent.update import running_work

    return running_work(ctx_of(request).db)


def _mode(interrupt: bool) -> RestartMode:
    return "now" if interrupt else "wait"


@router.get("", response_model=UpdateOut)
async def update_state(request: Request) -> UpdateOut:
    """Version, commits waiting on the remote, what a restart would interrupt, the last run step by step."""
    _admin(request)
    return await anyio.to_thread.run_sync(out, request)


@router.post("/check", response_model=UpdateOut)
async def update_check(request: Request) -> UpdateOut:
    """``git fetch`` and compare; changes nothing in the checkout."""
    _admin(request)
    updater = updater_of(request)
    try:
        await anyio.to_thread.run_sync(updater.check)
    except UpdateError as exc:
        raise ApiError(exc.status, exc.code, exc.detail or None) from exc
    return await anyio.to_thread.run_sync(out, request)


@router.post("/run", response_model=UpdateOut, status_code=202)
async def update_run(body: UpdateRunBody, request: Request) -> UpdateOut:
    """Start the update (the steps of ``scripts/deploy.sh``). With ``restart``, and a supervisor, the worker and the
    server restart once everything passed: after the running work ends, or at once with ``interrupt``."""
    by = _admin(request)
    updater = updater_of(request)
    try:
        await anyio.to_thread.run_sync(lambda: updater.start(by=by, restart=body.restart, mode=_mode(body.interrupt)))
    except UpdateError as exc:
        raise ApiError(exc.status, exc.code, exc.detail or None) from exc
    return await anyio.to_thread.run_sync(out, request)


@router.post("/restart", response_model=UpdateOut)
async def update_restart(body: UpdateRestartBody, request: Request) -> UpdateOut:
    """Restart worker and server without updating (or, with ``interrupt``, stop waiting for the running work)."""
    by = _admin(request)
    updater = updater_of(request)
    try:
        await anyio.to_thread.run_sync(lambda: updater.request_restart(_mode(body.interrupt), by=by))
    except UpdateError as exc:
        raise ApiError(exc.status, exc.code, exc.detail or None) from exc
    return await anyio.to_thread.run_sync(out, request)


@router.delete("/restart", response_model=UpdateOut)
async def update_cancel_restart(request: Request) -> UpdateOut:
    """Withdraw a restart that has not happened yet; the worker takes work again."""
    by = _admin(request)
    updater = updater_of(request)
    try:
        await anyio.to_thread.run_sync(lambda: updater.cancel_restart(by=by))
    except UpdateError as exc:
        raise ApiError(exc.status, exc.code, exc.detail or None) from exc
    return await anyio.to_thread.run_sync(out, request)
