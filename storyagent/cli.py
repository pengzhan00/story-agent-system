"""``storyagent`` command line.

    storyagent config show
    storyagent db migrate | status | import-legacy [--from PATH]
    storyagent i18n check
    storyagent project create NAME | list [--trashed] | trash ID [--restore]
    storyagent media healthcheck | submit | jobs | show | cancel | retry | prioritize | adopt
    storyagent spec create | show | edit | diff | revert | lock | unlock | regenerate
    storyagent serve [--host H] [--port P]
    storyagent worker [--once]
    storyagent workflow run | status | watch | review      (story pipeline with live progress bars)
    storyagent project create | estimate | update | list | trash   (kind of work, pipeline plan, seasons, episodes)
    storyagent export episode --project N [--episode 1] --out DIR   (film + subtitles + stems + provenance)
    storyagent content status --project N | adult --project N (--character NAME ... | --all)
    storyagent delivery platforms | make --project N [--episode 1] [--platform A,B] [--lang en,ja] --out DIR
    storyagent user create NAME --role R [--birth-date D] | list
    storyagent user set NAME [--role R] [--active|--inactive] [--reset-password]
    storyagent user age NAME [--reject] [--birth-date D] [--note T]   (approve / reject an account's age)
    storyagent audit [--user U] [--action A] [--project N] [--limit N]
    storyagent llm check
    storyagent api openapi [--out FILE]
    storyagent ops llm-unload
    storyagent ops stop NAME [--force]      (end a service listed with stop = "port" in services.toml)
    storyagent update status | check | run|apply [--restart] [--now]   (scripts/deploy.sh runs this)
    storyagent update marker --pid N [--clear]                     (scripts/start.sh says it supervises worker + server)
    storyagent setup [--profile P] [--root DIR] | profiles | check | plan | config [--write] | run STEP | verify

Commands that touch data migrate the database first (idempotent, snapshot taken before an upgrade).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import signal
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from storyagent.auth import AuthError, AuthStore
from storyagent.compose.export import ExportError, build_package
from storyagent.compose.interchange import InterchangeError
from storyagent.compose.qc import ADVISORY
from storyagent.config import ConfigError, Settings, get_settings
from storyagent.content import ContentError
from storyagent.db import Database, MigrationError, current_version, migrate
from storyagent.db.legacy_import import LegacyImportError, import_legacy
from storyagent.domain import JobStatus, MediaKind, QualityTier
from storyagent.i18n import available_locales, check_catalogs, set_locale, t
from storyagent.launcher import Launcher, LaunchError
from storyagent.logging_setup import setup_logging
from storyagent.media import healthcheck as health
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import Ledger, LedgerError
from storyagent.media.llm_resident import build_probes
from storyagent.media.pack import PackError
from storyagent.media.registry import AdapterRegistry, default_registry
from storyagent.media.specs import SpecError, SpecLocked, SpecStore
from storyagent.media.worker import Worker, run_once
from storyagent.progressbar import render_run, render_stages
from storyagent.projects import ProjectNotFound, ProjectStateError, ProjectStore
from storyagent.restart import (
    RESTART_EXIT_CODE,
    WorkerGate,
    clear_supervisor,
    read_supervisor,
    watch_worker,
    write_supervisor,
)
from storyagent.setup.layout import SetupError
from storyagent.update import UpdateError, Updater, repo_root, running_work
from storyagent.workflow import STAGES, WorkflowEngine, WorkflowState, WorkflowStateError

CONFIRM_HELP = "export a project of a restricted rating: confirms everyone shown is an adult and has consented"


class CliError(Exception):
    def __init__(self, key: str, code: int = 1, **params: object) -> None:
        super().__init__(key)
        self.key, self.code, self.params = key, code, params


@dataclass
class Ctx:
    settings: Settings
    db: Database
    ledger: Ledger
    specs: SpecStore


def _ctx(settings: Settings) -> Ctx:
    db = Database(settings.db_path)
    migrate(db)
    ledger = Ledger(db, settings)
    return Ctx(settings, db, ledger, SpecStore(db, settings, ledger))


def _kv(items: Sequence[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise CliError("cli.error.bad_kv", 2, item=item)
        try:
            out[key] = json.loads(raw)
        except ValueError:
            out[key] = raw
    return out


def _dump(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=lambda o: o.value if hasattr(o, "value") else str(o)))


# -- config / db / i18n ---------------------------------------------------------------------------
def _cmd_config_show(s: Settings, _a: argparse.Namespace) -> int:
    _dump({
        "home": str(s.home), "db_path": str(s.db_path), "locale": s.locale,
        "services": {n: c.url for n, c in s.services.items()},
        "default_models": dict(s.default_models), "llm_unload_policy": s.llm_unload_policy,
        "compliance": asdict(s.compliance), "strict_params": s.strict_params,
        "delivery_profile": s.delivery["default"]["profile"], "recipes": sorted(s.recipes),
        "pipelines": {n: {"default": n == s.default_pipeline, "frame": p.delivery_profile, "tier": p.tier,
                          "models": dict(p.models)} for n, p in s.pipelines.items()},
        "kinds": {n: {"default": n == s.default_kind, "pipeline": k.pipeline} for n, k in s.kinds.items()},
        "llm_roles": {n: f"{r.backend}:{r.model}" for n, r in s.llm_roles.items()},
    })
    return 0


def _cmd_db_migrate(s: Settings, _a: argparse.Namespace) -> int:
    before, after = migrate(Database(s.db_path))
    print(t("cli.db.up_to_date" if before == after else "cli.db.migrated", version=after))
    return 0


def _cmd_db_status(s: Settings, _a: argparse.Namespace) -> int:
    print(current_version(Database(s.db_path)))
    return 0


def _cmd_db_import(s: Settings, a: argparse.Namespace) -> int:
    src = Path(a.src).expanduser() if a.src else s.legacy_db_path
    if src is None:
        print(t("error.legacy.not_found", path="(--from / STORY_AGENT_LEGACY_DB)"), file=sys.stderr)
        return 2
    db = Database(s.db_path)
    migrate(db)
    report = import_legacy(src, db)
    print(t("cli.db.import_done", projects=report.projects, shots=report.shots))
    for note in report.notes:
        print(f"  - {note}", file=sys.stderr)
    return 0


def _cmd_i18n_check(_s: Settings, _a: argparse.Namespace) -> int:
    problems = check_catalogs()
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(t("cli.i18n.ok", count=len(available_locales())))
    return 0


# -- project ----------------------------------------------------------------------------------------
def _answers(a: argparse.Namespace) -> dict[str, Any]:
    """The creator's answers from the command line (anything not given takes the kind's default)."""
    given = {"seasons": a.seasons, "episodes_per_season": a.episodes, "episode_sec": a.episode_sec,
             "audience": a.audience, "platform": a.platform}
    return {k: v for k, v in given.items() if v not in (None, "")}


def _print_plan(p: Any) -> None:
    b = p.brief
    print(t("cli.project.plan", kind=p.kind or "-", pipeline=p.pipeline or "-", ratio=p.aspect_ratio,
            episodes=b.get("episodes_total", "-"), minutes=round(b.get("total_sec", 0) / 60, 1)))


def _cmd_project_create(s: Settings, a: argparse.Namespace) -> int:
    p = ProjectStore(_ctx(s).db, s).create(
        a.name, genre=a.genre or "", delivery_profile=a.profile or "", kind=a.kind or "", pipeline=a.pipeline or "",
        brief=_answers(a) or None, rating=a.rating or "")
    print(t("cli.project.created", id=p.id, slug=p.slug))
    _print_plan(p)
    return 0


def _cmd_project_estimate(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.brief import estimate, resolve_brief

    c = _ctx(s)
    brief = resolve_brief(s, a.kind or "", _answers(a))
    plan = ProjectStore(c.db, s).plan_of(a.pipeline or "", a.kind or "")
    e = estimate(s, c.ledger, brief, plan)
    print(t("cli.estimate.head", pipeline=e["pipeline"], tier=e["tier"], episodes=e["episodes"], shots=e["shots"]))
    for task in e["tasks"]:
        print(t("cli.estimate.row", kind=task["kind"], model=task["model_ref"], count=task["count"],
                each=task["each_sec"], total=round(task["total_sec"] / 60), rough="" if task["known"] else "~"))
    print(t("cli.estimate.total", hours=e["hours"]))
    if e["over_budget"]:
        print(t("cli.estimate.over_budget", hours=e["video_budget_hours"]), file=sys.stderr)
    return 0


def _cmd_project_update(s: Settings, a: argparse.Namespace) -> int:
    store = ProjectStore(_ctx(s).db, s)
    try:
        if a.kind or a.pipeline:
            store.update(a.project, **{k: v for k, v in (("kind", a.kind), ("pipeline", a.pipeline)) if v})
        if _answers(a):
            store.set_brief(a.project, _answers(a))
    except ProjectStateError as exc:
        raise CliError("cli.project.state", 1, detail=str(exc)) from exc
    _print_plan(store.get(a.project))
    return 0


def _cmd_project_list(s: Settings, a: argparse.Namespace) -> int:
    for p in ProjectStore(_ctx(s).db, s).list(trashed=a.trashed):
        print(t("cli.project.row", id=p.id, slug=p.slug, name=p.name, kind=p.kind or "-", pipeline=p.pipeline or "-"))
    return 0


def _cmd_project_trash(s: Settings, a: argparse.Namespace) -> int:
    store = ProjectStore(_ctx(s).db, s)
    try:
        project = store.restore(a.project) if a.restore else store.trash(a.project)
    except ProjectStateError as exc:
        raise CliError("cli.project.state", 1, detail=str(exc)) from exc
    print(t("cli.project.restored" if a.restore else "cli.project.trashed", id=project.id, name=project.name))
    return 0


def _password(a: argparse.Namespace, *, confirm: bool = True) -> str:
    """From the environment variable named by ``--password-env`` (scripts), else asked twice on the terminal."""
    if getattr(a, "password_env", None):
        value = os.environ.get(a.password_env, "")
        if not value:
            raise CliError("cli.user.password_env_empty", 2, name=a.password_env)
        return value
    first = getpass.getpass(t("cli.user.password_prompt"))
    if confirm and first != getpass.getpass(t("cli.user.password_again")):
        raise CliError("cli.user.password_mismatch", 2)
    return first


def _users(s: Settings) -> AuthStore:
    return AuthStore(_ctx(s).db, s)


def _cmd_user_create(s: Settings, a: argparse.Namespace) -> int:
    store = _users(s)
    user = store.create_user(a.username, _password(a), a.role, a.display_name or "", a.birth_date or "")
    store.audit("user.create", actor="cli", detail={"username": user.username, "role": user.role})
    print(t("cli.user.created", username=user.username, role=user.role))
    return 0


def _cmd_user_list(s: Settings, a: argparse.Namespace) -> int:
    for u in _users(s).list_users():
        print(t("cli.user.row", id=u.id, username=u.username, role=u.role,
                active=t("cli.user.active" if u.active else "cli.user.inactive"), last=u.last_login_at or "-",
                age=u.age if u.age is not None else "-", age_status=t(f"web.users.age_status.{u.age_status}")))
    return 0


def _cmd_user_age(s: Settings, a: argparse.Namespace) -> int:
    store = _users(s)
    user = store.find_user(a.username)
    if user is None:
        raise CliError("cli.user.unknown", 1, username=a.username)
    updated = store.review_age(user.id, approve=not a.reject, reviewer="cli", birth_date=a.birth_date or "",
                               note=a.note or "")
    store.audit("user.age_rejected" if a.reject else "user.age_approved", actor="cli",
                detail={"username": updated.username, "claim": user.age_claim, "birth_date": updated.birth_date,
                        "age": updated.age})
    print(t("cli.user.age_set", username=updated.username, status=t(f"web.users.age_status.{updated.age_status}"),
            age=updated.age if updated.age is not None else "-"))
    return 0


def _cmd_user_set(s: Settings, a: argparse.Namespace) -> int:
    store = _users(s)
    user = store.find_user(a.username)
    if user is None:
        raise CliError("cli.user.unknown", 1, username=a.username)
    active = True if a.active else False if a.inactive else None
    password = _password(a) if a.reset_password else None
    updated = store.update_user(user.id, role=a.role, active=active, display_name=a.display_name, password=password)
    changed = [k for k, v in (("role", a.role), ("active", active), ("display_name", a.display_name),
                              ("password", password)) if v is not None]
    store.audit("user.update", actor="cli", detail={"username": updated.username, "changed": changed})
    print(t("cli.user.updated", username=updated.username, role=updated.role))
    return 0


def _cmd_audit(s: Settings, a: argparse.Namespace) -> int:
    for r in _users(s).list_audit(user=a.user or "", action=a.action or "", project_id=a.project, limit=a.limit):
        print(t("cli.audit.row", at=r["at"], actor=r["actor"] or "-", action=r["action"], path=r["path"] or "-",
                status=r["status"] or "-"))
    return 0


def _cmd_serve(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.api.app import serve

    migrate(Database(s.db_path))
    print(t("cli.serve.starting", host=a.host or s.api["host"], port=a.port or s.api["port"]))
    return serve(s, a.host, a.port)           # RESTART_EXIT_CODE when a restart was requested (scripts/start.sh)


# -- media ------------------------------------------------------------------------------------------
def _cmd_media_health(s: Settings, a: argparse.Namespace) -> int:
    needs = [r for r in (a.require or "").split(",") if r]
    plan = a.pipeline or (ProjectStore(_ctx(s).db, s).get(a.project).pipeline if a.project else "")
    if plan:                                            # what the plan needs counts as required
        needs += s.pipeline(plan).requires
    report = health.run_checks(s, registry=default_registry(s), require=needs)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) if a.json else health.format_report(report))
    return report.exit_code


def _project_settings(c: Ctx, project_id: int | None) -> dict[str, Any]:
    if project_id is None:
        return {}
    row = c.db.conn.execute("SELECT settings FROM projects WHERE id=?", (project_id,)).fetchone()
    return json.loads(row["settings"]) if row else {}


def _cmd_media_submit(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    inputs = _kv(a.input or [])
    if a.prompt:
        inputs["prompt"] = a.prompt
    job = MediaJob(
        kind=MediaKind(a.kind), inputs=inputs, project_id=a.project, shot_id=a.shot, model_ref=a.model or "",
        tier=QualityTier(a.tier), slot=a.slot, params=_kv(a.param or []), seed=a.seed, priority=a.priority,
    )
    rec, created = c.ledger.enqueue(job, _project_settings(c, a.project))
    print(t("cli.job.enqueued", id=rec.id, state=t(f"status.{rec.status.value}")) if created
          else t("cli.job.deduplicated", id=rec.id))
    return 0


def _cmd_media_jobs(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    for j in c.ledger.list_jobs(project_id=a.project, status=a.status or None, limit=a.limit):
        print(t("cli.job.row", id=j.id, status=j.status.value, kind=j.kind.value, model=j.model_ref,
                progress=f"{j.progress:.0%}"))
    return 0


def _cmd_media_show(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    try:
        job = c.ledger.get_job(a.id)
    except LedgerError:
        raise CliError("cli.error.not_found", 1, what=a.id) from None
    assets = [x for x in c.ledger.list_assets(project_id=job.project_id) if x.job_id == job.id]
    _dump({"job": asdict(job), "events": c.ledger.events(job.id), "assets": [asdict(x) for x in assets]})
    return 0


def _cmd_media_cancel(s: Settings, a: argparse.Namespace) -> int:
    print(t("cli.job.cancel", status=t(f"status.{_ctx(s).ledger.request_cancel(a.id).value}")))
    return 0


def _cmd_media_retry(s: Settings, a: argparse.Namespace) -> int:
    print(t("cli.job.retried", id=_ctx(s).ledger.retry(a.id, new_seed=a.new_seed).id))
    return 0


def _cmd_media_prioritize(s: Settings, a: argparse.Namespace) -> int:
    try:
        move = "first" if a.first else "last" if a.last else None
        moved = _ctx(s).ledger.reprioritize(a.ids, move=move, priority=a.priority)
    except LedgerError as e:
        raise CliError("cli.error.job_not_movable", 1, why=str(e)) from None
    print(t("cli.job.prioritized", n=len(moved), priority=moved[0].priority))
    return 0


def _cmd_media_adopt(s: Settings, a: argparse.Namespace) -> int:
    print(t("cli.asset.adopted", id=_ctx(s).ledger.adopt(a.id).id))
    return 0


# -- uploads and the library ------------------------------------------------------------------------
def _cmd_asset_import(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.media.library import LibraryError, import_file

    c = _ctx(s)
    try:
        asset = import_file(s, c.ledger, Path(a.file).expanduser(), project_id=a.project, slot=a.slot)
    except LibraryError as exc:
        raise CliError("cli.error.library", 1, detail=str(exc)) from exc
    print(t("cli.asset.imported", id=asset.id, kind=asset.kind))
    return 0


# -- asset packs ------------------------------------------------------------------------------------
def _print_pack_report(report: Any) -> None:
    d = report.as_dict()
    print(t("cli.pack.dry_run" if d["dry_run"] else "cli.pack.done", project=d["project_id"] or "-"))
    for table, counts in d["story"].items():
        print(t("cli.pack.line", section=table, counts=_counts(counts)))
    for section in ("specs", "assets"):
        if d[section]:
            print(t("cli.pack.line", section=section, counts=_counts(d[section])))
    if d["unassigned"]:
        print(t("cli.pack.unassigned", names=", ".join(d["unassigned"])))
    for w in d["warnings"]:
        detail = ", ".join(f"{k}={v}" for k, v in w.items() if k != "code")
        print(t(f"pack.warn.{w['code']}", **{k: v for k, v in w.items() if k != "code"}) + f" ({detail})",
              file=sys.stderr)


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items())


def _cmd_pack_export(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.media.pack import export_pack

    c = _ctx(s)
    manifest = export_pack(s, c.db, a.project, Path(a.out).expanduser(), scope=a.scope,
                           include_history=a.include_history, confirm_restricted=a.confirm_restricted)
    print(t("cli.pack.exported", path=a.out, files=len(manifest["files"]),
            assets=manifest["counts"].get("assets", 0), specs=manifest["counts"].get("specs", 0)))
    for w in manifest["warnings"]:
        print(t(f"pack.warn.{w['code']}", **{k: v for k, v in w.items() if k != "code"}), file=sys.stderr)
    return 0


def _cmd_pack_import(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.media.pack import import_pack

    c = _ctx(s)
    report = import_pack(s, c.db, Path(a.source).expanduser(), into=a.into, policy=a.on_conflict, only=a.only,
                         dry_run=a.dry_run)
    _print_pack_report(report)
    return 0


def _cmd_pack_import_folder(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.media.pack import import_folder

    c = _ctx(s)
    report = import_folder(s, c.db, Path(a.folder).expanduser(), a.project, policy=a.on_conflict, dry_run=a.dry_run)
    _print_pack_report(report)
    return 0


# -- shot grammar -----------------------------------------------------------------------------------
def _grammar_line(v: dict[str, Any]) -> str:
    fix = ""
    if v["fix"]:
        fix = "  -> " + t(f"grammar.fix.{v['fix']['type']}", value=v["fix"]["value"])
    return t("cli.grammar.line", severity=v["severity"], episode=v["episode"], act=v["act"], scene=v["scene"],
             shot=v["shot"], message=t(f"grammar.msg.{v['rule']}", **v["params"]), fix=fix)


def _print_grammar(report: dict[str, Any]) -> None:
    for v in report["violations"]:
        print(_grammar_line(v))
    print(t("cli.grammar.summary", shots=report["shots"], unclassified=report["unclassified"], **report["counts"]))


def _cmd_grammar_check(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.story import StoryStore
    from storyagent.workflow.grammar import run_check

    c = _ctx(s)
    ProjectStore(c.db, s).get(a.project)
    report = run_check(StoryStore(c.db), a.project, s.grammar, a.episode).as_dict()
    _print_grammar(report)
    return 1 if report["counts"]["error"] else 0


def _cmd_grammar_fix(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.story import StoryStore
    from storyagent.workflow.grammar import apply_fixes

    c = _ctx(s)
    ProjectStore(c.db, s).get(a.project)
    fixed, left, after = apply_fixes(StoryStore(c.db), a.project, s.grammar, episode=a.episode,
                                     rules=a.rule or None)
    print(t("cli.grammar.fixed", n=len(fixed), left=len(left)))
    _print_grammar(after.as_dict())
    return 0


# -- Final Cut Pro interchange ---------------------------------------------------------------------
def _cmd_fcp_export(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.compose.interchange import build_fcp_package

    c = _ctx(s)
    try:
        manifest = build_fcp_package(s, c.ledger, a.project, a.episode, Path(a.out).expanduser(),
                                     absolute=a.absolute, confirm_restricted=a.confirm_restricted)
    except ExportError as exc:
        raise CliError("cli.export.failed", 1, detail=str(exc)) from exc
    print(t("cli.fcp.exported", path=a.out, files=len(manifest["files"])))
    return 0


def _cmd_fcp_diff(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.compose.interchange import diff_edit, parse_fcpxml

    package = Path(a.package).expanduser()
    manifest = json.loads((package / "interchange.json").read_text("utf-8"))
    diff = diff_edit(manifest, parse_fcpxml(Path(a.edited).expanduser().read_text("utf-8")))
    print(t("cli.fcp.diff_head", original=diff["original_sec"], edited=diff["edited_sec"], changed=diff["changed"]))
    for shot in diff["shots"]:
        if shot["status"] != "kept":
            print(t("cli.fcp.diff_shot", shot=shot["shot_id"], status=t(f"interchange.status.{shot['status']}"),
                    old=shot["duration"], new=shot["new_duration"]))
    for extra in diff["added"]:
        print(t("cli.fcp.diff_added", name=extra["name"] or extra["media"], seconds=extra["duration"]))
    return 0


def _cmd_fcp_final(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.compose.interchange import import_final

    c = _ctx(s)
    asset = import_final(s, c.ledger, a.project, a.episode, Path(a.file).expanduser(), note=a.note)
    print(t("cli.fcp.final", id=asset.id, version=asset.version))
    return 0


# -- picture artifact check -------------------------------------------------------------------------
def _cmd_qc_artifacts(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.compose.artifacts import measure
    from storyagent.media.probe import find_tool, probe

    path = Path(a.file).expanduser()
    ffmpeg = find_tool(s, "ffmpeg")
    info = probe(s, path) if path.is_file() else None
    if ffmpeg is None or info is None:
        raise CliError("cli.qc.unavailable", 1, path=str(path))
    qc = dict(s.delivery["qc"])
    for key in ("flicker_amp", "stripe_amp", "stripe_ratio"):
        value = getattr(a, key)
        if value is not None:
            qc[key] = value
    rep = measure(ffmpeg, path, qc, native_fps=info.fps)
    print(t("cli.qc.artifacts", frames=rep.frames, flicker=round(rep.flicker_sec, 2), banding=round(rep.banding_sec, 2),
            peak=round(rep.stripe_peak, 1)))
    for kind, windows in (("flicker", rep.flicker_windows), ("banding", rep.banding_windows)):
        for start, end in windows:
            print(t("cli.qc.window", kind=kind, start=round(start, 2), end=round(end, 2)))
    return 0


def _library(s: Settings) -> Any:
    from storyagent.media.library import Library

    c = _ctx(s)
    return Library(c.db, s, c.ledger)


def _library_meta(a: argparse.Namespace) -> dict[str, Any]:
    meta = _kv(a.meta or [])
    if a.text is not None:
        meta["text"] = a.text
    if a.consent:
        meta["consent"] = True
    return meta


def _cmd_library_add(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.media.library import LibraryError

    lib = _library(s)
    try:
        if a.asset:
            item = lib.publish(a.source, a.role, a.name, description=a.description, tags=a.tag or [],
                               meta=_library_meta(a), replace=a.replace)
        else:
            item = lib.add_file(Path(a.source).expanduser(), a.role, a.name, description=a.description,
                                tags=a.tag or [], meta=_library_meta(a), replace=a.replace)
    except LibraryError as exc:
        raise CliError("cli.error.library", 1, detail=str(exc)) from exc
    print(t("cli.library.added", ref=item.ref))
    return 0


def _cmd_library_import_aishell3(s: Settings, a: argparse.Namespace) -> int:
    import tempfile

    from storyagent import aishell
    from storyagent.media.library import LibraryError

    root = Path(a.dir).expanduser()
    if not (root / "spk-info.txt").is_file():
        raise CliError("cli.error.library", 1, detail=f"{root}/spk-info.txt")
    ages = tuple(x.strip().upper() for x in a.ages.split(",") if x.strip())
    genders = tuple(x.strip() for x in a.gender.split(",") if x.strip())
    try:
        with tempfile.TemporaryDirectory(prefix="aishell3-") as tmp:
            rows = aishell.import_speakers(
                s, _library(s), root, Path(tmp), ages=ages, genders=genders, only=tuple(a.speaker or ()),
                limit=a.limit, consent=a.consent, replace=a.replace, dry_run=a.dry_run)
    except LibraryError as exc:
        raise CliError("cli.error.library", 1, detail=str(exc)) from exc
    for r in rows:
        print("\t".join([r.speaker, r.name or "-", f"{r.seconds:g}s" if r.name else "", r.skipped or r.text]))
    made = sum(1 for r in rows if r.name)
    print(t("cli.library.aishell_dry" if a.dry_run else "cli.library.aishell_done", n=made,
            skipped=len(rows) - made))
    return 0


def _cmd_library_list(s: Settings, a: argparse.Namespace) -> int:
    for i in _library(s).list(role=a.role, tag=a.tag, q=a.q):
        print("\t".join([i.id, i.ref, ",".join(i.tags), i.description]))
    return 0


def _wiki(s: Settings) -> Any:
    from storyagent.wiki import WikiStore

    db = Database(s.db_path)
    migrate(db)
    return WikiStore(db, s)


def _cmd_wiki_sync(s: Settings, a: argparse.Namespace) -> int:
    report = _wiki(s).sync()
    print(t("cli.wiki.synced", added=report.added, updated=report.updated, unchanged=report.unchanged,
            removed=report.removed))
    for source, n in sorted(report.sources.items()):
        print(t("cli.wiki.source", source=source, n=n))
    for w in report.warnings:
        print(t(f"cli.wiki.warn.{w['code']}", source=w["source"], detail=w["detail"]))
    return 0


def _cmd_wiki_list(s: Settings, a: argparse.Namespace) -> int:
    store = _wiki(s)
    pages = store.all()
    if a.source:
        pages = [p for p in pages if p.source == a.source]
    if a.tag:
        pages = [p for p in pages if a.tag in p.tags]
    if a.q:
        found = {p.slug: snip for p, snip, _ in store.search(a.q, pages)}
        pages = [p for p in pages if p.slug in found]
    for p in pages:
        print("\t".join([p.slug, p.rating, p.title, ",".join(p.tags)]))
    return 0


def _cmd_wiki_show(s: Settings, a: argparse.Namespace) -> int:
    page = _wiki(s).get(a.slug)
    if page is None:
        raise CliError("cli.error.not_found", 1, what=a.slug)
    print(f"# {page.title}  [{page.slug}]\n")
    print(page.body)
    return 0


def _cmd_library_show(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.media.library import LibraryError

    try:
        item = _library(s).get(a.id)
    except LibraryError as exc:
        raise CliError("cli.error.library", 1, detail=str(exc)) from exc
    _dump(asdict(item))
    return 0


def _cmd_library_remove(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.media.library import LibraryError

    try:
        item = _library(s).remove(a.id)
    except LibraryError as exc:
        raise CliError("cli.error.library", 1, detail=str(exc)) from exc
    print(t("cli.library.removed", ref=item.ref))
    return 0


# -- specs ------------------------------------------------------------------------------------------
def _spec_fields(a: argparse.Namespace) -> dict[str, Any]:
    f: dict[str, Any] = {}
    for src, dst in (("prompt", "prompt"), ("negative", "negative_prompt"), ("text", "text_payload"),
                     ("model", "model_ref"), ("seed", "seed")):
        v = getattr(a, src, None)
        if v is not None:
            f[dst] = v
    if getattr(a, "param", None):
        f["params_patch"] = _kv(a.param)
    if getattr(a, "ref", None):
        f["refs"] = [json.loads(r) if r.startswith(("{", "[")) else r for r in a.ref]
    return f


def _cmd_spec_create(s: Settings, a: argparse.Namespace) -> int:
    f = _spec_fields(a)
    params = f.pop("params_patch", None)
    spec = _ctx(s).specs.create(
        a.project, a.subject_type, a.subject_id, MediaKind(a.kind), slot=a.slot, params=params, **f
    )
    print(t("cli.spec.created", id=spec.id, version=spec.version))
    return 0


def _cmd_spec_show(s: Settings, a: argparse.Namespace) -> int:
    st = _ctx(s).specs
    if a.history:
        for v in st.versions(a.project, a.subject_type, a.subject_id, a.slot):
            print(t("cli.spec.row", version=v.version, author=v.author, note=v.note))
        return 0
    cur = st.current(a.project, a.subject_type, a.subject_id, a.slot)
    if cur is None:
        raise CliError("cli.error.not_found", 1, what=f"{a.subject_type}/{a.subject_id}/{a.slot}")
    _dump({**asdict(cur), "locked": st.is_locked(a.project, a.subject_type, a.subject_id, a.slot)})
    return 0


def _cmd_spec_edit(s: Settings, a: argparse.Namespace) -> int:
    st = _ctx(s).specs
    before = st.current(a.project, a.subject_type, a.subject_id, a.slot)
    new = st.edit(a.project, a.subject_type, a.subject_id, a.slot, note=a.note or "", **_spec_fields(a))
    print(t("cli.spec.unchanged" if before and before.id == new.id else "cli.spec.edited", version=new.version))
    return 0


def _cmd_spec_diff(s: Settings, a: argparse.Namespace) -> int:
    st = _ctx(s).specs
    vs = {v.version: v for v in st.versions(a.project, a.subject_type, a.subject_id, a.slot)}
    try:
        d = SpecStore.diff(vs[a.v1], vs[a.v2])
    except KeyError:
        raise CliError("cli.error.not_found", 1, what=f"v{a.v1}/v{a.v2}") from None
    if not d:
        print(t("cli.spec.diff_empty"))
    else:
        _dump({k: {"from": o, "to": n} for k, (o, n) in d.items()})
    return 0


def _cmd_spec_revert(s: Settings, a: argparse.Namespace) -> int:
    new = _ctx(s).specs.revert_to(a.project, a.subject_type, a.subject_id, a.version, a.slot)
    print(t("cli.spec.edited", version=new.version))
    return 0


def _cmd_spec_lock(s: Settings, a: argparse.Namespace) -> int:
    _ctx(s).specs.lock(a.project, a.subject_type, a.subject_id, a.slot, a.note or "")
    print(t("cli.spec.locked"))
    return 0


def _cmd_spec_unlock(s: Settings, a: argparse.Namespace) -> int:
    _ctx(s).specs.unlock(a.project, a.subject_type, a.subject_id, a.slot)
    print(t("cli.spec.unlocked"))
    return 0


def _cmd_spec_regen(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    from storyagent.story import StoryStore
    from storyagent.workflow.media import job_preparer

    pset = _project_settings(c, a.project)
    jobs = c.specs.regenerate(
        a.project, a.subject_type, a.subject_id, a.slot, n_candidates=a.n, vary_seed=not a.same_seed,
        tier=QualityTier(a.tier), shot_id=a.shot, project_settings=pset,
        prepare=job_preparer(s, StoryStore(c.db), c.specs, c.ledger, dict(pset or {})),
    )
    print(t("cli.spec.regenerated", count=len(jobs)))
    for j in jobs:
        print(j.id)
    return 0


# -- update -----------------------------------------------------------------------------------------
def _commit_line(c: Any) -> str:
    return f"{c.short} {c.subject}" if c else "-"


def _updater(s: Settings) -> Updater:
    sup = read_supervisor(s)
    return Updater(s, parent_pid=sup.pid if sup else None, audit=lambda action, by, detail: AuthStore(
        Database(s.db_path), s).audit(action, actor=by or "local", detail=detail))


def _cmd_update_status(s: Settings, _a: argparse.Namespace) -> int:
    u = _updater(s)
    i = u.inspect()
    if not i.available.enabled:
        print(t("cli.update.disabled", reason=t(f"update.reason.{i.available.reason}")))
    print(t("cli.update.current", commit=_commit_line(i.current), branch=i.branch or "-"))
    print(t("cli.update.behind", count=i.behind_count, ref=i.remote_ref))
    for c in i.behind:
        print(f"  {_commit_line(c)}")
    if not i.clean:
        print(t("cli.update.dirty", count=len(i.dirty)))
    work = running_work(_ctx(s).db)
    print(t("cli.update.running", jobs=len(work.jobs), runs=len(work.runs), queued=work.queued))
    print(t("cli.update.supervised" if u.is_supervised() else "cli.update.unsupervised"))
    return 0


def _cmd_update_check(s: Settings, a: argparse.Namespace) -> int:
    try:
        _updater(s).check()
    except UpdateError as exc:
        print(t(f"api.error.{exc.code}") + (f" ({exc.detail})" if exc.detail else ""), file=sys.stderr)
        return 1
    return _cmd_update_status(s, a)


def _cmd_update_run(s: Settings, a: argparse.Namespace) -> int:
    u = _updater(s)
    try:
        run = u.start(by="cli", restart=a.restart, mode="now" if a.now else "wait")
    except UpdateError as exc:
        print(t(f"api.error.{exc.code}") + (f" ({exc.detail})" if exc.detail else ""), file=sys.stderr)
        return 1
    log_path, seen = Path(run.log_file), 0
    while True:
        done = not u.busy()
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        for line in lines[seen:]:
            print(line)
        seen = len(lines)
        if done:
            break
        time.sleep(0.5)
    u.wait()
    state = u.last_run
    status = state.status if state else "failed"
    print(t(f"update.run.{status}"))
    if state and state.restart == "requested":
        print(t("cli.update.restart_requested", mode=state.restart_mode))
    elif state and status == "succeeded":
        print(t("cli.update.restart_manual"))
    return 0 if status in ("succeeded", "current") else 1


def _cmd_update_marker(s: Settings, a: argparse.Namespace) -> int:
    """Used by scripts/start.sh: tell the API which process supervises worker and server."""
    if a.clear:
        clear_supervisor(s, a.pid)
    else:
        write_supervisor(s, a.pid, repo_root())
    return 0


# -- worker / ops -----------------------------------------------------------------------------------
def _cmd_worker(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    worker = Worker(s, c.db, default_registry(s), ledger=c.ledger)
    if a.once:
        run_once(worker, timeout=a.timeout)
        print(t("cli.worker.done"))
        return 0
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    print(t("cli.worker.started", owner=worker.owner))

    def reread() -> tuple[Settings, AdapterRegistry]:
        fresh = get_settings(reload=True)
        return fresh, default_registry(fresh)

    gate = WorkerGate(s, worker)
    watch_worker(gate, stop)
    worker.run_forever(stop, reload=reread)
    if gate.leaving:
        print(t("cli.worker.restarting"))
        return RESTART_EXIT_CODE           # scripts/start.sh starts worker and server again
    return 0


def _cmd_api_openapi(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.api.app import create_app

    text = json.dumps(create_app(s).openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_llm_unload(s: Settings, _a: argparse.Namespace) -> int:
    unloaded: list[str] = []
    for p in build_probes(s):
        unloaded += p.unload(p.resident())
    print(t("cli.llm.unloaded", models=", ".join(unloaded)) if unloaded else t("cli.llm.none"))
    return 0


def _cmd_launch(s: Settings, a: argparse.Namespace) -> int:
    """Show the services and start the ones that are needed (`--all`) or one by name."""
    launcher = Launcher(s)
    if a.all or a.name:
        try:
            if a.all:
                for name, outcome, detail in launcher.start_all():
                    if outcome in ("started", "already"):
                        print(t(f"cli.launch.{outcome}", name=name, state=t(f"launch.state.{detail}")))
                    else:
                        print(t("cli.launch.error", name=name, error=t(f"api.error.{outcome}"), detail=detail))
            else:
                st = launcher.start(a.name)
                print(t("cli.launch.started", name=st.name, state=t(f"launch.state.{st.state}")))
        except LaunchError as exc:
            print(t("cli.launch.error", name=a.name or "", error=t(f"api.error.{exc.code}"), detail=exc.detail),
                  file=sys.stderr)
            return 1
    print(t("cli.launch.header"))
    for st in launcher.statuses():
        print(t("cli.launch.row", name=st.name, state=t(f"launch.state.{st.state}"), detail=st.detail))
    report = health.run_checks(s, registry=default_registry(s))
    for e in report.entries:
        if e.category != "service" and not e.ok and e.state.value != "disabled":
            print(t("cli.launch.issue", name=e.name, label=t(f"health.{e.state.value}"), detail=e.detail))
    required_down = [st for st in launcher.statuses() if st.name in s.healthcheck_required and st.state != "running"]
    print(t("cli.launch.ready") if not required_down and not report.failing_required else t("cli.launch.not_ready"))
    return 0 if not required_down else 1


def _cmd_stop(s: Settings, a: argparse.Namespace) -> int:
    """End the process of a service that the operator listed with `stop = "port"` (SIGTERM; `--force` = SIGKILL)."""
    try:
        st = Launcher(s).stop(a.name, force=a.force)
    except LaunchError as exc:
        print(t("cli.launch.error", name=a.name, error=t(f"api.error.{exc.code}"), detail=exc.detail), file=sys.stderr)
        return 1
    print(t("cli.launch.stopped", name=st.name, state=t(f"launch.state.{st.state}")))
    return 0


# -- tools: connect outside software ----------------------------------------------------------------
def _cmd_tool_import_comfy(s: Settings, a: argparse.Namespace) -> int:
    """Read a ComfyUI workflow saved in API format, show what was bound, and (unless --dry-run) save it."""
    from storyagent.connect.comfy_import import ImportFailed, import_workflow
    from storyagent.connect.store import ConnectError, save_comfy

    try:
        data = json.loads(Path(a.file).expanduser().read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise CliError("cli.error.unreadable", 1, what=a.file, detail=str(exc)) from exc
    try:
        result = import_workflow(data, a.kind)
    except ImportFailed as exc:
        raise CliError(f"connect.error.{exc.code}", 1, **exc.params) from exc
    for d in result.detected:
        where = ", ".join(f"{n}.{i}" for n, i in d.targets)
        print(t("cli.tool.bound", name=d.name, where=where, value="" if d.value is None else d.value))
    for w in result.warnings:
        print(t(f"connect.warn.{w.code}", **w.params), file=sys.stderr)
    out = {"output": result.template["output"], "type": json.dumps(result.template["output_type"])}
    print(t("cli.tool.output", **out))
    if a.dry_run:
        print(t("cli.tool.dry_run"))
        return 0
    name = a.name or Path(a.file).stem.lower().replace(" ", "-")
    try:
        saved = save_comfy(s, name=name, ref=a.ref or name, kind=a.kind, result=result, family=a.family,
                           heavy=a.heavy, timeout_sec=a.timeout, force=a.force)
    except ConnectError as exc:
        raise CliError(f"connect.error.{exc.code}", 1, **exc.params) from exc
    print(t("cli.tool.saved", workflow=str(saved.workflow_path), models=str(saved.models_path), ref=a.ref or name))
    return 0


# -- story workflow ---------------------------------------------------------------------------------
def _show_run(state: WorkflowState, run_id: str, interval: float) -> dict[str, Any]:
    """Draw the run's progress bar until it ends (a worker - or ``--inline`` - is doing the work)."""
    tty = sys.stdout.isatty()
    last = ""
    while True:
        run = state.get_run(run_id)
        line = render_run(run)
        if tty:
            print("\r\033[K" + line, end="", flush=True)
        elif line != last:
            print(line, flush=True)
        last = line
        if run["status"] not in ("queued", "running"):
            if tty:
                print()
            return run
        time.sleep(interval)


def _finish_message(run: dict[str, Any]) -> int:
    print(t("cli.workflow.done", id=run["id"][:8], status=t(f"workflow.status.{run['status']}")))
    if run["status"] == "failed":
        code = str(run.get("error_code") or "internal")
        print("  " + (t(f"{code.replace('.', '.error.', 1)}") if "." in code else code), file=sys.stderr)
        print("  " + str(run.get("error_message") or ""), file=sys.stderr)
        return 1
    return 0 if run["status"] == "completed" else 1


def _cmd_export_episode(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    project = ProjectStore(c.db, s).get(a.project)
    try:
        manifest = build_package(s, c.ledger, a.project, a.episode, Path(a.out), dict(s.delivery_profile(
            project.delivery_profile)), confirm_restricted=a.confirm_restricted)
    except ExportError as exc:
        raise CliError("cli.export.failed", 1, detail=str(exc)) from exc
    failed = [x["code"] for x in manifest["qc"]["checks"] if x["ok"] is False and x["code"] not in ADVISORY]
    print(t("cli.export.done", path=a.out, files=len(manifest["files"])))
    if failed:
        print(t("cli.export.qc_failed", checks=", ".join(failed)), file=sys.stderr)
    return 0 if not failed else 6


def _cmd_delivery_platforms(s: Settings, a: argparse.Namespace) -> int:
    for name, p in s.platforms.items.items():
        mark = "*" if name == s.platforms.default else " "
        print(t("cli.delivery.platform", mark=mark, name=name, label=t(p.label_key), width=p.width, height=p.height,
                fps=f"{p.fps:g}", reframe=p.reframe, lufs=f"{p.loudnorm_i:g}"))
    return 0


def _cmd_delivery_make(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.compose.delivery import DeliveryError, deliver

    c = _ctx(s)
    project = ProjectStore(c.db, s).get(a.project)
    langs = [x.strip() for x in (a.lang or "").split(",") if x.strip()]
    engine = WorkflowEngine(s, c.db, spec_store=c.specs)
    names = [x.strip() for x in (a.platform or "").split(",") if x.strip()] or [s.platforms.default]
    code = 0
    for name in names:
        try:
            record = deliver(s, c.ledger, a.project, a.episode, name, Path(a.out), languages=langs,
                             content_language=project.content_language, confirm_restricted=a.confirm_restricted,
                             translate=lambda lines, lang: engine.translate_lines(a.project, lines, lang))
        except DeliveryError as exc:
            print(t("cli.delivery.failed", platform=name, detail=t(f"delivery.error.{exc.code}", detail=exc.detail)),
                  file=sys.stderr)
            code = 1
            continue
        res = record["result"]
        print(t("cli.delivery.done", platform=name, path=Path(a.out) / name, files=len(record["files"]),
                size=res["size_mb"], lufs="-" if res["loudness_lufs"] is None else f"{res['loudness_lufs']:g}"))
        for w in record["warnings"]:
            print("  ! " + t(f"delivery.warn.{w}"), file=sys.stderr)
    return code


def _cmd_content_status(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.content import effective_rating, vault_ready

    c = _ctx(s)
    p = ProjectStore(c.db, s).get(a.project)
    print(t("cli.content.status", project=p.name, rating=effective_rating(s, c.db, a.project),
            vault=str(vault_ready(s)).lower()))
    return 0


def _cmd_content_adult(s: Settings, a: argparse.Namespace) -> int:
    """Record that a character is an adult (the project's first hard gate); the declaration is audited."""
    c = _ctx(s)
    ProjectStore(c.db, s).get(a.project)
    names = set(a.character or [])
    rows = c.db.conn.execute("SELECT id, name FROM characters WHERE project_id=?", (a.project,)).fetchall()
    chosen = [r for r in rows if a.all or r["name"] in names]
    for r in chosen:
        c.db.conn.execute("UPDATE characters SET adult_confirmed=1 WHERE id=?", (r["id"],))
    _users(s).audit("content.adult_confirmed", actor="cli", project_id=a.project,
                 detail={"characters": [r["name"] for r in chosen]})
    print(t("cli.content.adult", n=len(chosen)))
    return 0


def _cmd_workflow_review(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    try:
        review = WorkflowEngine(s, c.db, spec_store=c.specs).review_script(a.project, a.episode)
    except WorkflowStateError as exc:
        raise CliError("cli.review.none", 1, detail=str(exc)) from exc
    print(t("cli.review.score", score=review.get("score", "?")), "-", review.get("summary", ""))
    for issue in review.get("issues", []):
        print(t("cli.review.issue", severity=issue.get("severity", ""), where=issue.get("where", ""),
                problem=issue.get("problem", ""), fix=issue.get("fix", "")))
    return 0


def _cmd_workflow_run(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    state = WorkflowState(c.db)
    ProjectStore(c.db, s).get(a.project)
    unknown = [x for x in a.stage or [] if x not in STAGES]
    if unknown:
        raise CliError("cli.error.bad_kv", 2, item=",".join(unknown))
    params = _kv(a.param or [])
    try:
        plan = state.plan(a.project, a.stage, params)
    except WorkflowStateError as exc:
        raise CliError("cli.error.bad_kv", 2, item=str(exc)) from exc
    if not plan:
        print(t("cli.workflow.nothing"))
        return 0
    if not a.stage:
        params["resume"] = True
    stages = [x for x in STAGES if any(x == name for _, name in plan)]
    if a.force:
        params["force"] = True
    run = state.enqueue(a.project, stages, params)
    print(t("cli.workflow.queued", id=run["id"][:8], stages=", ".join(t(f"workflow.stage.{x}") for x in stages)))
    if a.inline:
        engine = WorkflowEngine(s, c.db, spec_store=c.specs)
        claimed = state.claim_next(f"cli:{run['id'][:6]}", 60.0)
        if claimed is None:
            raise CliError("cli.workflow.nothing", 1)
        runner = threading.Thread(target=engine.execute_run, args=(claimed, f"cli:{run['id'][:6]}", 60.0, 1.0),
                                  daemon=True)
        runner.start()
        final = _show_run(state, run["id"], a.interval)
        runner.join()
        return _finish_message(final)
    if a.no_wait:
        return 0
    return _finish_message(_show_run(state, run["id"], a.interval))


def _cmd_workflow_status(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    state = WorkflowState(c.db)
    ProjectStore(c.db, s).get(a.project)
    runs = state.list_runs(a.project, 1)
    if runs and runs[0]["status"] in ("queued", "running"):
        print(render_run(runs[0]))
        print("\n".join(render_stages(runs[0])))
    for st in state.stages(a.project, a.episode):
        print(f"{t('workflow.stage.' + st['stage']):<14} {t('workflow.status.' + st['status'])}"
              + (f"  ({st['error_code']})" if st["error_code"] else ""))
    return 0


def _cmd_workflow_watch(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    state = WorkflowState(c.db)
    run_id = a.run
    if run_id is None:
        runs = state.list_runs(a.project, 1) if a.project else []
        if not runs:
            raise CliError("cli.workflow.nothing", 1)
        run_id = runs[0]["id"]
    try:
        return _finish_message(_show_run(state, run_id, a.interval))
    except WorkflowStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _cmd_llm_roles(s: Settings, a: argparse.Namespace) -> int:
    """Every role with the backend and model it uses now, and where that choice comes from."""
    from storyagent.llm import LLMRouter, overrides

    c = _ctx(s)
    router = LLMRouter(s, c.db)
    scope = overrides.project_scope(a.project) if a.project is not None else overrides.GLOBAL
    chosen = overrides.listing(c.db, scope)
    for role in sorted(s.llm_roles):
        backend, model, _ = router.resolve(role, project_id=a.project)
        print(f"{role}\t{backend}\t{model}\t{'chosen' if role in chosen else 'config'}")
    return 0


def _cmd_llm_set(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.llm import overrides

    if a.role not in s.llm_roles or a.backend not in s.llm_backends:
        print(t("cli.llm.unknown", role=a.role, backend=a.backend), file=sys.stderr)
        return 2
    c = _ctx(s)
    scope = overrides.project_scope(a.project) if a.project is not None else overrides.GLOBAL
    overrides.put(c.db, scope, a.role, a.backend, a.model)
    print(t("cli.llm.set", role=a.role, backend=a.backend, model=a.model))
    return 0


def _cmd_llm_unset(s: Settings, a: argparse.Namespace) -> int:
    from storyagent.llm import overrides

    c = _ctx(s)
    scope = overrides.project_scope(a.project) if a.project is not None else overrides.GLOBAL
    overrides.remove(c.db, scope, a.role)
    print(t("cli.llm.unset", role=a.role))
    return 0


def _cmd_llm_check(s: Settings, _a: argparse.Namespace) -> int:
    from storyagent.llm import LLMError, LLMRouter

    c = _ctx(s)
    router = LLMRouter(s, c.db)
    bad = 0
    for role in sorted(s.llm_roles):
        backend, model, _ = router.resolve(role)
        try:
            router.check_role(role)
            print(t("cli.llm.ok", role=role, backend=backend, model=model))
        except LLMError as exc:
            bad += 1
            problem = t(f"llm.error.{exc.code}", **{k: str(v) for k, v in exc.params.items()})
            print(t("cli.llm.bad", role=role, backend=backend, model=model, problem=problem))
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="storyagent")
    sub = p.add_subparsers(dest="group", required=True)

    def add(group: Any, name: str, fn: Any, **kw: Any) -> argparse.ArgumentParser:
        sp = group.add_parser(name, **kw)
        sp.set_defaults(func=fn)
        return sp

    def subject_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--project", type=int, required=True)
        sp.add_argument("subject_type")
        sp.add_argument("subject_id")
        sp.add_argument("--slot", default="main")

    def spec_fields(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--prompt")
        sp.add_argument("--negative")
        sp.add_argument("--text", help="dialogue line / lyrics")
        sp.add_argument("--model")
        sp.add_argument("--seed", type=int)
        sp.add_argument("--param", action="append", metavar="K=V")
        sp.add_argument("--ref", action="append")

    cfg = sub.add_parser("config").add_subparsers(dest="cmd", required=True)
    add(cfg, "show", _cmd_config_show)

    db = sub.add_parser("db").add_subparsers(dest="cmd", required=True)
    add(db, "migrate", _cmd_db_migrate)
    add(db, "status", _cmd_db_status)
    add(db, "import-legacy", _cmd_db_import).add_argument("--from", dest="src", help="v1 story_agents.db (read-only)")

    add(sub.add_parser("i18n").add_subparsers(dest="cmd", required=True), "check", _cmd_i18n_check)

    pr = sub.add_parser("project").add_subparsers(dest="cmd", required=True)
    pc = add(pr, "create", _cmd_project_create)
    pc.add_argument("name")
    pc.add_argument("--genre")
    pc.add_argument("--rating", help="content rating (content.toml), decided now; default: the unrestricted one")
    pc.add_argument("--profile", help="delivery profile = the picture frame (vertical / landscape), fixed at the start")

    def brief_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--kind", help="kind of work (drama_series, advert ...): recommends the pipeline plan")
        sp.add_argument("--pipeline", help="pipeline plan: one complete set of models (default: the kind's)")
        sp.add_argument("--seasons", type=int)
        sp.add_argument("--episodes", type=int, help="episodes per season")
        sp.add_argument("--episode-sec", type=float, help="target length of one episode in seconds")
        sp.add_argument("--audience")
        sp.add_argument("--platform")

    brief_args(pc)
    pe = add(pr, "estimate", _cmd_project_estimate)
    brief_args(pe)
    pu = add(pr, "update", _cmd_project_update)
    pu.add_argument("project", type=int)
    brief_args(pu)
    pl = add(pr, "list", _cmd_project_list)
    pl.add_argument("--trashed", action="store_true", help="list the recycle bin")
    pt = add(pr, "trash", _cmd_project_trash)
    pt.add_argument("project", type=int)
    pt.add_argument("--restore", action="store_true", help="bring a trashed project back")

    us = sub.add_parser("user").add_subparsers(dest="cmd", required=True)
    uc = add(us, "create", _cmd_user_create)
    uc.add_argument("username")
    uc.add_argument("--role", required=True)
    uc.add_argument("--display-name")
    uc.add_argument("--birth-date", help="YYYY-MM-DD; given here the age counts at once")
    uc.add_argument("--password-env", help="read the password from this environment variable (else it is asked)")
    add(us, "list", _cmd_user_list)
    uset = add(us, "set", _cmd_user_set)
    uset.add_argument("username")
    uset.add_argument("--role")
    uset.add_argument("--display-name")
    uset.add_argument("--active", action="store_true")
    uset.add_argument("--inactive", action="store_true")
    uset.add_argument("--reset-password", action="store_true")
    uset.add_argument("--password-env")
    ua = add(us, "age", _cmd_user_age)
    ua.add_argument("username")
    ua.add_argument("--reject", action="store_true", help="reject the age (default: approve)")
    ua.add_argument("--birth-date", help="approve this birth date instead of the claimed one")
    ua.add_argument("--note")
    au = add(sub, "audit", _cmd_audit)
    au.add_argument("--user")
    au.add_argument("--action")
    au.add_argument("--project", type=int)
    au.add_argument("--limit", type=int, default=50)

    me = sub.add_parser("media").add_subparsers(dest="cmd", required=True)
    hc = add(me, "healthcheck", _cmd_media_health)
    hc.add_argument("--json", action="store_true")
    hc.add_argument("--require", default="")
    hc.add_argument("--pipeline", help="what this pipeline plan needs counts as required")
    hc.add_argument("--project", type=int, help="the same, for the plan of this project")
    ms = add(me, "submit", _cmd_media_submit)
    ms.add_argument("--kind", required=True, choices=[k.value for k in MediaKind])
    ms.add_argument("--model")
    ms.add_argument("--project", type=int)
    ms.add_argument("--shot", type=int)
    ms.add_argument("--tier", default="final", choices=[x.value for x in QualityTier])
    ms.add_argument("--slot", default="main")
    ms.add_argument("--prompt")
    ms.add_argument("--input", action="append", metavar="K=V")
    ms.add_argument("--param", action="append", metavar="K=V")
    ms.add_argument("--seed", type=int)
    ms.add_argument("--priority", type=int, default=5)
    mj = add(me, "jobs", _cmd_media_jobs)
    mj.add_argument("--project", type=int)
    mj.add_argument("--status", action="append", choices=[x.value for x in JobStatus])
    mj.add_argument("--limit", type=int, default=50)
    add(me, "show", _cmd_media_show).add_argument("id")
    add(me, "cancel", _cmd_media_cancel).add_argument("id")
    mp = add(me, "prioritize", _cmd_media_prioritize)
    mp.add_argument("ids", nargs="+")
    where = mp.add_mutually_exclusive_group(required=True)
    where.add_argument("--first", action="store_true")
    where.add_argument("--last", action="store_true")
    where.add_argument("--priority", type=int)
    mr = add(me, "retry", _cmd_media_retry)
    mr.add_argument("id")
    mr.add_argument("--new-seed", action="store_true")
    add(me, "adopt", _cmd_media_adopt).add_argument("id")

    asset = sub.add_parser("asset").add_subparsers(dest="cmd", required=True)
    ai = add(asset, "import", _cmd_asset_import)
    ai.add_argument("file")
    ai.add_argument("--project", type=int, required=True)
    ai.add_argument("--slot", default="main")

    pk = sub.add_parser("pack").add_subparsers(dest="cmd", required=True)
    pe = add(pk, "export", _cmd_pack_export)
    pe.add_argument("--project", type=int, required=True)
    pe.add_argument("--out", required=True, help="a new directory, or a .zip file")
    pe.add_argument("--scope", choices=["all", "assets", "story"], default="all")
    pe.add_argument("--include-history", action="store_true", help="also the earlier takes and spec versions")
    pe.add_argument("--confirm-restricted", action="store_true", help=CONFIRM_HELP)
    pi = add(pk, "import", _cmd_pack_import)
    pi.add_argument("source", help="a pack directory or .zip")
    pi.add_argument("--into", type=int, help="project to import into (default: a new project from the manifest)")
    pi.add_argument("--only", choices=["all", "assets", "story"], default="all")
    pi.add_argument("--on-conflict", choices=["skip", "new_version"], default="skip")
    pi.add_argument("--dry-run", action="store_true")
    pf = add(pk, "import-folder", _cmd_pack_import_folder)
    pf.add_argument("folder", help="plain files, mapped to slots by file name (config: [runtime] loose_rules)")
    pf.add_argument("--project", type=int, required=True)
    pf.add_argument("--on-conflict", choices=["skip", "new_version"], default="skip")
    pf.add_argument("--dry-run", action="store_true")

    gr = sub.add_parser("grammar").add_subparsers(dest="cmd", required=True)
    gc = add(gr, "check", _cmd_grammar_check)
    gc.add_argument("--project", type=int, required=True)
    gc.add_argument("--episode", type=int)
    gf = add(gr, "fix", _cmd_grammar_fix)
    gf.add_argument("--project", type=int, required=True)
    gf.add_argument("--episode", type=int)
    gf.add_argument("--rule", action="append", help="only this rule (repeatable)")

    fc = sub.add_parser("fcp").add_subparsers(dest="cmd", required=True)
    fe = add(fc, "export", _cmd_fcp_export)
    fe.add_argument("--project", type=int, required=True)
    fe.add_argument("--episode", type=int, default=1)
    fe.add_argument("--out", required=True, help="a new folder: episode.fcpxml + media/")
    fe.add_argument("--absolute", action="store_true", help="file:// URLs of this folder instead of relative paths")
    fe.add_argument("--confirm-restricted", action="store_true", help=CONFIRM_HELP)
    fd = add(fc, "diff", _cmd_fcp_diff)
    fd.add_argument("package", help="the folder written by `fcp export`")
    fd.add_argument("edited", help="the FCPXML exported back from Final Cut Pro")
    ff = add(fc, "final", _cmd_fcp_final)
    ff.add_argument("file", help="the finished film (.mp4/.mov)")
    ff.add_argument("--project", type=int, required=True)
    ff.add_argument("--episode", type=int, default=1)
    ff.add_argument("--note", default="")

    qq = sub.add_parser("qc").add_subparsers(dest="cmd", required=True)
    qa = add(qq, "artifacts", _cmd_qc_artifacts)
    qa.add_argument("file", help="a video: an episode or one generated clip")
    qa.add_argument("--flicker-amp", dest="flicker_amp", type=float)
    qa.add_argument("--stripe-amp", dest="stripe_amp", type=float)
    qa.add_argument("--stripe-ratio", dest="stripe_ratio", type=float)

    wiki_cmds = sub.add_parser("wiki").add_subparsers(dest="cmd", required=True)
    add(wiki_cmds, "sync", _cmd_wiki_sync)
    wiki_ls = add(wiki_cmds, "list", _cmd_wiki_list)
    wiki_ls.add_argument("--source")
    wiki_ls.add_argument("--tag")
    wiki_ls.add_argument("-q")
    add(wiki_cmds, "show", _cmd_wiki_show).add_argument("slug")

    li = sub.add_parser("library").add_subparsers(dest="cmd", required=True)
    la = add(li, "add", _cmd_library_add)
    la.add_argument("source", help="a file path, or an asset id with --asset")
    la.add_argument("--asset", action="store_true", help="source is an asset id (publish it)")
    la.add_argument("--role", required=True)
    la.add_argument("--name", required=True)
    la.add_argument("--description", default="")
    la.add_argument("--tag", action="append")
    la.add_argument("--meta", action="append", metavar="K=V")
    la.add_argument("--text", help="voice: what the reference clip says")
    la.add_argument("--consent", action="store_true", help="voice: the speaker agreed to cloning")
    la.add_argument("--replace", action="store_true")
    lx = add(li, "import-aishell3", _cmd_library_import_aishell3)
    lx.add_argument("dir", help="the extracted corpus folder (the one that holds spk-info.txt)")
    lx.add_argument("--ages", default="B,C,D",
                    help="age groups to take: B 14-25, C 26-40, D over 40 (A, under 14, never)")
    lx.add_argument("--speaker", action="append", metavar="SSBxxxx", help="only these speakers (repeatable)")
    lx.add_argument("--gender", default="female,male")
    lx.add_argument("--limit", type=int, default=0, help="at most this many speakers (0 = all)")
    lx.add_argument("--consent", action="store_true",
                    help="record that the speakers agreed to cloning (the corpus licence alone does not say so)")
    lx.add_argument("--replace", action="store_true")
    lx.add_argument("--dry-run", action="store_true", help="list what would be imported, change nothing")
    ll = add(li, "list", _cmd_library_list)
    ll.add_argument("--role")
    ll.add_argument("--tag")
    ll.add_argument("-q")
    add(li, "show", _cmd_library_show).add_argument("id")
    add(li, "remove", _cmd_library_remove).add_argument("id")

    sp = sub.add_parser("spec").add_subparsers(dest="cmd", required=True)
    sc = add(sp, "create", _cmd_spec_create)
    subject_args(sc)
    sc.add_argument("--kind", required=True, choices=[k.value for k in MediaKind])
    spec_fields(sc)
    ss = add(sp, "show", _cmd_spec_show)
    subject_args(ss)
    ss.add_argument("--history", action="store_true")
    se = add(sp, "edit", _cmd_spec_edit)
    subject_args(se)
    spec_fields(se)
    se.add_argument("--note")
    sd = add(sp, "diff", _cmd_spec_diff)
    subject_args(sd)
    sd.add_argument("v1", type=int)
    sd.add_argument("v2", type=int)
    sv = add(sp, "revert", _cmd_spec_revert)
    subject_args(sv)
    sv.add_argument("version", type=int)
    sl = add(sp, "lock", _cmd_spec_lock)
    subject_args(sl)
    sl.add_argument("--note")
    subject_args(add(sp, "unlock", _cmd_spec_unlock))
    sg = add(sp, "regenerate", _cmd_spec_regen)
    subject_args(sg)
    sg.add_argument("-n", type=int, default=1)
    sg.add_argument("--tier", default="final", choices=[x.value for x in QualityTier])
    sg.add_argument("--shot", type=int)
    sg.add_argument("--same-seed", action="store_true")

    sv_ = add(sub, "serve", _cmd_serve)
    sv_.add_argument("--host")
    sv_.add_argument("--port", type=int)

    wk = add(sub, "worker", _cmd_worker)
    wk.add_argument("--once", action="store_true", help="process the queue, then exit")
    wk.add_argument("--timeout", type=float, default=3600.0)

    wf = sub.add_parser("workflow").add_subparsers(dest="cmd", required=True)
    wp = add(wf, "run", _cmd_workflow_run)
    wp.add_argument("--project", type=int, required=True)
    wp.add_argument("--stage", action="append", choices=list(STAGES), help="repeat; omit to resume the project")
    wp.add_argument("--param", action="append", metavar="K=V", help="e.g. episode=2, source_text=..., acts=3")
    wp.add_argument("--force", action="store_true", help="redo stages even if their output exists")
    wp.add_argument("--inline", action="store_true", help="run here instead of waiting for a worker")
    wp.add_argument("--no-wait", action="store_true", help="queue it and return")
    wp.add_argument("--interval", type=float, default=1.0)
    wp = add(wf, "review", _cmd_workflow_review)
    wp.add_argument("--project", type=int, required=True)
    wp.add_argument("--episode", type=int, default=1)
    wp = add(wf, "status", _cmd_workflow_status)
    wp.add_argument("--project", type=int, required=True)
    wp.add_argument("--episode", type=int, default=1)
    wp = add(wf, "watch", _cmd_workflow_watch)
    wp.add_argument("run", nargs="?")
    wp.add_argument("--project", type=int)
    wp.add_argument("--interval", type=float, default=1.0)
    ex = sub.add_parser("export").add_subparsers(dest="cmd", required=True)
    wp = add(ex, "episode", _cmd_export_episode)
    wp.add_argument("--project", type=int, required=True)
    wp.add_argument("--episode", type=int, default=1)
    wp.add_argument("--out", required=True, help="directory to write the package into")
    wp.add_argument("--confirm-restricted", action="store_true", help=CONFIRM_HELP)
    ct = sub.add_parser("content").add_subparsers(dest="cmd", required=True)
    wp = add(ct, "status", _cmd_content_status)
    wp.add_argument("--project", type=int, required=True)
    wp = add(ct, "adult", _cmd_content_adult)
    wp.add_argument("--project", type=int, required=True)
    wp.add_argument("--character", action="append", help="a character name (repeat for several)")
    wp.add_argument("--all", action="store_true", help="every character of the project")
    dl = sub.add_parser("delivery").add_subparsers(dest="cmd", required=True)
    add(dl, "platforms", _cmd_delivery_platforms)
    wp = add(dl, "make", _cmd_delivery_make)
    wp.add_argument("--project", type=int, required=True)
    wp.add_argument("--episode", type=int, default=1)
    wp.add_argument("--platform", help="comma-separated platform names (default: the configured default)")
    wp.add_argument("--lang", help="comma-separated subtitle languages to translate into, e.g. en,ja")
    wp.add_argument("--out", required=True, help="directory to write <platform>/ folders into")
    wp.add_argument("--confirm-restricted", action="store_true", help=CONFIRM_HELP)
    llm = sub.add_parser("llm").add_subparsers(dest="cmd", required=True)
    add(llm, "check", _cmd_llm_check)
    wp = add(llm, "roles", _cmd_llm_roles)
    wp.add_argument("--project", type=int, help="the choices of this project (default: every project)")
    wp = add(llm, "set", _cmd_llm_set)
    wp.add_argument("role")
    wp.add_argument("backend")
    wp.add_argument("model")
    wp.add_argument("--project", type=int, help="only for this project (default: every project)")
    wp = add(llm, "unset", _cmd_llm_unset)
    wp.add_argument("role")
    wp.add_argument("--project", type=int)

    from storyagent.setup import cli as setup_cli

    su = sub.add_parser("setup", help="new-machine install guide")
    setup_cli.add_arguments(su)
    su.add_argument("--write-config", action="store_true", help="write the configuration without asking")
    su.set_defaults(func=setup_cli.cmd_guide, write=False)
    sus = su.add_subparsers(dest="cmd")
    add(sus, "profiles", setup_cli.cmd_profiles)
    sc = add(sus, "check", setup_cli.cmd_check)
    setup_cli.add_arguments(sc)
    sc.add_argument("--json", action="store_true")
    sp_ = add(sus, "plan", setup_cli.cmd_plan)
    setup_cli.add_arguments(sp_)
    sp_.add_argument("--all", action="store_true", help="also the optional parts")
    scf = add(sus, "config", setup_cli.cmd_config)
    setup_cli.add_arguments(scf)
    scf.add_argument("--llm-model", help="language model for every role (an Ollama model name)")
    scf.add_argument("--write", action="store_true",
                     help="write the files (a replaced one is kept as .bak-setup-*)")
    sr = add(sus, "run", setup_cli.cmd_run)
    setup_cli.add_arguments(sr)
    sr.add_argument("step", help="step id from `setup plan`, e.g. uv.1")
    sr.add_argument("--yes", action="store_true", help="you have read the step above and confirm it")
    sv = add(sus, "verify", setup_cli.cmd_verify)
    setup_cli.add_arguments(sv)
    sv.add_argument("--start", action="store_true",
                    help="first start the [launch.*] services that the profile needs")

    tool = sub.add_parser("tool").add_subparsers(dest="cmd", required=True)
    timp = tool.add_parser("import").add_subparsers(dest="source", required=True)
    tc = add(timp, "comfy", _cmd_tool_import_comfy)
    tc.add_argument("file", help="a workflow saved with ComfyUI's Save (API Format)")
    tc.add_argument("--kind", required=True, choices=[k.value for k in MediaKind if not k.value.startswith(
        ("text.", "compose.", "train."))])
    tc.add_argument("--name", help="template file name (default: the file's name)")
    tc.add_argument("--ref", help="model ref in models.toml (default: --name)")
    tc.add_argument("--family", default="", help="model family (pairs image.generate with image.edit, ...)")
    tc.add_argument("--heavy", action="store_true", help="one at a time with the other heavy jobs")
    tc.add_argument("--timeout", type=int, default=1800, help="seconds one job may take")
    tc.add_argument("--dry-run", action="store_true", help="only show what would be bound")
    tc.add_argument("--force", action="store_true", help="replace a template / model entry of the same name")

    api = sub.add_parser("api").add_subparsers(dest="cmd", required=True)
    wp = add(api, "openapi", _cmd_api_openapi)
    wp.add_argument("--out", help="write the OpenAPI document here (web/openapi.json feeds `pnpm gen:api`)")

    up = sub.add_parser("update").add_subparsers(dest="cmd", required=True)
    add(up, "status", _cmd_update_status)
    add(up, "check", _cmd_update_check)
    ur = add(up, "run", _cmd_update_run, aliases=["apply"])
    ur.add_argument("--restart", action="store_true", help="then restart worker and server (needs scripts/start.sh)")
    ur.add_argument("--now", action="store_true", help="with --restart: hand running work back at once, do not wait")
    um = add(up, "marker", _cmd_update_marker)
    um.add_argument("--pid", type=int, required=True, help="the pid of scripts/start.sh")
    um.add_argument("--clear", action="store_true")

    ops = sub.add_parser("ops").add_subparsers(dest="cmd", required=True)
    add(ops, "llm-unload", _cmd_llm_unload)
    ost = add(ops, "stop", _cmd_stop)
    ost.add_argument("name")
    ost.add_argument("--force", action="store_true", help="SIGKILL instead of SIGTERM")
    launch = add(ops, "launch", _cmd_launch)
    launch.add_argument("name", nargs="?", default="", help="service to start (default: only show the state)")
    launch.add_argument("--all", action="store_true", help="start every startable service that is not up")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
        set_locale(settings.locale)
        setup_logging(settings.logs_dir)
        return int(args.func(settings, args))
    except ConfigError as exc:
        print(t("error.config.invalid", detail=str(exc)), file=sys.stderr)
        return 2
    except MigrationError as exc:
        print(t("error.db.migration_failed", detail=str(exc)), file=sys.stderr)
        return 3
    except LegacyImportError as exc:
        print(t(exc.key, **exc.params), file=sys.stderr)
        return 4
    except SpecLocked:
        print(t("cli.spec.is_locked"), file=sys.stderr)
        return 5
    except InterchangeError as exc:
        print(t(f"interchange.error.{exc.code}", **exc.params), file=sys.stderr)
        return 1
    except ContentError as exc:
        key = "cli.content.blocked" if exc.code == "export_blocked" else f"content.error.{exc.code}"
        print(t(key, **exc.params), file=sys.stderr)
        return 1
    except PackError as exc:
        print(t(f"pack.error.{exc.code}", **exc.params), file=sys.stderr)
        return 1
    except AuthError as exc:
        print(t(f"api.error.{exc.code}") + (f" ({exc.detail})" if exc.detail else ""), file=sys.stderr)
        return 1
    except (SpecError, LedgerError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ProjectNotFound as exc:
        print(t("cli.error.project_not_found", id=str(exc)), file=sys.stderr)
        return 1
    except SetupError as exc:
        print(t(f"api.error.setup_{exc.code}") + (f" ({exc.detail})" if exc.detail else ""), file=sys.stderr)
        return 1
    except CliError as exc:
        print(t(exc.key, **exc.params), file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
