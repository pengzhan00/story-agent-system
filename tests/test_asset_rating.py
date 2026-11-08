"""Rating an asset by hand: the file follows the rating between the vault and the open folders."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyagent.config import Settings, load_settings
from storyagent.content import ContentError, rate_asset
from storyagent.db import Database, migrate
from storyagent.media.ledger import Ledger
from storyagent.media.library import import_file

from .conftest import FAKE_SERVICES
from .test_content import RATED_MODELS, _file, rclient, rsettings, unlock  # noqa: F401  (fixtures)


def test_an_asset_is_rated_by_hand_and_its_file_follows_the_rating(rsettings: Settings) -> None:  # noqa: F811
    db = Database(rsettings.db_path)
    migrate(db)
    ledger = Ledger(db, rsettings)
    asset = import_file(rsettings, ledger, _file(rsettings, "scene.png", b"png"), project_id=None)
    assert asset.rating == "general" and not asset.path.startswith("vault/")
    opened = rsettings.home / asset.path

    hidden = rate_asset(rsettings, ledger, asset.id, "restricted")
    assert hidden.rating == "restricted" and hidden.path.startswith("vault/")
    assert not opened.exists() and (rsettings.home / hidden.path).read_bytes() == b"png"
    assert db.conn.execute("SELECT rating FROM media_assets WHERE id=?", (asset.id,)).fetchone()[0] == "restricted"

    assert rate_asset(rsettings, ledger, asset.id, "restricted").path == hidden.path   # same rating: nothing moves
    back = rate_asset(rsettings, ledger, asset.id, "general")
    assert back.rating == "general" and back.path == asset.path
    assert opened.read_bytes() == b"png" and not (rsettings.home / hidden.path).exists()


def test_rating_refuses_an_unknown_rating_a_taken_place_and_a_missing_vault(
        rsettings: Settings, tmp_path: Path) -> None:  # noqa: F811
    db = Database(rsettings.db_path)
    migrate(db)
    ledger = Ledger(db, rsettings)
    asset = import_file(rsettings, ledger, _file(rsettings, "a.png", b"x"), project_id=None)
    with pytest.raises(ContentError) as unknown:
        rate_asset(rsettings, ledger, asset.id, "nonsense")
    assert unknown.value.code == "unknown_rating"

    taken = rsettings.vault_dir / (rsettings.home / asset.path).relative_to(rsettings.projects_dir)
    taken.parent.mkdir(parents=True, exist_ok=True)
    taken.write_bytes(b"other")
    with pytest.raises(ContentError) as clash:
        rate_asset(rsettings, ledger, asset.id, "restricted")
    assert clash.value.code == "target_exists"
    assert (rsettings.home / asset.path).read_bytes() == b"x" and taken.read_bytes() == b"other"
    assert ledger.get_asset(asset.id).rating == "general"

    home = tmp_path / "unmounted"
    (home / "config").mkdir(parents=True)
    (home / "config" / "models.toml").write_text(RATED_MODELS)
    (home / "config" / "services.toml").write_text(FAKE_SERVICES)
    (home / "config" / "content.toml").write_text(f'[content]\nvolume_mount = "{tmp_path / "not-a-mount"}"\n')
    s = load_settings({"STORY_AGENT_HOME": str(home)})
    db2 = Database(s.db_path)
    migrate(db2)
    ledger2 = Ledger(db2, s)
    asset2 = import_file(s, ledger2, _file(s, "b.png", b"y"), project_id=None)
    with pytest.raises(ContentError) as unmounted:
        rate_asset(s, ledger2, asset2.id, "restricted")
    assert unmounted.value.code == "vault_unavailable"
    assert (s.home / asset2.path).read_bytes() == b"y" and ledger2.get_asset(asset2.id).rating == "general"


def test_the_rating_route_moves_the_file_needs_the_unlock_and_is_audited(
        rclient: TestClient, rsettings: Settings) -> None:  # noqa: F811
    pid = rclient.post("/api/projects", json={"name": "Open"}).json()["id"]
    up = rclient.post(f"/api/projects/{pid}/uploads", params={"filename": "face.png"}, content=b"png")
    assert up.status_code == 201
    asset = up.json()
    assert asset["rating"] == "general"

    locked = rclient.put(f"/api/assets/{asset['id']}/rating", json={"rating": "restricted"})
    assert locked.status_code == 403 and locked.json()["error"]["code"] == "content_locked"
    assert rclient.get(f"/api/assets/{asset['id']}").json()["rating"] == "general"

    assert unlock(rclient).status_code == 200
    bad = rclient.put(f"/api/assets/{asset['id']}/rating", json={"rating": "nonsense"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "content_invalid"
    assert rclient.get(f"/api/assets/{asset['id']}").json()["rating"] == "general"
    done = rclient.put(f"/api/assets/{asset['id']}/rating", json={"rating": "restricted"})
    assert done.status_code == 200 and done.json()["rating"] == "restricted"
    assert done.json()["path"].startswith("vault/")
    assert rclient.get(f"/api/assets/{asset['id']}/file").content == b"png"
    back = rclient.put(f"/api/assets/{asset['id']}/rating", json={"rating": "general"})
    assert back.json()["rating"] == "general" and not back.json()["path"].startswith("vault/")
    rows = Database(rsettings.db_path).conn.execute(
        "SELECT detail FROM audit_log WHERE action='content.rate' ORDER BY id").fetchall()
    assert [(json.loads(r["detail"])["from"], json.loads(r["detail"])["to"]) for r in rows] == [
        ("general", "restricted"), ("restricted", "general")]


def test_the_cli_rates_an_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture[str]) -> None:
    from storyagent.cli import main
    from storyagent.config import get_settings

    monkeypatch.setenv("STORY_AGENT_HOME", str(tmp_path / "cli"))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    get_settings(reload=True)
    try:
        assert main(["db", "migrate"]) == 0
        s = get_settings()
        asset = import_file(s, Ledger(Database(s.db_path), s), _file(s, "c.png", b"z"), project_id=None)
        capsys.readouterr()
        assert main(["asset", "rate", asset.id, "--rating", "restricted"]) == 0
        assert 'is now rated "restricted"' in capsys.readouterr().out
        assert Ledger(Database(s.db_path), s).get_asset(asset.id).path.startswith("vault/")
        actions = [r["action"] for r in Database(s.db_path).conn.execute("SELECT action FROM audit_log")]
        assert "content.rate" in actions
    finally:
        monkeypatch.undo()
        get_settings(reload=True)
