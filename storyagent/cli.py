"""``storyagent`` command line.

    storyagent config show
    storyagent db migrate | status | import-legacy [--from PATH]
    storyagent i18n check
    storyagent project create NAME | list
    storyagent media healthcheck | submit | jobs | show | cancel | retry | adopt
    storyagent spec create | show | edit | diff | revert | lock | unlock | regenerate
    storyagent worker [--once]
    storyagent ops llm-unload

Commands that touch data migrate the database first (idempotent, snapshot taken before an upgrade).
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from storyagent.config import ConfigError, Settings, get_settings
from storyagent.db import Database, MigrationError, current_version, migrate
from storyagent.db.legacy_import import LegacyImportError, import_legacy
from storyagent.domain import JobStatus, MediaKind, QualityTier, slugify
from storyagent.i18n import available_locales, check_catalogs, set_locale, t
from storyagent.logging_setup import setup_logging
from storyagent.media import healthcheck as health
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import Ledger, LedgerError
from storyagent.media.llm_resident import build_probes
from storyagent.media.registry import default_registry
from storyagent.media.specs import SpecError, SpecLocked, SpecStore
from storyagent.media.worker import Worker, run_once


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
def _cmd_project_create(s: Settings, a: argparse.Namespace) -> int:
    c = _ctx(s)
    base = slugify(a.name)
    slug, n = base, 2
    while c.db.conn.execute("SELECT 1 FROM projects WHERE slug=?", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    cur = c.db.conn.execute("INSERT INTO projects(slug, name, genre) VALUES (?,?,?)", (slug, a.name, a.genre or ""))
    print(t("cli.project.created", id=cur.lastrowid, slug=slug))
    return 0


def _cmd_project_list(s: Settings, _a: argparse.Namespace) -> int:
    for r in _ctx(s).db.conn.execute("SELECT id, slug, name FROM projects ORDER BY id"):
        print(t("cli.project.row", id=r["id"], slug=r["slug"], name=r["name"]))
    return 0


# -- media ------------------------------------------------------------------------------------------
def _cmd_media_health(s: Settings, a: argparse.Namespace) -> int:
    report = health.run_checks(
        s, registry=default_registry(s), require=[r for r in (a.require or "").split(",") if r]
    )
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


def _cmd_media_adopt(s: Settings, a: argparse.Namespace) -> int:
    print(t("cli.asset.adopted", id=_ctx(s).ledger.adopt(a.id).id))
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
    jobs = c.specs.regenerate(
        a.project, a.subject_type, a.subject_id, a.slot, n_candidates=a.n, vary_seed=not a.same_seed,
        tier=QualityTier(a.tier), shot_id=a.shot, project_settings=_project_settings(c, a.project),
    )
    print(t("cli.spec.regenerated", count=len(jobs)))
    for j in jobs:
        print(j.id)
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
    worker.run_forever(stop)
    return 0


def _cmd_llm_unload(s: Settings, _a: argparse.Namespace) -> int:
    unloaded: list[str] = []
    for p in build_probes(s):
        unloaded += p.unload(p.resident())
    print(t("cli.llm.unloaded", models=", ".join(unloaded)) if unloaded else t("cli.llm.none"))
    return 0


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
    add(pr, "list", _cmd_project_list)

    me = sub.add_parser("media").add_subparsers(dest="cmd", required=True)
    hc = add(me, "healthcheck", _cmd_media_health)
    hc.add_argument("--json", action="store_true")
    hc.add_argument("--require", default="")
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
    mr = add(me, "retry", _cmd_media_retry)
    mr.add_argument("id")
    mr.add_argument("--new-seed", action="store_true")
    add(me, "adopt", _cmd_media_adopt).add_argument("id")

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

    wk = add(sub, "worker", _cmd_worker)
    wk.add_argument("--once", action="store_true", help="process the queue, then exit")
    wk.add_argument("--timeout", type=float, default=3600.0)

    ops = sub.add_parser("ops").add_subparsers(dest="cmd", required=True)
    add(ops, "llm-unload", _cmd_llm_unload)
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
    except (SpecError, LedgerError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except CliError as exc:
        print(t(exc.key, **exc.params), file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
