"""Hard deletes and the maintenance buttons: what goes, what must stay, and that nothing is lost half-way."""
from __future__ import annotations

from pathlib import Path

import pytest

from storyagent import maintain
from storyagent.config import Settings, load_settings
from storyagent.db import Database, migrate
from storyagent.media.ledger import Ledger, LedgerError
from storyagent.media.specs import SpecStore


@pytest.fixture
def rig(tmp_path: Path) -> tuple[Database, Settings, Ledger, SpecStore]:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    settings = load_settings({"STORY_AGENT_HOME": str(home), "STORY_AGENT_LOCALE": "en"})
    db = Database(settings.db_path)
    migrate(db)
    db.conn.execute("INSERT INTO projects(slug, name) VALUES ('a', 'A'), ('b', 'B')")
    ledger = Ledger(db, settings)
    return db, settings, ledger, SpecStore(db, settings, ledger)


def _spec(db: Database, spec_id: str, subject_id: str, subject_type: str = "shot.keyframe", project: int = 1) -> None:
    db.conn.execute("INSERT INTO gen_specs(id, project_id, subject_type, subject_id, version, kind, author)"
                    " VALUES (?, ?, ?, ?, 1, 'image.generate', 'agent')", (spec_id, project, subject_type, subject_id))


def _asset(db: Database, settings: Settings, asset_id: str, *, spec: str | None = None, current: bool = False,
           project: int | None = 1) -> Path:
    path = settings.home / f"{asset_id}.png"
    path.write_bytes(b"x")
    db.conn.execute("INSERT INTO media_assets(id, project_id, spec_id, spec_version, kind, slot, path, is_current)"
                    " VALUES (?, ?, ?, 1, 'image.generate', ?, ?, ?)",
                    (asset_id, project, spec, f"s:{asset_id}", path.name, int(current)))
    return path


def test_a_current_asset_is_not_purged_and_its_file_stays(rig: tuple[Database, Settings, Ledger, SpecStore]) -> None:
    db, settings, ledger, _ = rig
    file = _asset(db, settings, "a1", current=True)
    with pytest.raises(LedgerError, match="trash"):
        ledger.purge_asset("a1")
    assert file.exists() and ledger.get_asset("a1")
    ledger.trash_asset("a1")
    ledger.purge_asset("a1")
    assert not file.exists()
    with pytest.raises(LedgerError):
        ledger.get_asset("a1")


def test_an_asset_published_in_the_library_is_never_purged(rig: tuple[Database, Settings, Ledger, SpecStore]) -> None:
    """The library item points at this very row (ON DELETE CASCADE): purging it would silently delete the voice."""
    db, settings, ledger, _ = rig
    file = _asset(db, settings, "lib1", project=None)
    db.conn.execute("INSERT INTO library_items(id, role, name, asset_id) VALUES ('i1', 'voice', 'Lin', 'lib1')")
    with pytest.raises(LedgerError, match="voice/Lin"):
        ledger.purge_asset("lib1")
    assert file.exists()
    assert db.conn.execute("SELECT COUNT(*) FROM library_items").fetchone()[0] == 1


def test_pruning_orphans_removes_only_specs_of_deleted_shots_and_empties_their_slots(
    rig: tuple[Database, Settings, Ledger, SpecStore],
) -> None:
    db, settings, _, specs = rig
    db.conn.execute("INSERT INTO episodes(project_id, number) VALUES (1, 1)")
    db.conn.execute("INSERT INTO shots(id, project_id, episode_id) VALUES (7, 1, 1)")
    _spec(db, "alive", "7")
    _spec(db, "dead", "99")
    _spec(db, "dead-line", "99#0", "dialogue.line")
    _spec(db, "other-project", "99", project=2)         # the same shot id in another project is not this one's orphan
    keep = _asset(db, settings, "keep", spec="alive", current=True)
    gone = _asset(db, settings, "gone", spec="dead", current=True)      # still current: its slot is emptied first
    gone2 = _asset(db, settings, "gone2", spec="dead-line")
    other = _asset(db, settings, "other", spec="other-project", current=True, project=2)
    assert maintain.prune_orphans(specs, 1, dry_run=True) == {"specs": 2, "assets": 0, "kept": 0}
    assert gone.exists()
    assert maintain.prune_orphans(specs, 1) == {"specs": 2, "assets": 2, "kept": 0}
    assert keep.exists() and other.exists() and not gone.exists() and not gone2.exists()
    left = {r[0] for r in db.conn.execute("SELECT id FROM gen_specs")}
    assert left == {"alive", "other-project"}


def test_an_orphan_whose_asset_is_in_the_library_is_kept_not_half_deleted(
    rig: tuple[Database, Settings, Ledger, SpecStore],
) -> None:
    db, settings, _, specs = rig
    _spec(db, "dead", "99")
    file = _asset(db, settings, "pub", spec="dead")
    db.conn.execute("INSERT INTO library_items(id, role, name, asset_id) VALUES ('i1', 'reference', 'Keep', 'pub')")
    assert maintain.prune_orphans(specs, 1) == {"specs": 0, "assets": 0, "kept": 1}
    assert file.exists() and db.conn.execute("SELECT COUNT(*) FROM gen_specs WHERE id='dead'").fetchone()[0] == 1


def test_history_pruning_needs_a_real_age_and_stays_inside_the_project(
    rig: tuple[Database, Settings, Ledger, SpecStore],
) -> None:
    db, _, _, _ = rig
    for pid, jid in ((1, "j1"), (2, "j2")):
        db.conn.execute("INSERT INTO media_jobs(id, project_id, kind, provider, model_ref, status) "
                        "VALUES (?, ?, 'image.generate', 'fake', 'm', 'completed')", (jid, pid))
        db.conn.execute("INSERT INTO job_events(job_id, ts, event) VALUES (?, '2020-01-01T00:00:00.000Z', 'old')",
                        (jid,))
    with pytest.raises(ValueError, match="at least 1"):
        maintain.prune_history(db, days=0)
    assert maintain.prune_history(db, days=30, project_id=1, dry_run=True)["deleted"]["job_events"] == 1
    maintain.prune_history(db, days=30, project_id=1)
    left = [r[0] for r in db.conn.execute("SELECT job_id FROM job_events")]
    assert left == ["j2"]                                    # project 2's history was not touched
