from __future__ import annotations

import sqlite3

import pytest

from storyagent.db import Database, MigrationError, current_version, migrate
from storyagent.db import migrator as migrate_mod


def test_migrate_is_idempotent(db: Database) -> None:
    v = current_version(db)
    assert v >= 1
    assert migrate(db) == (v, v)


def test_core_tables_exist(db: Database) -> None:
    names = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "scripts", "characters", "scenes", "episodes", "shots",
            "generation_logs", "schema_migrations"} <= names


def test_foreign_keys_enforced_and_cascade(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO scripts(project_id) VALUES (999)")
    db.conn.execute("INSERT INTO projects(slug,name) VALUES ('p','P')")
    pid = db.conn.execute("SELECT id FROM projects").fetchone()["id"]
    db.conn.execute("INSERT INTO episodes(project_id) VALUES (?)", (pid,))
    db.conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    assert db.conn.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"] == 0


def test_json_columns_validated(db: Database) -> None:
    db.conn.execute("INSERT INTO projects(slug,name) VALUES ('p','P')")
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE projects SET genre_tags='not json'")


def test_shot_numbering_unique_per_episode(db: Database) -> None:
    db.conn.execute("INSERT INTO projects(slug) VALUES ('p')")
    db.conn.execute("INSERT INTO episodes(project_id) VALUES (1)")
    ins = "INSERT INTO shots(project_id, episode_id, act_number, scene_number, shot_number) VALUES (1,1,1,1,1)"
    db.conn.execute(ins)
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(ins)


def test_transaction_rolls_back_and_nests(db: Database) -> None:
    with pytest.raises(RuntimeError), db.transaction() as c:
        c.execute("INSERT INTO projects(slug) VALUES ('rolled-back')")
        raise RuntimeError("boom")
    assert db.conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"] == 0
    with db.transaction() as outer, db.transaction() as inner:
        assert outer is inner
        inner.execute("INSERT INTO projects(slug) VALUES ('kept')")
    assert db.conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"] == 1


def test_modified_applied_migration_is_rejected(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    real = migrate_mod.discover()
    tampered = [migrate_mod.Migration(real[0].version, real[0].name, real[0].sql + "\n-- edit"), *real[1:]]
    monkeypatch.setattr(migrate_mod, "discover", lambda: tampered)
    with pytest.raises(MigrationError, match="modified after being applied"):
        migrate(db)


def test_failed_migration_rolls_back_atomically(settings, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    bad = migrate_mod.Migration(
        1, "bad", "CREATE TABLE ok_table(x INTEGER);\nCREATE TABLE ok_table(x INTEGER);"
    )
    monkeypatch.setattr(migrate_mod, "discover", lambda: [bad])
    database = Database(settings.db_path)
    with pytest.raises(MigrationError):
        migrate(database)
    names = {r[0] for r in database.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_table" not in names
    assert current_version(database) == 0
    database.close()


def test_gap_in_versions_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    class Entry:
        def __init__(self, name: str):
            self.name = name

        def read_text(self, _enc: str) -> str:
            return "SELECT 1;"

    class Dir:
        def iterdir(self):  # noqa: ANN201
            return [Entry("001_a.sql"), Entry("003_c.sql")]

    class Pkg:
        def joinpath(self, _n: str) -> Dir:
            return Dir()

    monkeypatch.setattr(migrate_mod.resources, "files", lambda _p: Pkg())
    with pytest.raises(MigrationError, match="contiguous"):
        migrate_mod.discover()


def test_snapshot_taken_before_upgrading_existing_db(
    settings, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    database = Database(settings.db_path)
    migrate(database)
    database.conn.execute("INSERT INTO projects(slug,name) VALUES ('keep','Keep')")
    real = migrate_mod.discover()
    extra = migrate_mod.Migration(len(real) + 1, "extra", "CREATE TABLE extra(x INTEGER);")
    monkeypatch.setattr(migrate_mod, "discover", lambda: [*real, extra])

    assert migrate(database) == (len(real), len(real) + 1)
    bak = settings.db_path.with_name(f"{settings.db_path.name}.bak-v{len(real)}")
    assert bak.is_file()
    snap = sqlite3.connect(bak)
    assert snap.execute("SELECT name FROM projects").fetchone()[0] == "Keep"
    assert snap.execute("SELECT 1 FROM sqlite_master WHERE name='extra'").fetchone() is None
    snap.close()
    database.close()


def test_no_snapshot_on_fresh_install_or_noop(db: Database) -> None:
    assert not list(db.path.parent.glob("*.bak-*"))
    migrate(db)
    assert not list(db.path.parent.glob("*.bak-*"))


def test_content_language_independent_of_ui_locale(db: Database) -> None:
    db.conn.execute("INSERT INTO projects(slug) VALUES ('p')")
    assert db.conn.execute("SELECT content_language FROM projects").fetchone()[0] == "zh-CN"
