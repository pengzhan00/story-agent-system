"""``storyagent`` command line.

    storyagent config show
    storyagent db migrate | status | import-legacy [--from PATH]
    storyagent i18n check
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from storyagent.config import ConfigError, Settings, get_settings
from storyagent.db import Database, MigrationError, current_version, migrate
from storyagent.db.legacy_import import LegacyImportError, import_legacy
from storyagent.i18n import available_locales, check_catalogs, set_locale, t
from storyagent.logging_setup import setup_logging


def _settings_view(s: Settings) -> dict[str, object]:
    return {
        "home": str(s.home),
        "db_path": str(s.db_path),
        "locale": s.locale,
        "services": {n: c.url for n, c in s.services.items()},
        "default_models": dict(s.default_models),
        "ollama_unload_policy": s.ollama_unload_policy,
        "delivery_profile": s.delivery["default"]["profile"],
    }


def _cmd_config_show(s: Settings, _args: argparse.Namespace) -> int:
    print(json.dumps(_settings_view(s), ensure_ascii=False, indent=2))
    return 0


def _cmd_db_migrate(s: Settings, _args: argparse.Namespace) -> int:
    before, after = migrate(Database(s.db_path))
    key, ver = ("cli.db.up_to_date", after) if before == after else ("cli.db.migrated", after)
    print(t(key, version=ver))
    return 0


def _cmd_db_status(s: Settings, _args: argparse.Namespace) -> int:
    print(current_version(Database(s.db_path)))
    return 0


def _cmd_db_import(s: Settings, args: argparse.Namespace) -> int:
    src = Path(args.src).expanduser() if args.src else s.legacy_db_path
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


def _cmd_i18n_check(_s: Settings, _args: argparse.Namespace) -> int:
    problems = check_catalogs()
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(t("cli.i18n.ok", count=len(available_locales())))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="storyagent")
    sub = p.add_subparsers(dest="group", required=True)

    cfg = sub.add_parser("config").add_subparsers(dest="cmd", required=True)
    cfg.add_parser("show").set_defaults(func=_cmd_config_show)

    db = sub.add_parser("db").add_subparsers(dest="cmd", required=True)
    db.add_parser("migrate").set_defaults(func=_cmd_db_migrate)
    db.add_parser("status").set_defaults(func=_cmd_db_status)
    imp = db.add_parser("import-legacy")
    imp.add_argument("--from", dest="src", help="path to the v1 story_agents.db (opened read-only)")
    imp.set_defaults(func=_cmd_db_import)

    i18n = sub.add_parser("i18n").add_subparsers(dest="cmd", required=True)
    i18n.add_parser("check").set_defaults(func=_cmd_i18n_check)
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


if __name__ == "__main__":
    raise SystemExit(main())
