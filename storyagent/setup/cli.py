"""``storyagent setup``: the install guide on the command line.

    storyagent setup [--profile P] [--root DIR]        the guided walk: check, what is missing, config, verify
    storyagent setup profiles
    storyagent setup check [--profile P] [--root DIR] [--with CAP,...] [--json]
    storyagent setup plan  [...] [--all]                what is missing, with the commands and the download list
    storyagent setup config [...] [--llm-model NAME] [--write]
    storyagent setup run STEP [...] [--yes]             run ONE step after showing it
    storyagent setup verify [...] [--start]             the healthcheck for what the profile needs
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from storyagent.config import Settings
from storyagent.i18n import t
from storyagent.launcher import Launcher, LaunchError
from storyagent.media import healthcheck as health
from storyagent.media.registry import default_registry

from . import configgen
from .detect import Detector
from .layout import Layout, SetupError
from .probe import Probe
from .report import ComponentView, Report, StepView, build_report
from .runner import find_step, run_step

Ask = Callable[[str], str]


def make_probe() -> Probe:
    """The machine the guide looks at (tests replace this with a made-up one)."""
    return Probe()

STATE_MARK = {"ok": "ok", "partial": "~", "missing": "x", "unverified": "?"}


def add_arguments(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--profile", default="", help="what to set up (see `setup profiles`); default: full")
    sp.add_argument("--root", help="install new tools below this folder (default: where the configuration says)")
    sp.add_argument("--with", dest="extra", default="", help="more capabilities to count as required, comma-separated")


def _report(s: Settings, a: argparse.Namespace, probe: Probe | None = None) -> Report:
    extra = tuple(x for x in str(getattr(a, "extra", "") or "").split(",") if x)
    root = Path(a.root).expanduser() if getattr(a, "root", None) else None
    return build_report(s, profile=getattr(a, "profile", "") or "full", root=root, extra=extra,
                        probe=probe or make_probe())


def _pad(text: str, width: int) -> str:
    """Left-align ``text`` in ``width`` columns, counting wide (CJK) characters as two."""
    used = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(width - used, 0)


def _label(comp: ComponentView) -> str:
    return t(comp.label_key)


def _size(gib: float, measured: bool) -> str:
    if gib <= 0:
        return "-"
    return ("" if measured else "~") + (f"{gib:.1f} GiB" if gib >= 0.1 else f"{gib * 1024:.0f} MiB")


def _print_machine(report: Report) -> None:
    print(t("cli.setup.machine"))
    for m in report.machine:
        print(f"  [{m.state}] " + t(f"setup.machine.{m.key}.{m.state}", **m.params))


def print_check(report: Report) -> None:
    print(t("cli.setup.title", profile=t(f"setup.profile.{report.profile}"),
            root=report.root or t("cli.setup.no_root")))
    _print_machine(report)
    print(t("cli.setup.components"))
    print("  " + "  ".join([_pad(t("cli.setup.col.state"), 9), _pad(t("cli.setup.col.need"), 12),
                            _pad(t("cli.setup.col.component"), 36), t("cli.setup.col.size")]))
    for c in report.components:
        miss = [k.label for k in c.checks if k.state == "missing"]
        unknown = [k.label for k in c.checks if k.state == "unknown"]
        note = (" - " + t("cli.setup.missing", items=", ".join(miss))) if miss and c.state != "ok" else ""
        note += (" - " + t("cli.setup.unverified", items=", ".join(unknown))) if unknown else ""
        print(f"  {_pad(STATE_MARK[c.state], 9)}  {_pad(t('setup.need.' + c.need), 12)}  {_pad(_label(c), 36)}  "
              f"{_size(c.size_gib, c.measured)}{note}")
    print(t("cli.setup.ready") if report.ready else t("cli.setup.not_ready"))


def print_step(step: StepView, indent: str = "      ") -> None:
    if step.error:
        print(f"{indent}! {t('api.error.setup_' + step.error)}")
        return
    kind = t(f"setup.step.kind.{step.kind}")
    print(f"{indent}{step.id}  [{kind}]")
    if step.note_key:
        print(f"{indent}  {t(step.note_key)}")
    if step.shell:
        print(f"{indent}  $ {step.shell}")
    if step.kind == "download":
        size = _size(step.size_gib or 0, True)
        print(f"{indent}  {t('cli.setup.download', source=step.source, file=step.file, size=size)}")
    if step.needs_service:
        print(f"{indent}  {t('cli.setup.needs_service', name=step.needs_service)}")


def print_plan(report: Report, *, everything: bool = False) -> None:
    todo = [c for c in report.components if c.state != "ok" and (everything or c.need != "optional")]
    print(t("cli.setup.plan_title", profile=t(f"setup.profile.{report.profile}")))
    if not todo:
        print(t("cli.setup.nothing_to_do"))
    for n, c in enumerate(todo, 1):
        print(f"\n[{n}] {_label(c)}  ({t('setup.need.' + c.need)}, {t('setup.state.' + c.state)}, "
              f"{_size(c.size_gib, c.measured)}, {c.license})")
        print(f"    {t(c.note_key)}")
        if c.url:
            print(f"    {c.url}")
        if c.blocked_by:
            print("    " + t("cli.setup.after", items=", ".join(t(f"setup.c.{b}") for b in c.blocked_by)))
        for step in (s for s in c.steps if not s.done):
            print_step(step)
    if report.downloads:
        print("\n" + t("cli.setup.downloads", count=len(report.downloads), gib=f"{report.download_gib:.0f}"))
        for d in report.downloads:
            print(f"  - {d.file}  <- {d.source}  ({_size(d.size_gib or 0, True)})")
        print(t("cli.setup.nothing_downloaded"))


def print_config(files: list[configgen.ConfigFile], *, written: bool) -> None:
    print(t("cli.setup.config_title"))
    changed = [f for f in files if f.changed]
    if not changed:
        print(t("cli.setup.config_nothing"))
    for f in changed:
        how = t("cli.setup.config_merge") if f.exists else t("cli.setup.config_new")
        print(f"\n# {f.path}  ({how})")
        for line in f.changes:
            print(f"  + {line}")
    if changed and not written:
        print("\n" + t("cli.setup.config_not_written"))


def _verify(s: Settings, report: Report, *, start: bool) -> int:
    if start:
        launcher = Launcher(s)
        for name in report.health_required:
            if name in s.launch:
                try:
                    st = launcher.start(name)
                    print(t("cli.launch.started", name=name, state=t(f"launch.state.{st.state}")))
                except LaunchError as exc:
                    print(t("cli.launch.error", name=name, error=t(f"api.error.{exc.code}"), detail=exc.detail))
    checked = health.run_checks(s, registry=default_registry(s), require=list(report.health_required))
    print(health.format_report(checked))
    return int(checked.exit_code)


def cmd_profiles(s: Settings, _a: argparse.Namespace) -> int:
    for p in s.setup.profiles.values():
        print(f"{p.name:<12} {t(p.label_key)}")
        print(f"             {t(p.note_key)}")
        print("             " + t("cli.setup.profile_caps", required=", ".join(p.required),
                                  recommended=", ".join(p.recommended) or "-"))
    return 0


def cmd_check(s: Settings, a: argparse.Namespace) -> int:
    report = _report(s, a)
    if a.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print_check(report)
    return 0 if report.ready else 1


def cmd_plan(s: Settings, a: argparse.Namespace) -> int:
    print_plan(_report(s, a), everything=a.all)
    return 0


def cmd_config(s: Settings, a: argparse.Namespace) -> int:
    report = _report(s, a)
    root = Path(a.root).expanduser() if a.root else None
    files = configgen.preview(s, report, probe=make_probe(), root=root, llm_model=a.llm_model or "")
    if a.write:
        configgen.validate(s, files)
    done = configgen.write(files) if a.write else []
    print_config(files, written=bool(a.write))
    for path, backup in done:
        print(t("cli.setup.config_written", path=str(path)) + (
            " " + t("cli.setup.config_backup", path=str(backup)) if backup else ""))
    return 0


def cmd_run(s: Settings, a: argparse.Namespace, *, ask: Ask = input) -> int:
    report = _report(s, a)
    try:
        step = find_step(report, a.step)
        print_step(step, indent="")
        if step.kind == "download":
            print(t("cli.setup.confirm_download"))
        confirmed = bool(a.yes)
        if not confirmed and step.runnable and sys.stdin.isatty():
            confirmed = ask(t("cli.setup.confirm_prompt")).strip().lower() in ("y", "yes")
        probe = make_probe()
        code = run_step(report, a.step, probe=probe, service_up=Detector(s, Layout(s), probe).service_up,
                        confirmed=confirmed, extra_path=[p for p in (s.tool_path("mflux", "bin_dir"),) if p])
    except SetupError as exc:
        print(t(f"api.error.setup_{exc.code}") + (f" ({exc.detail})" if exc.detail else ""), file=sys.stderr)
        return 1
    print(t("cli.setup.step_done", id=a.step, code=code))
    return code


def cmd_verify(s: Settings, a: argparse.Namespace) -> int:
    return _verify(s, _report(s, a), start=a.start)


def cmd_guide(s: Settings, a: argparse.Namespace, *, ask: Ask = input) -> int:
    """The whole walk: check, plan, config, verify. Asks only when a person is at the keyboard."""
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not a.profile and interactive:
        names = list(s.setup.profiles)
        for i, n in enumerate(names, 1):
            print(f"  {i}. {n:<11} {t(s.setup.profiles[n].label_key)} - {t(s.setup.profiles[n].note_key)}")
        raw = ask(t("cli.setup.pick_profile", default="full")).strip()
        a.profile = names[int(raw) - 1] if raw.isdigit() and 0 < int(raw) <= len(names) else (
            raw if raw in names else "full")
    a.profile = a.profile or "full"
    report = _report(s, a)
    print_check(report)
    print()
    print_plan(report)
    root = Path(a.root).expanduser() if a.root else None
    files = configgen.preview(s, report, probe=make_probe(), root=root)
    write = bool(a.write_config)
    if write:
        configgen.validate(s, files)
    if not write and interactive and any(f.changed for f in files):
        print()
        print_config(files, written=False)
        write = ask(t("cli.setup.write_prompt")).strip().lower() in ("y", "yes")
        if write:
            configgen.validate(s, files)
        done = configgen.write(files) if write else []
    else:
        done = configgen.write(files) if write else []
        print()
        print_config(files, written=write)
    for path, backup in done:
        print(t("cli.setup.config_written", path=str(path)) + (
            " " + t("cli.setup.config_backup", path=str(backup)) if backup else ""))
    print()
    return _verify(s, report, start=False)
