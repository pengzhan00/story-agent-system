"""A migration that a real database has applied is never edited: its checksum is pinned here, and a database in the
state production was left in upgrades cleanly."""
from __future__ import annotations

from pathlib import Path

from storyagent.db import Database, migrate
from storyagent.db.migrator import discover

# version -> sha256 of the file as a real database applied it. Add a line when a migration reaches a real database;
# a change to an applied file must be a NEW migration instead (the migrator refuses to start on a mismatch).
APPLIED = {
    25: "ccb83b526385b172ddae383be212c598d981068a682a8779b48fa6fa1d967ff2",   # control center, as of 2026-10-28
}


def test_a_migration_that_a_real_database_has_applied_is_unchanged() -> None:
    by_version = {m.version: m.checksum for m in discover()}
    for version, checksum in APPLIED.items():
        assert by_version[version] == checksum, f"migration {version:03d} was edited after a database applied it"


def test_a_database_left_at_the_applied_025_upgrades_to_the_latest(tmp_path: Path) -> None:
    db = Database(tmp_path / "prod.db")
    db.conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                    "checksum TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))")
    for m in discover():
        if m.version <= 25:
            db.conn.executescript("BEGIN IMMEDIATE;\n" + m.sql +
                                  f"\nINSERT INTO schema_migrations(version, name, checksum) "
                                  f"VALUES ({m.version}, '{m.name}', '{m.checksum}');\nCOMMIT;")
    before, after = migrate(db)
    assert before == 25 and after == len(discover())
    assert db.conn.execute("SELECT name FROM sqlite_master WHERE name='control_cache'").fetchone()
    cols = [r["name"] for r in db.conn.execute("PRAGMA table_info(publication_revisions)")]
    assert cols[-1] == "classification"
    assert migrate(db) == (after, after)
