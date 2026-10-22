"""Content ratings: config, hard gates, vault storage, masking and unlock, export exclusion, log redaction, audit."""
from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import ConfigError, Settings, load_settings
from storyagent.config.content import build_content
from storyagent.content import ContentError, digest_text, effective_rating, job_rating, project_rating
from storyagent.db import Database, migrate
from storyagent.domain import JobStatus
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.ledger import Ledger
from storyagent.media.library import import_file
from storyagent.media.pack import export_pack
from storyagent.media.probe import find_tool
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker

from .conftest import FAKE_MODELS, FAKE_SERVICES
from .test_api import job_body

RATED_MODELS = FAKE_MODELS + """
[models."fake-adult"]
kind = "video.generate"
provider = "fake"
rating = "restricted"
timeout_sec = 30
"""


@pytest.fixture
def rsettings(tmp_path: Path) -> Settings:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "models.toml").write_text(RATED_MODELS)
    (home / "config" / "services.toml").write_text(FAKE_SERVICES)
    return load_settings({"STORY_AGENT_HOME": str(home)})


@pytest.fixture
def rclient(rsettings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(rsettings), raise_server_exceptions=False) as c:
        yield c


def drain(settings: Settings) -> None:
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    assert Worker(settings, Database(settings.db_path), reg, probes=[]).drain(15)


def restricted_project(client: TestClient, *, adults: bool = True, characters: tuple[str, ...] = ("Ann",)) -> int:
    r = client.post("/api/projects", json={"name": "Private", "rating": "restricted"})
    assert r.status_code == 201 and r.json()["rating"] == "restricted", r.text
    pid = int(r.json()["id"])
    for name in characters:
        c = client.post(f"/api/projects/{pid}/characters", json={"name": name, "age": "28"})
        assert c.status_code == 201
        if adults:
            assert client.patch(f"/api/projects/{pid}/characters/{c.json()['id']}",
                                json={"adult_confirmed": 1}).status_code == 200
    return pid


def unlock(client: TestClient, **body: Any) -> Any:
    return client.post("/api/content/unlock", json={"confirm_adult": True, **body})


# -- configuration ---------------------------------------------------------------------------------------------
def test_the_packaged_ratings_and_the_rules_a_config_must_keep(tmp_path: Path) -> None:
    s = load_settings({"STORY_AGENT_HOME": str(tmp_path / "h")})
    r, g = s.content.policy("restricted"), s.content.policy("general")
    assert r.vault and r.mask_ui and not r.allow_export and not r.log_prompts and not r.allow_remote and r.gates
    assert not g.restricted and s.content.higher("general", "restricted") == "restricted"
    assert s.content.policy("gone").name == "general"                       # an unknown name reads as the default
    assert s.project_media_dir("p", "image.generate", 3, "restricted") == s.home / "vault" / "p/media/image.generate/3"
    assert s.project_media_dir("p", "image.generate", 3, "general") == s.projects_dir / "p/media/image.generate/3"

    def bad(toml: str) -> None:
        home = tmp_path / "bad"
        (home / "config").mkdir(parents=True, exist_ok=True)
        (home / "config" / "content.toml").write_text(toml)
        with pytest.raises(ConfigError):
            load_settings({"STORY_AGENT_HOME": str(home)})

    bad('[content.ratings.restricted]\ngates = false\n')                       # the vault needs the gates
    bad('[content.gates]\nmin_adult_age = 16\n')
    bad('[content]\nvault_dir = "/etc"\n')
    bad('[content]\nvault_dir = "../x"\n')
    bad('[content]\nencryption = "magic"\n')
    bad('[content]\ndefault_rating = "restricted"\n')
    with pytest.raises(ConfigError):
        build_content({"content": {"ratings": {"a": {"rank": 0}, "b": {"rank": 0}}, "default_rating": "a"}},
                      ConfigError)


def test_a_model_may_name_a_rating_only_when_it_exists(tmp_path: Path) -> None:
    home = tmp_path / "h"
    (home / "config").mkdir(parents=True)
    (home / "config" / "models.toml").write_text(
        '[models."x"]\nkind = "video.generate"\nprovider = "fake"\nrating = "nonsense"\n')
    with pytest.raises(ConfigError):
        load_settings({"STORY_AGENT_HOME": str(home)})


def test_blocked_terms_match_whole_latin_words_and_cjk_substrings(tmp_path: Path) -> None:
    cfg = load_settings({"STORY_AGENT_HOME": str(tmp_path / "h")}).content
    assert cfg.blocked_in("a young child in the rain") == "child"
    assert cfg.blocked_in("kidnapped heiress") is None and cfg.blocked_in("a detective") is None
    assert cfg.blocked_in("一个未成年的角色") == "未成年"


# -- projects ----------------------------------------------------------------------------------------------------
def test_a_rating_is_chosen_at_creation_and_fixed_once_files_exist(rclient: TestClient, rsettings: Settings) -> None:
    assert rclient.post("/api/projects", json={"name": "X", "rating": "nope"}).status_code in (409, 422)
    general = rclient.post("/api/projects", json={"name": "Open"}).json()
    assert general["rating"] == "general"
    assert rclient.patch(f"/api/projects/{general['id']}", json={"rating": "restricted"}).status_code == 200
    pid = restricted_project(rclient)
    db = Database(rsettings.db_path)
    assert project_rating(db, pid) == "restricted" and effective_rating(rsettings, db, pid) == "restricted"
    import_file(rsettings, Ledger(db, rsettings), _file(rsettings, "n.txt", b"notes"), project_id=pid)
    again = rclient.patch(f"/api/projects/{pid}", json={"rating": "general"})
    assert again.status_code == 409


def _file(settings: Settings, name: str, data: bytes) -> Path:
    path = settings.home / "tmp-src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# -- the hard gates and the vault, through the worker ------------------------------------------------------------
def test_restricted_jobs_need_adult_characters_and_land_in_the_vault(rclient: TestClient, rsettings: Settings) -> None:
    pid = restricted_project(rclient, adults=False)
    job = rclient.post("/api/jobs", json=job_body(pid)).json()["job"]
    drain(rsettings)
    row = rclient.get(f"/api/jobs/{job['id']}").json()
    assert row["status"] == JobStatus.FAILED_VALIDATION.value and row["error_code"] == "content_blocked"
    assert "adult_not_confirmed" in row["error_message"]
    cid = rclient.get(f"/api/projects/{pid}/characters").json()[0]["id"]
    rclient.patch(f"/api/projects/{pid}/characters/{cid}", json={"adult_confirmed": 1})
    retry = rclient.post(f"/api/jobs/{job['id']}/retry").json()
    drain(rsettings)
    assert rclient.get(f"/api/jobs/{retry['id']}").json()["status"] == "completed"
    unlock(rclient)
    [asset] = rclient.get("/api/assets", params={"project_id": pid}).json()
    assert asset["rating"] == "restricted" and asset["path"].startswith("vault/private/media/")
    assert (rsettings.home / asset["path"]).is_file()


def test_a_minor_age_or_a_blocked_term_stops_the_job_whatever_else_is_set(rclient: TestClient,
                                                                         rsettings: Settings) -> None:
    pid = restricted_project(rclient)
    cid = rclient.get(f"/api/projects/{pid}/characters").json()[0]["id"]
    rclient.patch(f"/api/projects/{pid}/characters/{cid}", json={"age": "15"})
    first = rclient.post("/api/jobs", json=job_body(pid)).json()["job"]
    drain(rsettings)
    assert "minor_age" in rclient.get(f"/api/jobs/{first['id']}").json()["error_message"]
    rclient.patch(f"/api/projects/{pid}/characters/{cid}", json={"age": "28"})
    second = rclient.post("/api/jobs", json=job_body(pid, inputs={"prompt": "a young child waits"},
                                                      seed=9)).json()["job"]
    drain(rsettings)
    row = rclient.get(f"/api/jobs/{second['id']}").json()
    assert row["error_code"] == "content_blocked" and "blocked_term" in row["error_message"]
    # switching compliance off does not lift the gate
    rclient.patch(f"/api/projects/{pid}/settings", json={"compliance": {"enabled": False}})
    third = rclient.post("/api/jobs", json=job_body(pid, inputs={"prompt": "a kid waits"}, seed=10)).json()["job"]
    drain(rsettings)
    assert rclient.get(f"/api/jobs/{third['id']}").json()["error_code"] == "content_blocked"


def test_an_unmounted_vault_volume_refuses_restricted_jobs_but_not_general_ones(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "models.toml").write_text(RATED_MODELS)
    (home / "config" / "services.toml").write_text(FAKE_SERVICES)
    (home / "config" / "content.toml").write_text(f'[content]\nvolume_mount = "{tmp_path / "not-a-mount"}"\n')
    s = load_settings({"STORY_AGENT_HOME": str(home)})
    with TestClient(create_app(s), raise_server_exceptions=False) as c:
        pid = restricted_project(c)
        open_pid = c.post("/api/projects", json={"name": "Open"}).json()["id"]
        assert c.get("/api/content/status").json()["vault_ready"] is False
        a = c.post("/api/jobs", json=job_body(pid)).json()["job"]
        b = c.post("/api/jobs", json=job_body(open_pid)).json()["job"]
        drain(s)
        assert c.get(f"/api/jobs/{a['id']}").json()["error_code"] == "vault_unavailable"
        assert c.get(f"/api/jobs/{b['id']}").json()["status"] == "completed"


def test_a_restricted_model_makes_its_output_restricted_even_in_a_general_project(rclient: TestClient,
                                                                                  rsettings: Settings) -> None:
    assert job_rating(rsettings, "general", "fake-adult") == "restricted"
    pid = rclient.post("/api/projects", json={"name": "Open"}).json()["id"]
    rclient.post("/api/jobs", json=job_body(pid, model_ref="fake-adult"))
    drain(rsettings)
    [asset] = Database(rsettings.db_path).conn.execute("SELECT * FROM media_assets").fetchall()
    assert asset["rating"] == "restricted" and asset["path"].startswith("vault/")
    assert effective_rating(rsettings, Database(rsettings.db_path), pid) == "restricted"


# -- masking and unlock -------------------------------------------------------------------------------------------
def _picture(settings: Settings, tmp_path: Path) -> bytes:
    ffmpeg = find_tool(settings, "ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is needed for a real picture")
    out = tmp_path / "p.png"
    subprocess.run([str(ffmpeg), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x90", "-frames:v",
                    "1", str(out)], check=True)
    return out.read_bytes()


def test_locked_material_is_blanked_blocked_and_shown_as_a_blurred_thumbnail(
        rclient: TestClient, rsettings: Settings, tmp_path: Path) -> None:
    pid = restricted_project(rclient)
    png = _picture(rsettings, tmp_path)
    up = rclient.post(f"/api/projects/{pid}/uploads", params={"filename": "secret-name.png"}, content=png)
    assert up.status_code == 201
    asset = up.json()
    assert asset["locked"] and asset["path"] == "" and asset["sha256"] == "" and asset["metadata"] == {}
    assert asset["rating"] == "restricted"
    spec = rclient.post(f"/api/projects/{pid}/specs", json={
        "subject_type": "scene.keyframe", "subject_id": "1", "kind": "image.generate", "prompt": "SECRET WORDS"})
    assert spec.status_code == 201, spec.text
    assert spec.json()["masked"] and spec.json()["prompt"] == ""
    job = rclient.post("/api/jobs", json=job_body(pid)).json()["job"]
    assert job["locked"] and job["inputs"] == {}
    locked = rclient.get(f"/api/assets/{asset['id']}/file")
    assert locked.status_code == 403 and locked.json()["error"]["code"] == "content_locked"
    mask = rclient.get(f"/api/assets/{asset['id']}/mask")
    assert mask.status_code == 200 and mask.headers["content-type"] == "image/jpeg" and 0 < len(mask.content) < 20000

    assert unlock(rclient, confirm_adult=False).status_code == 422
    status = unlock(rclient).json()
    assert status["unlocked"] and status["expires_at"] and status["vault_ready"]
    shown = rclient.get(f"/api/assets/{asset['id']}").json()
    assert not shown["locked"] and shown["path"].startswith("vault/")
    assert rclient.get(f"/api/assets/{asset['id']}/file").content == png
    assert rclient.get(f"/api/projects/{pid}/specs/scene.keyframe/1").json()["prompt"] == "SECRET WORDS"
    assert rclient.get(f"/api/jobs/{job['id']}").json()["inputs"]["prompt"] == "a cat"
    assert rclient.delete("/api/content/unlock").json()["unlocked"] is False
    assert rclient.get(f"/api/assets/{asset['id']}/file").status_code == 403
    actions = [r["action"] for r in Database(rsettings.db_path).conn.execute("SELECT action FROM audit_log")]
    assert {"content.unlock", "content.view", "content.lock"} <= set(actions)


def test_an_unlock_expires_by_itself(rclient: TestClient, rsettings: Settings) -> None:
    unlock(rclient)
    assert rclient.get("/api/content/status").json()["unlocked"]
    Database(rsettings.db_path).conn.execute("UPDATE content_unlocks SET expires_at='2000-01-01T00:00:00.000Z'")
    assert rclient.get("/api/content/status").json()["unlocked"] is False


def test_with_accounts_an_unlock_needs_the_users_password(fsettings: Settings, tmp_path: Path) -> None:
    from storyagent.auth import AuthStore

    s = load_settings({"STORY_AGENT_HOME": str(fsettings.home), "STORY_AGENT_AUTH": "token"})
    with TestClient(create_app(s), raise_server_exceptions=False) as c:
        AuthStore(Database(s.db_path), s).create_user("ann", "correct horse", "admin")
        assert c.post("/api/auth/login", json={"username": "ann", "password": "correct horse"}).status_code == 200
        assert unlock(c, password="wrong").status_code == 403
        assert c.get("/api/content/status").json()["needs_password"] is True
        assert unlock(c, password="correct horse").json()["unlocked"] is True
        actions = [r["action"] for r in Database(s.db_path).conn.execute("SELECT action FROM audit_log")]
        assert "content.unlock_failed" in actions and "content.unlock" in actions


# -- leaving the machine -----------------------------------------------------------------------------------------
def test_a_restricted_project_is_not_exported_unless_confirmed_and_unlocked(
        rclient: TestClient, rsettings: Settings, tmp_path: Path) -> None:
    pid = restricted_project(rclient)
    db = Database(rsettings.db_path)
    with pytest.raises(ContentError) as err:
        export_pack(rsettings, db, pid, tmp_path / "pack")
    assert err.value.code == "export_blocked"
    refused = rclient.post(f"/api/projects/{pid}/pack")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "content_export_blocked"
    confirmed_but_locked = rclient.post(f"/api/projects/{pid}/pack", params={"confirm_restricted": "true"})
    assert confirmed_but_locked.status_code == 403 and confirmed_but_locked.json()["error"]["code"] == "content_locked"
    unlock(rclient)
    ok = rclient.post(f"/api/projects/{pid}/pack", params={"confirm_restricted": "true"})
    assert ok.status_code == 201
    manifest = export_pack(rsettings, db, pid, tmp_path / "pack2", confirm_restricted=True)
    assert manifest["restricted_export"]["notice_key"] == "content.export.personal_use"
    assert manifest["project"]["rating"] == "restricted"
    assert "content.export" in [r["action"] for r in db.conn.execute("SELECT action FROM audit_log")]
    for url in (f"/api/projects/{pid}/episodes/1/package", f"/api/projects/{pid}/episodes/1/fcp"):
        rclient.delete("/api/content/unlock")
        assert rclient.get(url).status_code == 403


def test_a_pack_imported_into_a_restricted_project_stays_in_the_vault(
        rclient: TestClient, rsettings: Settings, tmp_path: Path) -> None:
    from storyagent.media.pack import import_pack

    db = Database(rsettings.db_path)
    src = rclient.post("/api/projects", json={"name": "Open"}).json()["id"]
    import_file(rsettings, Ledger(db, rsettings), _file(rsettings, "a.txt", b"hello"), project_id=src)
    export_pack(rsettings, db, src, tmp_path / "p")
    target = restricted_project(rclient)
    import_pack(rsettings, db, tmp_path / "p", into=target, policy="skip", only="all", dry_run=False)
    row = db.conn.execute("SELECT path, rating FROM media_assets WHERE project_id=?", (target,)).fetchone()
    assert row["rating"] == "restricted" and row["path"].startswith("vault/")


def test_a_restricted_asset_published_to_the_library_stays_in_the_vault(
        rclient: TestClient, rsettings: Settings, tmp_path: Path) -> None:
    from storyagent.media.library import Library

    db = Database(rsettings.db_path)
    ledger = Ledger(db, rsettings)
    pid = restricted_project(rclient)
    asset = import_file(rsettings, ledger, _file(rsettings, "face.png", b"png"), project_id=pid)
    item = Library(db, rsettings, ledger).publish(asset.id, "portrait", "Hidden")
    assert item.asset is not None and item.asset.path.startswith("vault/_library/portrait/")
    assert db.conn.execute("SELECT rating FROM library_items").fetchone()["rating"] == "restricted"
    assert rclient.get(f"/api/library/{item.id}/file").status_code == 403


# -- logs ---------------------------------------------------------------------------------------------------------
def test_model_calls_of_a_restricted_project_keep_a_digest_not_the_words(rsettings: Settings) -> None:
    from storyagent.llm import ChatResult, LLMRouter

    class Echo:
        name = "echo"

        def list_models(self) -> list[str]:
            return ["m"]

        def chat(self, model: str, system: str, prompt: str, **kw: Any) -> ChatResult:
            return ChatResult(json.dumps({"ok": True}), model, 1, 1, 1)

    db = Database(rsettings.db_path)
    migrate(db)
    db.conn.execute("INSERT INTO projects(slug, name, rating) VALUES ('r', 'R', 'restricted')")
    db.conn.execute("INSERT INTO projects(slug, name) VALUES ('g', 'G')")
    seen: list[dict[str, Any]] = []
    router = LLMRouter(rsettings, db, clients={"ollama": Echo()}, on_call=seen.append)
    for pid in (1, 2):
        router._log("default", "ollama", pid, "SECRET PROMPT", ChatResult("SECRET ANSWER", "m", 1, 1, 1),
                    system="SECRET SYSTEM")
    rows = db.conn.execute("SELECT project_id, prompt, response, system FROM generation_logs ORDER BY id").fetchall()
    assert rows[0]["prompt"] == digest_text("SECRET PROMPT") and "SECRET" not in rows[0]["response"] + rows[0]["system"]
    assert rows[1]["prompt"] == "SECRET PROMPT"
    assert "SECRET" not in json.dumps(seen[0]) and seen[1]["prompt"] == "SECRET PROMPT"


# -- CLI ---------------------------------------------------------------------------------------------------------
def test_the_cli_creates_a_restricted_project_records_adults_and_refuses_export(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from storyagent.cli import main
    from storyagent.config import get_settings

    monkeypatch.setenv("STORY_AGENT_HOME", str(tmp_path / "cli"))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    get_settings(reload=True)
    try:
        assert main(["db", "migrate"]) == 0
        assert main(["project", "create", "Hidden", "--rating", "restricted"]) == 0
        assert main(["project", "create", "Bad", "--rating", "nonsense"]) == 2
        capsys.readouterr()
        assert main(["content", "status", "--project", "1"]) == 0
        assert "restricted" in capsys.readouterr().out
        assert main(["pack", "export", "--project", "1", "--out", str(tmp_path / "o1")]) == 1
        assert "--confirm-restricted" in capsys.readouterr().err
        assert main(["pack", "export", "--project", "1", "--out", str(tmp_path / "o2"), "--confirm-restricted"]) == 0
        db = Database(get_settings().db_path)
        db.conn.execute("INSERT INTO characters(project_id, name) VALUES (1, 'Ann')")
        assert main(["content", "adult", "--project", "1", "--all"]) == 0
        assert "Recorded 1" in capsys.readouterr().out
        assert db.conn.execute("SELECT adult_confirmed FROM characters").fetchone()[0] == 1
    finally:
        monkeypatch.undo()
        get_settings(reload=True)
