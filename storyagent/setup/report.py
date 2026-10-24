"""The guide's answer: what the machine is, which components of the chosen profile are there, what is missing and what
to do about it. Read-only; built from the catalog (``setup.toml``), the configuration and a ``Probe``.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from storyagent.config import Settings
from storyagent.config.setup import SetupComponent, SetupStep

from .detect import MISSING, OK, CheckResult, Detector, combine
from .layout import Layout, SetupError
from .probe import Probe

NEEDS = ("required", "recommended", "optional")


@dataclass(frozen=True)
class StepView:
    id: str
    component: str
    kind: str                   # command | download | manual
    argv: tuple[str, ...] = ()
    cwd: str = ""
    shell: str = ""             # the command as one copy-pasteable line (cd DIR && ...), or the manual `show` text
    note_key: str = ""
    source: str = ""
    file: str = ""
    size_gib: float | None = None
    needs_service: str = ""
    runnable: bool = False
    done: bool = False
    error: str = ""


@dataclass(frozen=True)
class ComponentView:
    name: str
    capability: str
    need: str
    state: str                  # ok | partial | missing | unverified
    label_key: str
    note_key: str
    size_gib: float
    measured: bool
    license: str
    url: str
    checks: tuple[CheckResult, ...]
    after: tuple[str, ...]
    blocked_by: tuple[str, ...]
    steps: tuple[StepView, ...]
    health: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityView:
    name: str
    need: str
    state: str
    label_key: str
    note_key: str
    components: tuple[str, ...]


@dataclass(frozen=True)
class MachineCheck:
    key: str                    # arch | system | macos | memory | disk
    state: str                  # ok | warn | fail
    params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    profile: str
    root: str
    machine: tuple[MachineCheck, ...]
    capabilities: tuple[CapabilityView, ...]
    components: tuple[ComponentView, ...]
    downloads: tuple[StepView, ...]         # missing weights of the required + recommended parts, to confirm first
    missing_gib: float                      # disk space everything missing needs (downloads and installs)
    download_gib: float                     # of which model files to download
    ready: bool
    health_required: tuple[str, ...]
    next_step: str = ""

    def component(self, name: str) -> ComponentView:
        return next(c for c in self.components if c.name == name)


def capability_needs(settings: Settings, profile: str, extra: tuple[str, ...] = ()) -> dict[str, str]:
    """``required`` / ``recommended`` / ``optional`` per capability for a profile (+ capabilities asked for by name)."""
    cfg = settings.setup
    try:
        prof = cfg.profiles[profile]
    except KeyError:
        raise SetupError("unknown_profile", profile) from None
    for cap in extra:
        if cap not in cfg.capabilities:
            raise SetupError("unknown_capability", cap)
    required = set(cfg.expand([*prof.required, *extra]))
    recommended = set(cfg.expand(list(prof.recommended))) - required
    return {c: "required" if c in required else "recommended" if c in recommended else "optional"
            for c in cfg.capabilities}


def _step_size(comp: SetupComponent, step: SetupStep, item: str) -> float | None:
    if step.size_gib is not None:
        return step.size_gib
    if item and item in comp.known_sizes:
        return comp.known_sizes[item]
    downloads = [s for s in comp.steps if s.kind == "download"]
    return comp.size_gib if len(downloads) == 1 and not step.foreach else None


def _resolve(comp: SetupComponent, step: SetupStep, layout: Layout, state: str, models: list[str],
             checks: list[CheckResult]) -> list[StepView]:
    items = models if step.foreach == "llm_models" else [""]
    views: list[StepView] = []
    for item in items:
        done = state == OK
        if step.foreach:
            done = any(c.label == item and c.state == OK for c in checks)
        base = dict(id=step.id if not step.foreach else f"{step.id}:{item}", component=comp.name, kind=step.kind,
                    note_key=step.note_key, size_gib=_step_size(comp, step, item), needs_service=step.needs_service,
                    runnable=step.runnable, done=done)
        try:
            argv = tuple(layout.expand(a, item=item) for a in step.argv)
            cwd = layout.expand(step.cwd, item=item) if step.cwd else ""
            shown = layout.expand(step.show, item=item) if step.show else ""
            source = layout.expand(step.source, item=item)
            file = layout.expand(step.file, item=item)
        except SetupError as exc:
            views.append(StepView(**base, error=exc.code))      # type: ignore[arg-type]
            continue
        line = shlex.join(argv) if argv else shown
        shell = f"cd {shlex.quote(cwd)} && {line}" if cwd and argv else line
        views.append(StepView(**base, argv=argv, cwd=cwd, shell=shell, source=source, file=file))  # type: ignore[arg-type]
    return views


def machine_checks(settings: Settings, probe: Probe, root: Path | None, caps: dict[str, str],
                   missing_gib: float) -> list[MachineCheck]:
    cfg = settings.setup
    m = cfg.machine
    info = probe.machine()
    out = [
        MachineCheck("arch", "ok" if info.arch == m.arch else "fail", {"value": info.arch, "want": m.arch}),
        MachineCheck("system", "ok" if info.system == m.system else "fail", {"value": info.system, "want": m.system}),
    ]
    if info.system == "Darwin" and m.min_macos:
        def ver(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split(".") if x.isdigit())
        ok = bool(info.macos) and ver(info.macos) >= ver(m.min_macos)
        out.append(MachineCheck("macos", "ok" if ok else "warn", {"value": info.macos or "?", "want": m.min_macos}))
    scope = [c for c, n in caps.items() if n != "optional"]
    rec = max((cfg.capabilities[c].rec_ram_gib for c in scope), default=0.0)
    least = max((cfg.capabilities[c].min_ram_gib for c in scope), default=0.0)
    if info.memory_gib > 0:
        state = "fail" if info.memory_gib < least else "warn" if info.memory_gib < rec else "ok"
        out.append(MachineCheck("memory", state, {"value": f"{info.memory_gib:.0f}", "min": f"{least:.0f}",
                                                  "rec": f"{rec:.0f}"}))
    target = root or Path(settings.home)
    free = probe.free_at(target)
    need = missing_gib + m.disk_margin_gib
    out.append(MachineCheck("disk", "ok" if free >= need else "warn",
                            {"free": f"{free:.0f}", "need": f"{need:.0f}", "path": str(target)}))
    return out


def build_report(settings: Settings, *, profile: str = "full", root: Path | None = None, extra: tuple[str, ...] = (),
                 probe: Probe | None = None) -> Report:
    probe = probe or Probe()
    cfg = settings.setup
    caps = capability_needs(settings, profile, extra)
    layout = Layout(settings, root)
    detector = Detector(settings, layout, probe)
    models = detector.llm_models()

    views: list[ComponentView] = []
    for comp in cfg.components.values():
        results = detector.component(comp)
        state = combine(results)
        steps: list[StepView] = []
        for step in comp.steps:
            steps.extend(_resolve(comp, step, layout, state, models, results))
        views.append(ComponentView(
            name=comp.name, capability=comp.capability, need=caps[comp.capability], state=state,
            label_key=comp.label_key, note_key=comp.note_key, size_gib=comp.size_gib, measured=comp.measured,
            license=comp.license, url=comp.url, checks=tuple(results), after=comp.after, blocked_by=(),
            steps=tuple(steps), health=comp.health))
    by_name = {v.name: v for v in views}
    views = [ComponentView(**{**v.__dict__, "blocked_by": tuple(a for a in v.after if by_name[a].state != "ok")})
             for v in views]

    in_scope = [v for v in views if v.need != "optional"]
    downloads = tuple(s for v in in_scope if v.state != "ok" for s in v.steps if s.kind == "download" and not s.done)
    download_gib = sum((s.size_gib or 0.0) for s in downloads)
    missing_gib = download_gib + sum(
        v.size_gib for v in in_scope if v.state in ("missing", "partial") and not any(
            s.kind == "download" for s in v.steps))
    machine = machine_checks(settings, probe, root, caps, missing_gib)

    cap_views = []
    for name, need in caps.items():
        mine = [v for v in views if v.capability == name]
        states = [v.state for v in mine]
        state = ("ok" if all(s == "ok" for s in states) else "missing" if all(s == "missing" for s in states)
                 else "unverified" if all(s in ("ok", "unverified") for s in states) else "partial")
        c = cfg.capabilities[name]
        cap_views.append(CapabilityView(name, need, state, c.label_key, c.note_key, tuple(v.name for v in mine)))

    todo = [v for v in in_scope if v.state in ("missing", "partial")]
    must = [v for v in todo if v.need == "required"]
    ordered = [*must, *todo]
    first = next((v for v in ordered if not v.blocked_by), ordered[0] if ordered else None)
    next_step = next((s.id for s in (first.steps if first else ()) if not s.done), "")
    health = tuple(dict.fromkeys(h for v in views if v.need == "required" for h in v.health))
    ready = not must and all(m.state != "fail" for m in machine)
    return Report(profile=profile, root=str(layout.root or ""), machine=tuple(machine), capabilities=tuple(cap_views),
                  components=tuple(views), downloads=downloads, missing_gib=round(missing_gib, 1),
                  download_gib=round(download_gib, 1), ready=ready,
                  health_required=health, next_step=next_step)


__all__ = ["CapabilityView", "ComponentView", "MachineCheck", "Report", "StepView", "build_report",
           "capability_needs", "MISSING"]
