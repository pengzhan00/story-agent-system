"""Versioned, checksummed, transactional SQL migrations.

Files: storyagent/db/migrations/NNN_name.sql, applied in numeric order, each in its
own transaction together with its ``schema_migrations`` row. Editing an already-applied
migration is an error (add a new one instead).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .connection import Database

_NAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover() -> list[Migration]:
    out: list[Migration] = []
    for entry in resources.files("storyagent.db").joinpath("migrations").iterdir():
        m = _NAME.match(entry.name)
        if m:
            out.append(Migration(int(m.group(1)), m.group(2), entry.read_text("utf-8")))
    out.sort(key=lambda x: x.version)
    versions = [m.version for m in out]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"migration versions must be contiguous from 001, got {versions}")
    return out


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version    INTEGER PRIMARY KEY,
               name       TEXT NOT NULL,
               checksum   TEXT NOT NULL,
               applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
           )"""
    )


def current_version(db: Database) -> int:
    conn = db.conn
    _ensure_table(conn)
    row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations").fetchone()
    return int(row["v"])


def backup_database(db: Database, label: str) -> Path:
    """Consistent snapshot via SQLite's online backup API -> ``<db>.bak-<label>``.

    This is the rollback path: migrations are forward-only, so restore = replace the DB
    file with this copy (stop the worker/UI first).
    """
    dest = db.path.with_name(f"{db.path.name}.bak-{label}")
    target = sqlite3.connect(dest)
    try:
        db.conn.backup(target)
    finally:
        target.close()
    return dest


def migrate(db: Database) -> tuple[int, int]:
    """Apply pending migrations. Returns (version_before, version_after). Idempotent.

    When pending migrations would change an existing (version >= 1) database, a snapshot
    ``<db>.bak-v<before>`` is written first.
    """
    conn = db.conn
    _ensure_table(conn)
    applied = {
        r["version"]: r["checksum"]
        for r in conn.execute("SELECT version, checksum FROM schema_migrations")
    }
    migrations = discover()
    for m in migrations:
        if m.version in applied and applied[m.version] != m.checksum:
            raise MigrationError(
                f"migration {m.version:03d}_{m.name} was modified after being applied"
            )
    before = max(applied, default=0)
    if before and any(m.version not in applied for m in migrations):
        backup_database(db, f"v{before}")
    for m in migrations:
        if m.version in applied:
            continue
        try:
            conn.executescript(
                "BEGIN IMMEDIATE;\n"
                + m.sql
                + f"\nINSERT INTO schema_migrations(version, name, checksum) "
                f"VALUES ({m.version}, '{m.name}', '{m.checksum}');\nCOMMIT;"
            )
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"{m.version:03d}_{m.name}: {exc}") from exc
    return before, current_version(db)
