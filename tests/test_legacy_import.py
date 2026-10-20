from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from storyagent.db import Database
from storyagent.db.legacy_import import LegacyImportError, import_legacy

from .legacy_db import build_legacy_db


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    return build_legacy_db(tmp_path / "story_agents.db")


def test_import_maps_everything_and_never_touches_source(legacy: Path, db: Database) -> None:
    before = sha(legacy)
    rep = import_legacy(legacy, db)
    assert sha(legacy) == before
    assert not Path(f"{legacy}-wal").exists()

    assert (rep.projects, rep.scripts, rep.characters, rep.scenes) == (2, 1, 2, 1)
    assert (rep.music_themes, rep.sound_effects, rep.shots) == (1, 1, 3)

    p = db.conn.execute("SELECT * FROM projects WHERE legacy_id=1").fetchone()
    assert p["name"] == "穿越女医生 Revenge!"
    assert p["slug"].startswith("穿越女医生")  # CJK kept, filesystem-safe
    assert json.loads(p["genre_tags"]) == ["悬疑", "古装"]
    unnamed = db.conn.execute("SELECT slug FROM projects WHERE legacy_id=2").fetchone()
    assert unnamed["slug"] == "project-2"


def test_dirty_legacy_data_is_normalised(legacy: Path, db: Database) -> None:
    rep = import_legacy(legacy, db)
    chars = db.conn.execute("SELECT * FROM characters ORDER BY id").fetchall()
    assert [c["name"] for c in chars] == ["林晚", "陆远"]  # duplicate dropped, reported
    assert json.loads(chars[0]["ref_images"]) == []  # 'broken-json' -> default
    assert any("duplicate character" in n for n in rep.notes)

    shots = db.conn.execute("SELECT * FROM shots ORDER BY id").fetchall()
    keys = {(s["episode_id"], s["act_number"], s["scene_number"], s["shot_number"]) for s in shots}
    assert len(keys) == 3  # colliding shots renumbered, none lost
    assert {s["payload_version"] for s in shots} == {"legacy"}
    by_status = {s["status"] for s in shots}
    assert {"generated", "failed"} <= by_status  # rendered/qc_failed mapped
    assert any(s["legacy_video_path"] == "/old/out/a.mp4" for s in shots)
    eps = {r["number"] for r in db.conn.execute("SELECT number FROM episodes")}
    assert eps == {1, 2}


def test_import_is_idempotent(legacy: Path, db: Database) -> None:
    import_legacy(legacy, db)
    again = import_legacy(legacy, db)
    assert again.projects == 0 and again.skipped_projects == 2 and again.shots == 0
    assert db.conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"] == 2
    assert db.conn.execute("SELECT COUNT(*) c FROM shots").fetchone()["c"] == 3


def test_missing_source(tmp_path: Path, db: Database) -> None:
    with pytest.raises(LegacyImportError) as e:
        import_legacy(tmp_path / "nope.db", db)
    assert e.value.key == "error.legacy.not_found"


def test_not_a_story_db(tmp_path: Path, db: Database) -> None:
    import sqlite3

    p = tmp_path / "other.db"
    sqlite3.connect(p).executescript("CREATE TABLE x(a);")
    with pytest.raises(LegacyImportError) as e:
        import_legacy(p, db)
    assert e.value.key == "error.legacy.unreadable"


def test_failure_rolls_back_whole_project(legacy: Path, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    from storyagent.db import legacy_import as li

    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(li, "_map_shot_status", boom)
    with pytest.raises(RuntimeError):
        import_legacy(legacy, db)
    assert db.conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"] == 0
