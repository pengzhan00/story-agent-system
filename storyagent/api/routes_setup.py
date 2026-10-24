"""Install guide: what this machine has, what is missing and how to install it, the configuration to write, a check.

Operator only (``/api/setup`` is an admin prefix). Reading never changes anything; the one write is the configuration
files in the operator's own config folder (the replaced ones are kept), on an explicit request. Nothing is downloaded,
installed or started here: the steps are shown, to be run in a terminal (``storyagent setup run``).
"""
from __future__ import annotations

from pathlib import Path

import anyio
from fastapi import APIRouter, Request

from storyagent.config import Settings
from storyagent.media import healthcheck as health
from storyagent.media.registry import default_registry
from storyagent.setup import configgen
from storyagent.setup.probe import Probe
from storyagent.setup.report import Report, StepView, build_report

from .context import Translator, ctx_of, translator_of
from .reloading import reload_now
from .schemas import (
    SetupCapabilityOut,
    SetupCheckOut,
    SetupComponentOut,
    SetupConfigBody,
    SetupConfigFileOut,
    SetupConfigOut,
    SetupConfigWritten,
    SetupMachineOut,
    SetupOut,
    SetupProfileOut,
    SetupStepOut,
    SetupVerifyEntry,
    SetupVerifyOut,
    SetupWritten,
)

router = APIRouter(prefix="/api/setup", tags=["setup"])


def _probe(request: Request) -> Probe:
    """The machine to look at (``app.state.setup_probe`` is the seam tests use to give it a made-up one)."""
    probe: Probe | None = getattr(request.app.state, "setup_probe", None)
    return probe or Probe()


def _step(s: StepView) -> SetupStepOut:
    return SetupStepOut(id=s.id, component=s.component, kind=s.kind, shell=s.shell, note_key=s.note_key,
                        source=s.source, file=s.file, size_gib=s.size_gib, needs_service=s.needs_service,
                        runnable=s.runnable, done=s.done, error=s.error)


def _out(settings: Settings, report: Report) -> SetupOut:
    cfg = settings.setup
    return SetupOut(
        profile=report.profile, root=report.root, default_root=cfg.machine.default_root,
        profiles=[SetupProfileOut(name=p.name, label_key=p.label_key, note_key=p.note_key,
                                  required=list(p.required), recommended=list(p.recommended))
                  for p in cfg.profiles.values()],
        capabilities_all=list(cfg.capabilities),
        machine=[SetupMachineOut(key=m.key, state=m.state, params=m.params) for m in report.machine],
        capabilities=[SetupCapabilityOut(name=c.name, need=c.need, state=c.state, label_key=c.label_key,
                                         note_key=c.note_key, components=list(c.components))
                      for c in report.capabilities],
        components=[SetupComponentOut(
            name=c.name, capability=c.capability, need=c.need, state=c.state, label_key=c.label_key,
            note_key=c.note_key, size_gib=c.size_gib, measured=c.measured, license=c.license, url=c.url,
            checks=[SetupCheckOut(label=k.label, state=k.state, detail=k.detail) for k in c.checks],
            after=list(c.after), blocked_by=list(c.blocked_by), steps=[_step(s) for s in c.steps])
            for c in report.components],
        downloads=[_step(s) for s in report.downloads], missing_gib=report.missing_gib,
        download_gib=report.download_gib, ready=report.ready,
        health_required=list(report.health_required), next_step=report.next_step)


def _args(profile: str, root: str, extra: str) -> tuple[str, Path | None, tuple[str, ...]]:
    return (profile or "full", Path(root).expanduser() if root else None,
            tuple(x for x in extra.split(",") if x))


@router.get("", response_model=SetupOut)
async def setup_state(request: Request, profile: str = "full", root: str = "", extra: str = "") -> SetupOut:
    """The checks of a profile, component by component, with the steps for what is missing."""
    settings = ctx_of(request).settings
    name, folder, more = _args(profile, root, extra)
    probe = _probe(request)
    report = await anyio.to_thread.run_sync(
        lambda: build_report(settings, profile=name, root=folder, extra=more, probe=probe))
    return _out(settings, report)


def _file(f: configgen.ConfigFile) -> SetupConfigFileOut:
    return SetupConfigFileOut(name=f.name, path=str(f.path), exists=f.exists, changed=f.changed,
                              changes=list(f.changes), text=f.text)


@router.post("/config", response_model=SetupConfigWritten)
async def setup_config(body: SetupConfigBody, request: Request) -> SetupConfigWritten:
    """The configuration this machine needs. ``write`` false = a preview; true writes the changed files (the file a
    write replaces is kept as ``<name>.bak-setup-<time>``) and loads them."""
    settings = ctx_of(request).settings
    folder = Path(body.root).expanduser() if body.root else None
    extra = tuple(body.extra)
    probe = _probe(request)

    def build() -> list[configgen.ConfigFile]:
        report = build_report(settings, profile=body.profile or "full", root=folder, extra=extra, probe=probe)
        return configgen.preview(settings, report, probe=probe, root=folder, extra=extra, llm_model=body.llm_model)

    files = await anyio.to_thread.run_sync(build)
    if body.write:
        await anyio.to_thread.run_sync(configgen.validate, settings, files)
    written = configgen.write(files) if body.write else []
    if written:
        await anyio.to_thread.run_sync(reload_now, request.app, request.app.state.reload_loader)
        ctx = ctx_of(request)
        ctx.auth.audit("setup.config", user=request.scope.get("state", {}).get("user"), actor="local",
                       path=request.url.path, status=200, detail={"files": [str(p) for p, _ in written]})
    return SetupConfigWritten(files=[_file(f) for f in files],
                              written=[SetupWritten(path=str(p), backup=str(b) if b else "") for p, b in written])


@router.get("/config", response_model=SetupConfigOut)
async def setup_config_preview(request: Request, profile: str = "full", root: str = "", extra: str = "",
                               llm_model: str = "") -> SetupConfigOut:
    settings = ctx_of(request).settings
    name, folder, more = _args(profile, root, extra)
    probe = _probe(request)

    def build() -> list[configgen.ConfigFile]:
        report = build_report(settings, profile=name, root=folder, extra=more, probe=probe)
        return configgen.preview(settings, report, probe=probe, root=folder, extra=more, llm_model=llm_model)

    return SetupConfigOut(files=[_file(f) for f in await anyio.to_thread.run_sync(build)])


@router.post("/verify", response_model=SetupVerifyOut)
async def setup_verify(request: Request, profile: str = "full", root: str = "", extra: str = "") -> SetupVerifyOut:
    """The healthcheck, with everything the profile needs counted as required."""
    settings = ctx_of(request).settings
    t: Translator = translator_of(request)
    name, folder, more = _args(profile, root, extra)
    probe = _probe(request)

    def run() -> SetupVerifyOut:
        report = build_report(settings, profile=name, root=folder, extra=more, probe=probe)
        checked = health.run_checks(settings, registry=default_registry(settings),
                                    require=list(report.health_required))
        entries = [SetupVerifyEntry(name=e.name, category=e.category, state=e.state.value,
                                    label=t(f"health.{e.state.value}"), detail=e.detail, ok=e.ok, required=e.required)
                   for e in checked.entries]
        return SetupVerifyOut(entries=entries, ok=checked.exit_code == 0)

    return await anyio.to_thread.run_sync(run)
