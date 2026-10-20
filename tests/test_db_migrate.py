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


# ---- 002_media -------------------------------------------------------------------------------
def _project(db: Database) -> int:
    db.conn.execute("INSERT INTO projects(slug,name) VALUES ('p','P')")
    return int(db.conn.execute("SELECT id FROM projects").fetchone()["id"])


def _job(db: Database, jid: str, pid: int, **kw: object) -> None:
    cols = {"id": jid, "project_id": pid, "kind": "video.generate", "provider": "fake", "model_ref": "m", **kw}
    names = ",".join(cols)
    db.conn.execute(f"INSERT INTO media_jobs({names}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))


def test_active_idempotency_key_is_unique_but_reusable_after_finish(db: Database) -> None:
    pid = _project(db)
    _job(db, "a", pid, idempotency_key="k1")
    with pytest.raises(sqlite3.IntegrityError):
        _job(db, "b", pid, idempotency_key="k1")
    db.conn.execute("UPDATE media_jobs SET status='completed' WHERE id='a'")
    _job(db, "c", pid, idempotency_key="k1")  # fine: the first one is no longer active
    _job(db, "d", pid, idempotency_key="")  # empty key never collides
    _job(db, "e", pid, idempotency_key="")


def test_job_status_and_tier_constrained(db: Database) -> None:
    pid = _project(db)
    with pytest.raises(sqlite3.IntegrityError):
        _job(db, "x", pid, status="done")
    with pytest.raises(sqlite3.IntegrityError):
        _job(db, "y", pid, tier="ultra")


def _asset(db: Database, aid: str, pid: int, shot: int | None, current: int, **kw: object) -> None:
    cols = {"id": aid, "project_id": pid, "shot_id": shot, "kind": "video.generate", "slot": "main",
            "path": f"x/{aid}.mp4", "is_current": current, **kw}
    db.conn.execute(
        f"INSERT INTO media_assets({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values())
    )


def test_exactly_one_current_asset_per_slot_including_project_level(db: Database) -> None:
    pid = _project(db)
    _asset(db, "a1", pid, None, 1)
    _asset(db, "a2", pid, None, 0)  # candidates are unlimited
    _asset(db, "a3", pid, None, 0)
    with pytest.raises(sqlite3.IntegrityError):  # NULL shot must not escape the constraint
        _asset(db, "a4", pid, None, 1)
    _asset(db, "b1", pid, None, 1, kind="audio.music")  # other kind: fine
    _asset(db, "c1", pid, None, 1, slot="alt")  # other slot: fine
    db.conn.execute("UPDATE media_assets SET trashed_at='2026-01-01' WHERE id='a1'")
    _asset(db, "a5", pid, None, 1)  # trashed assets do not hold the slot


def test_spec_versions_unique_and_current_view(db: Database) -> None:
    pid = _project(db)
    ins = ("INSERT INTO gen_specs(id,project_id,subject_type,subject_id,version,kind,prompt) "
           "VALUES (?,?,?,?,?,?,?)")
    db.conn.execute(ins, ("s1", pid, "character.portrait", "7", 1, "image.generate", "v1"))
    db.conn.execute(ins, ("s2", pid, "character.portrait", "7", 2, "image.generate", "v2"))
    db.conn.execute(ins, ("s3", pid, "character.portrait", "8", 1, "image.generate", "other"))
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(ins, ("s4", pid, "character.portrait", "7", 2, "image.generate", "dup"))
    cur = {r["subject_id"]: r["prompt"] for r in db.conn.execute("SELECT * FROM gen_specs_current")}
    assert cur == {"7": "v2", "8": "other"}


def test_project_delete_cascades_to_ledger(db: Database) -> None:
    pid = _project(db)
    _job(db, "j", pid)
    _asset(db, "a", pid, None, 1, job_id="j")
    db.conn.execute("INSERT INTO job_events(job_id,event) VALUES ('j','x')")
    db.conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    for table in ("media_jobs", "media_assets", "job_events"):
        assert db.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"] == 0


def test_voice_consent_and_project_settings_columns(db: Database) -> None:
    pid = _project(db)
    assert db.conn.execute("SELECT settings FROM projects").fetchone()[0] == "{}"
    db.conn.execute("INSERT INTO characters(project_id,name) VALUES (?, 'A')", (pid,))
    row = db.conn.execute("SELECT voice_ref_consent, voice_consent_note FROM characters").fetchone()
    assert (row[0], row[1]) == (0, "")
