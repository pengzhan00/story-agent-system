"""Launch screen: which background services are up, what else is missing, and a button that starts them."""
from __future__ import annotations

from fastapi import APIRouter, Request

from storyagent.launcher import Launcher, ServiceState
from storyagent.llm.router import LLMRouter
from storyagent.media import healthcheck as health
from storyagent.media.registry import default_registry

from .context import Translator, ctx_of, translator_of
from .schemas import LaunchIssue, LaunchOut, LaunchService

router = APIRouter(prefix="/api/launch", tags=["launch"])


def _service(t: Translator, s: ServiceState, required: set[str]) -> LaunchService:
    return LaunchService(
        name=s.name, url=s.url, state=s.state, label=t(f"launch.state.{s.state}"), startable=s.startable, bulk=s.bulk,
        required=s.name in required, detail=s.detail, hint=t(s.hint_key) if s.hint_key else "", pid=s.pid,
        elapsed_sec=s.elapsed_sec, timeout_sec=s.timeout_sec, log=s.log,
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
    blocking = [s for s in services if s.required and s.state != "running"]
    blocking_issues = [i for i in issues if i.required]
    return LaunchOut(
        services=services, issues=issues, ready=not blocking and not blocking_issues,
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
