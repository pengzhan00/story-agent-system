"""Asset packs: export -> import round trip with remapped ids, dedupe, dry run, safe zips, loose folders."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from storyagent.media.ledger import Ledger, NewAsset
from storyagent.media.pack import PackError, export_pack, import_folder, import_pack
from storyagent.media.specs import SpecStore

from .story_kit import NAMES, make_engine, new_project


def _source(tmp_path: Path):  # noqa: ANN202
    """A project with a story, specs, and three files: a shot video (2 takes) and a character portrait."""
    engine, _, settings = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, ["analyze", "cast", "script", "scenes", "shotplan", "specs"], {"visual_prompts": False})
    ledger, specs = Ledger(engine.db, settings), SpecStore(engine.db, settings)
    slug = engine.projects.get(pid).slug
    first = engine.store.list_shots(pid)[0]
    hero = engine.store.find_character(pid, NAMES[0])

    def add(kind: str, slot: str, shot_id: int | None, body: bytes, name: str, *, adopt: bool = True) -> str:
        path = settings.project_media_dir(slug, kind, shot_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        spec = specs.current(pid, "shot.video", str(shot_id), "main") if shot_id else None
        a = ledger.register_asset(
            NewAsset(kind=kind, slot=slot, path=str(path.relative_to(settings.home)),
                     sha256=__import__("hashlib").sha256(body).hexdigest(), bytes=len(body),
                     provenance={"schema": "storyagent.provenance.v1", "kind": kind,
                                 "inputs": {"prompt": "SECRET PROMPT"}}),
            project_id=pid, shot_id=shot_id, adopt=adopt)
        if spec:
            engine.db.conn.execute("UPDATE media_assets SET spec_id=?, spec_version=? WHERE id=?",
                                   (spec.id, spec.version, a.id))
        return a.id

    add("video.generate", "main", first["id"], b"take-one", "a.bin", adopt=False)
    add("video.generate", "main", first["id"], b"take-two", "b.bin")
    add("image.generate", f"character.portrait:{hero['id']}", None, b"portrait", "p.bin")
    return engine, settings, pid, first, hero


def _fresh(tmp_path: Path):  # noqa: ANN202
    engine, _, settings = make_engine(tmp_path / "other")
    return engine, settings


def test_a_pack_round_trips_into_a_new_project_with_remapped_ids(tmp_path: Path) -> None:
    engine, settings, pid, first, hero = _source(tmp_path)
    pack = tmp_path / "out" / "rain-pack"
    manifest = export_pack(settings, engine.db, pid, pack, include_history=True)
    assert manifest["schema"] == "storyagent.pack.v1" and manifest["counts"]["assets"] == 3
    text = (pack / "assets" / "assets.json").read_text("utf-8")
    assert "SECRET PROMPT" not in text and "/home" not in text           # redacted provenance, no local paths

    other, other_settings = _fresh(tmp_path)
    # shift ids in the target so a copied id would point at the wrong row
    other.projects.create("filler")
    other.store.create_character(1, {"name": "Someone else"})
    report = import_pack(other_settings, other.db, pack)
    assert report.created_project and report.project_id is not None
    new = report.project_id
    assert report.assets == {"imported": 2, "candidate": 1}
    assert report.story["shots"]["created"] == len(engine.store.list_shots(pid))

    shot = other.store.list_shots(new)[0]
    assert (shot["act_number"], shot["scene_number"], shot["shot_number"]) == (
        first["act_number"], first["scene_number"], first["shot_number"])
    ledger = Ledger(other.db, other_settings)
    takes = ledger.list_assets(project_id=new, shot_id=shot["id"], kind="video.generate")
    assert sorted(t.sha256[:4] for t in takes) and len(takes) == 2
    [current] = [t for t in takes if t.is_current]
    assert (other_settings.home / current.path).read_bytes() == b"take-two"
    hero2 = other.store.find_character(new, NAMES[0])
    [portrait] = ledger.list_assets(project_id=new, kind="image.generate")
    assert portrait.slot == f"character.portrait:{hero2['id']}" and hero2["id"] != hero["id"]
    assert portrait.provenance["origin"] == "pack"
    # the spec of the shot arrived under the new shot id and the take is tied to it (so a later edit marks it stale)
    specs = SpecStore(other.db, other_settings)
    spec = specs.current(new, "shot.video", str(shot["id"]), "main")
    assert spec is not None and current.spec_id == spec.id and current.spec_version == spec.version
    assert len(specs.list_current(new)) == len(SpecStore(engine.db, settings).list_current(pid))


def test_importing_twice_stores_nothing_new_and_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    engine, settings, pid, *_ = _source(tmp_path)
    pack = tmp_path / "p.zip"
    export_pack(settings, engine.db, pid, pack)
    other, other_settings = _fresh(tmp_path)
    dry = import_pack(other_settings, other.db, pack, dry_run=True)
    assert dry.dry_run and dry.assets.get("imported") == 2
    assert other.projects.list() == [] and not list((other_settings.home / "projects").glob("*"))
    first = import_pack(other_settings, other.db, pack)
    assert first.assets["imported"] == 2
    again = import_pack(other_settings, other.db, pack, into=first.project_id)
    assert again.assets == {"duplicate": 2}
    assert all(v.get("created", 0) == 0 for v in again.story.values())
    assert again.specs.get("created", 0) == 0


def test_a_conflicting_take_becomes_a_candidate_unless_new_version_is_asked(tmp_path: Path) -> None:
    engine, settings, pid, first, _ = _source(tmp_path)
    pack = tmp_path / "p"
    export_pack(settings, engine.db, pid, pack)
    other, other_settings = _fresh(tmp_path)
    target = import_pack(other_settings, other.db, pack, only="story").project_id
    assert target is not None
    shot = other.store.list_shots(target)[0]
    ledger = Ledger(other.db, other_settings)
    ledger.register_asset(NewAsset(kind="video.generate", slot="main", path="x/own.bin", sha256="f" * 64, bytes=1),
                          project_id=target, shot_id=shot["id"])             # the teammate's own current take
    skip = import_pack(other_settings, other.db, pack, into=target, only="assets")
    assert skip.assets["candidate"] == 1 and skip.assets["imported"] == 1    # take stays a candidate; portrait is new
    [mine] = ledger.list_assets(project_id=target, shot_id=shot["id"], current_only=True)
    assert mine.sha256 == "f" * 64
    newer = import_pack(other_settings, other.db, pack, into=target, only="assets", policy="new_version")
    assert newer.assets["adopted"] == 1
    [now] = ledger.list_assets(project_id=target, shot_id=shot["id"], current_only=True)
    assert (other_settings.home / now.path).read_bytes() == b"take-two"


def test_zip_slip_and_foreign_schemas_are_refused(tmp_path: Path) -> None:
    other, settings = _fresh(tmp_path)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(PackError) as exc:
        import_pack(settings, other.db, evil)
    assert exc.value.code == "unsafe_path"
    assert not (tmp_path / "escape.txt").exists()
    folder = tmp_path / "foreign"
    folder.mkdir()
    (folder / "pack.json").write_text(json.dumps({"schema": "something.else"}))
    with pytest.raises(PackError) as exc2:
        import_pack(settings, other.db, folder)
    assert exc2.value.code == "schema"
    with pytest.raises(PackError) as exc3:
        import_pack(settings, other.db, tmp_path / "missing")
    assert exc3.value.code == "not_found"


def test_a_tampered_file_is_not_imported(tmp_path: Path) -> None:
    engine, settings, pid, *_ = _source(tmp_path)
    pack = tmp_path / "p"
    export_pack(settings, engine.db, pid, pack)
    victim = next((pack / "assets" / "image.generate").rglob("*.bin"))
    victim.write_bytes(b"swapped")
    other, other_settings = _fresh(tmp_path)
    report = import_pack(other_settings, other.db, pack)
    assert report.assets["corrupt"] == 1 and report.assets["imported"] == 1


def test_a_loose_folder_is_mapped_by_file_name_and_the_rest_is_listed(tmp_path: Path) -> None:
    engine, _, settings = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, ["analyze", "cast", "script", "scenes", "shotplan"], {"visual_prompts": False})
    folder = tmp_path / "drop"
    folder.mkdir()
    (folder / "shot_002.mp4").write_bytes(b"v2")
    (folder / "shot_999.mp4").write_bytes(b"nope")
    (folder / f"{NAMES[0]}.png").write_bytes(b"face")
    (folder / "notes.docx").write_bytes(b"?")
    dry = import_folder(settings, engine.db, folder, pid, dry_run=True)
    assert dry.assets == {"imported": 2}
    assert Ledger(engine.db, settings).list_assets(project_id=pid) == []
    report = import_folder(settings, engine.db, folder, pid)
    assert sorted(report.unassigned) == ["notes.docx", "shot_999.mp4"]
    shots = engine.store.list_shots(pid)
    [video] = Ledger(engine.db, settings).list_assets(project_id=pid, kind="video.generate")
    assert video.shot_id == shots[1]["id"] and video.is_current and video.provenance["origin"] == "import"
    hero = engine.store.find_character(pid, NAMES[0])
    [face] = Ledger(engine.db, settings).list_assets(project_id=pid, kind="image.generate")
    assert face.slot == f"character.portrait:{hero['id']}"


# -- API --------------------------------------------------------------------------------------------
from fastapi.testclient import TestClient  # noqa: E402

from storyagent.config import Settings  # noqa: E402

from . import test_api as base_tests  # noqa: E402
from .test_api import make_shots, project, spec_body  # noqa: E402

client = base_tests.client
run_worker = base_tests.run_worker


def test_pack_export_download_and_dry_run_import_over_http(client: TestClient, fsettings: Settings,
                                                           run_worker) -> None:  # noqa: ANN001
    pid = project(client)["id"]
    [shot] = make_shots(fsettings, pid, 1)
    client.post(f"/api/projects/{pid}/specs", json=spec_body(subject_type="shot.video", subject_id="s1", prompt="walk"))
    client.post(f"/api/projects/{pid}/specs/shot.video/s1/regenerate", json={"tier": "draft", "shot_id": shot})
    run_worker()
    made = client.post(f"/api/projects/{pid}/pack", params={"scope": "all"})
    assert made.status_code == 201, made.text
    info = made.json()
    assert info["counts"]["assets"] == 1 and info["name"].endswith(".zip")
    blob = client.get(f"/api/packs/{info['name']}")
    assert blob.status_code == 200 and blob.content[:2] == b"PK"
    assert client.get("/api/packs/..%2Fetc.zip").status_code == 404
    dry = client.post("/api/projects/import-pack", params={"filename": "x.zip", "dry_run": True}, content=blob.content)
    assert dry.status_code == 200, dry.text
    report = dry.json()
    assert report["dry_run"] is True and report["assets"] == {"imported": 1}
    n_projects = len(client.get("/api/projects").json())
    real = client.post("/api/projects/import-pack", params={"filename": "x.zip"}, content=blob.content).json()
    assert real["created_project"] is True and len(client.get("/api/projects").json()) == n_projects + 1
    bad = client.post("/api/projects/import-pack", params={"filename": "x.zip"}, content=b"not a zip")
    assert bad.status_code == 422


def test_the_cli_exports_previews_and_imports_a_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    from storyagent.cli import main
    from storyagent.config import get_settings

    home = tmp_path / "cli-home"
    monkeypatch.setenv("STORY_AGENT_HOME", str(home))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    get_settings(reload=True)
    try:
        assert main(["db", "migrate"]) == 0 and main(["project", "create", "Pack Demo"]) == 0
        capsys.readouterr()
        out = tmp_path / "pk.zip"
        assert main(["pack", "export", "--project", "1", "--out", str(out)]) == 0
        assert "Exported to" in capsys.readouterr().out and out.is_file()
        assert main(["pack", "export", "--project", "1", "--out", str(out)]) == 1        # never overwrites
        assert "already exists" in capsys.readouterr().err
        assert main(["pack", "import", str(out), "--dry-run"]) == 0
        assert "Dry run" in capsys.readouterr().out
        assert main(["pack", "import", str(out)]) == 0
        assert "Imported into project 2" in capsys.readouterr().out
        assert main(["pack", "import", str(tmp_path / "nope")]) == 1
    finally:
        monkeypatch.undo()
        get_settings(reload=True)
