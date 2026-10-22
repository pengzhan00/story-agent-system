"""Launch screen: which background services are up, what else is missing, and a button that starts them."""
from __future__ import annotations

from fastapi import APIRouter, Request

from storyagent.launcher import Launcher, ServiceState
from storyagent.llm.router import LLMRouter
from storyagent.media import healthcheck as health
from storyagent.media.broker import memory_snapshot
from storyagent.media.registry import default_registry

from .context import Translator, ctx_of, translator_of
from .errors import ApiError
from .schemas import LaunchIssue, LaunchOut, LaunchService, MemoryOut, StopBody

router = APIRouter(prefix="/api/launch", tags=["launch"])


def _service(t: Translator, s: ServiceState, required: set[str]) -> LaunchService:
    return LaunchService(
        name=s.name, url=s.url, state=s.state, label=t(f"launch.state.{s.state}"), startable=s.startable,
        stoppable=s.stoppable, bulk=s.bulk, required=s.name in required, detail=s.detail,
        hint=t(s.hint_key) if s.hint_key else "", pid=s.pid, elapsed_sec=s.elapsed_sec,
        timeout_sec=s.timeout_sec, log=s.log,
    )


def _model_issues(request: Request, t: Translator) -> list[LaunchIssue]:
    """Roles whose language model is not installed on a reachable backend: found now, not in the middle of a run."""
    settings = ctx_of(request).settings
    up = {s.name for s in Launcher(settings).statuses() if s.state in ("running", "probe_only")}
    router_ = LLMRouter(settings)
    issues: list[LaunchIssue] = []
    for role in settings.llm_roles:
        backend = settings.llm_roles[role].backend
        spec = settings.llm_backends.get(backend)
        if spec is None or (spec.service and spec.service not in up):
            continue
        try:
            router_.check_role(role)
        except Exception as exc:  # noqa: BLE001 - any failure of the probe is a finding, not a crash
            detail = getattr(exc, "detail", None) or str(exc)
            issues.append(LaunchIssue(name=f"llm.{role}", category="llm", state="missing_model",
                                      label=t("health.missing_model"), detail=str(detail), required=True))
    return issues


def report(request: Request) -> LaunchOut:
    ctx = ctx_of(request)
    t = translator_of(request)
    settings = ctx.settings
    required = set(settings.healthcheck_required)
    launcher = Launcher(settings)
    states = launcher.statuses()
    services = [_service(t, s, required) for s in states]
    checked = health.run_checks(settings, registry=default_registry(settings))
    issues = [
        LaunchIssue(name=e.name, category=e.category, state=e.state.value, label=t(f"health.{e.state.value}"),
                    detail=e.detail, required=e.name in required)
        for e in checked.entries
        if e.category != "service" and not e.ok and e.state.value != "disabled"
    ]
    issues += _model_issues(request, t)
    mem = memory_snapshot()
    swap_total, swap_used = mem["swap_total_gib"], mem["swap_used_gib"]
    memory = MemoryOut(**mem, swap_percent=round(100 * swap_used / swap_total) if swap_total and swap_used is not None
                       else None)
    blocking = [s for s in services if s.required and s.state != "running"]
    blocking_issues = [i for i in issues if i.required]
    return LaunchOut(
        memory=memory, services=services, issues=issues, ready=not blocking and not blocking_issues,
        starting=any(s.state == "starting" for s in services),
        startable=[s.name for s in services if s.startable and s.state in ("stopped", "failed")],
    )


@router.get("", response_model=LaunchOut)
def launch_state(request: Request) -> LaunchOut:
    """The services with their state, the problems found by the health checks, and whether the system is ready."""
    return report(request)


@router.post("/start-all", response_model=LaunchOut)
def start_all(request: Request) -> LaunchOut:
    """Start every startable service that is not up yet. A service that is up is left alone."""
    Launcher(ctx_of(request).settings).start_all()
    return report(request)


@router.post("/{name}/start", response_model=LaunchOut)
def start_one(name: str, request: Request) -> LaunchOut:
    Launcher(ctx_of(request).settings).start(name)
    return report(request)


@router.post("/{name}/stop", response_model=LaunchOut)
def stop_one(name: str, body: StopBody, request: Request) -> LaunchOut:
    """End the service's process (the operator's own, on the service's port) - only for services whose launch entry
    says ``stop = "port"``, and not while a job of that service is queued or running (``force`` overrides)."""
    ctx = ctx_of(request)
    launcher = Launcher(ctx.settings)
    if not body.force and ctx.settings.launch.get(name, {}).get("stop") == "port" and ctx.db.conn.execute(
            "SELECT 1 FROM media_jobs WHERE provider=? AND status IN ('queued','running') LIMIT 1",
            (name,)).fetchone():
        raise ApiError(409, "service_busy", name)
    pids = launcher.listeners(name)
    state = launcher.stop(name, force=body.force)
    user = request.scope.get("state", {}).get("user")
    ctx.auth.audit("service.stop", user=user, actor="local", path=request.url.path, status=200,
                   detail={"service": name, "force": body.force, "processes": [f"{p} {c}" for p, c in pids],
                           "state": state.state})
    return report(request)
