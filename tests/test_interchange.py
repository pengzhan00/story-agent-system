"""Final Cut Pro interchange: FCPXML out (DTD-valid), the edit read back and compared, the finished film imported."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from storyagent.compose.export import build_package
from storyagent.compose.interchange import (
    InterchangeError,
    MediaFile,
    build_fcp_package,
    build_fcpxml,
    diff_edit,
    import_final,
    parse_fcpxml,
)

from .test_workflow_media import _episode_rig, serving

DTD = Path("/Applications/Final Cut Pro.app/Contents/Frameworks/Interchange.framework/Versions/A/Resources/"
           "FCPXMLv1_10.dtd")


def validate(tmp_path: Path, xml: Path) -> None:
    """Apple's own DTD, when Final Cut Pro and xmllint are on this machine (the path has spaces: copy it)."""
    if not DTD.is_file() or shutil.which("xmllint") is None:
        pytest.skip("Final Cut Pro DTD or xmllint not available")
    local = tmp_path / "fcpxml.dtd"
    shutil.copyfile(DTD, local)
    done = subprocess.run(["xmllint", "--noout", "--dtdvalid", str(local), str(xml)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


TIMELINE: dict[str, Any] = {
    "title": "Ep 1", "total_sec": 12.0, "video": {"width": 720, "height": 1280, "fps": 24},
    "audio": {"rate": 48000, "voice_gain": 1.0, "sfx_gain": 0.5},
    "segments": [
        {"shot_id": 0, "label": "title", "start": 0, "duration": 2,
         "source": {"type": "card", "kind": "title", "lines": ["Rain Night"]}},
        {"shot_id": 1, "label": "s1", "start": 2, "duration": 4,
         "source": {"type": "video", "asset": "asset:v1", "mode": "slow", "speed": 1.5, "trim": 4}},
        {"shot_id": 2, "label": "s2", "start": 6, "duration": 3, "source": {"type": "image", "asset": "asset:i2"}},
        {"shot_id": 3, "label": "s3", "start": 9, "duration": 3,
         "source": {"type": "video", "asset": "asset:v3", "mode": "trim", "speed": 1.0, "trim": 3}}],
    "speech": [{"asset": "asset:s1", "start": 2.4, "duration": 1.5}],
    "sfx": [{"asset": "asset:x1", "start": 3.0, "duration": 0.8}],
    "music": [{"asset": "asset:m1", "start": 0, "duration": 12, "loop": True, "gain": 0.16}],
}
MEDIA = {
    "asset:v1": MediaFile("asset:v1", "media/video/0001_v1.mp4", "video.generate", True, False, 2.5),
    "asset:i2": MediaFile("asset:i2", "media/image/0002_i2.png", "image.generate", True, False, None),
    "asset:v3": MediaFile("asset:v3", "media/video/0003_v3.mp4", "video.generate", True, False, 5.0),
    "asset:s1": MediaFile("asset:s1", "media/speech/001_s1.wav", "audio.tts", False, True, 1.5),
    "asset:x1": MediaFile("asset:x1", "media/sfx/001_x1.wav", "audio.sfx", False, True, 0.8),
    "asset:m1": MediaFile("asset:m1", "media/music/m1.wav", "audio.music", False, True, 5.0),
}


def test_the_project_is_valid_fcpxml_with_roles_lanes_retime_and_a_tiled_music_bed(tmp_path: Path) -> None:
    xml = build_fcpxml(TIMELINE, MEDIA, name="Ep 1")
    path = tmp_path / "t.fcpxml"
    path.write_text(xml, "utf-8")
    validate(tmp_path, path)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>')
    assert 'src="./media/video/0001_v1.mp4"' in xml                      # relative media
    roles = dict(re.findall(r'name="(speech 1|effect 1|music 1)"[^>]*audioRole="(\w+)"', xml))
    assert roles == {"speech 1": "dialogue", "effect 1": "effects", "music 1": "music"}
    assert xml.count('name="music ') == 3                                # a 5 s bed over 12 s: 5 + 5 + 2
    assert '<timept time="90/24s" value="60/24s"' in xml                 # 2.5 s clip slowed x1.5 over 3.75 s
    assert 'amount="-15.9dB"' in xml and 'amount="-6.0dB"' in xml
    parsed = parse_fcpxml(xml)
    assert parsed["fps"] == 24 and parsed["duration_sec"] == 12.0
    spine = [c for c in parsed["clips"] if c["lane"] == 0]
    assert [c["media"] for c in spine if c["media"]] == ["0001_v1.mp4", "0002_i2.png", "0003_v3.mp4"]
    assert next(c for c in spine if c["media"] == "0001_v1.mp4")["retimed"] is True


def test_absolute_media_uses_file_urls(tmp_path: Path) -> None:
    xml = build_fcpxml(TIMELINE, MEDIA, name="Ep 1", absolute=tmp_path)
    assert f'src="file://{tmp_path.resolve()}/media/video/0001_v1.mp4"' in xml


def test_bad_xml_and_other_documents_are_refused() -> None:
    with pytest.raises(InterchangeError) as bad:
        parse_fcpxml("<fcpxml")
    assert bad.value.code == "bad_xml"
    with pytest.raises(InterchangeError) as other:
        parse_fcpxml("<html/>")
    assert other.value.code == "not_fcpxml"


def test_an_assembled_episode_round_trips_through_final_cut(tmp_path: Path) -> None:
    engine, worker = _episode_rig(tmp_path)
    with serving(worker):
        engine.run_stage(1, "compose", {"burn_subtitles": False})
    ledger = engine.spec_store.ledger
    dest = tmp_path / "fcp"
    manifest = build_fcp_package(engine.settings, ledger, 1, 1, dest)
    names = {f["name"] for f in manifest["files"]}
    assert "episode.fcpxml" in names and any(n.startswith("media/video/") for n in names)
    assert any(n.startswith("media/speech/") for n in names) and any(n.startswith("media/music/") for n in names)
    assert all((dest / n).is_file() for n in names)
    validate(tmp_path, dest / "episode.fcpxml")

    xml = (dest / "episode.fcpxml").read_text("utf-8")
    parsed = parse_fcpxml(xml)
    same = diff_edit(manifest, parsed)
    assert same["changed"] == 0 and all(s["status"] == "kept" for s in same["shots"])

    # the editor trims the first picture, drops the second and brings in a clip of their own
    first_pic = [s for s in manifest["segments"] if s["media"]][:2]
    edited = json.loads(json.dumps(parsed))
    spine = [c for c in edited["clips"] if c["lane"] == 0 and c["media"]]
    spine[0]["duration"] = round(spine[0]["duration"] - 0.5, 3)
    edited["clips"] = [c for c in edited["clips"] if c is not spine[1]]
    edited["clips"].append({"name": "my b-roll", "tag": "asset-clip", "media": "broll.mov", "offset": 0.0, "start": 0.0,
                            "duration": 1.0, "lane": 0, "role": "", "retimed": False})
    diff = diff_edit(manifest, edited)
    status = {s["shot_id"]: s["status"] for s in diff["shots"]}
    assert status[first_pic[0]["shot_id"]] == "trimmed" and status[first_pic[1]["shot_id"]] == "removed"
    assert diff["added"] == [{"name": "my b-roll", "media": "broll.mov", "duration": 1.0}] and diff["changed"] == 3


def test_the_finished_film_becomes_the_episode_and_the_package_follows_it(tmp_path: Path) -> None:
    engine, worker = _episode_rig(tmp_path)
    with serving(worker):
        out = engine.run_stage(1, "compose", {"burn_subtitles": False})
    ledger = engine.spec_store.ledger
    original = ledger.get_asset(out["asset_id"])
    film = engine.settings.home / original.path
    graded = tmp_path / "graded.mov"
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-y", "-loglevel", "error", "-i", str(film), "-c", "copy",
                    str(graded)], check=True)
    asset = import_final(engine.settings, ledger, 1, 1, graded, note="graded in FCP")
    assert asset.is_current and asset.slot == "episode_01" and asset.version == original.version + 1
    assert asset.provenance["origin"] == "external_edit" and asset.provenance["derived_from"] == original.id
    assert not ledger.get_asset(original.id).is_current and asset.duration_sec
    profile = dict(engine.settings.delivery_profile(None))
    manifest = build_package(engine.settings, ledger, 1, 1, tmp_path / "pkg", profile)
    assert manifest["duration_sec"] == asset.duration_sec
    with pytest.raises(InterchangeError) as exc:
        import_final(engine.settings, ledger, 1, 1, tmp_path / "nope.mov")
    assert exc.value.code == "file_missing"
    junk = tmp_path / "notes.txt"
    junk.write_text("x")
    with pytest.raises(InterchangeError) as exc2:
        import_final(engine.settings, ledger, 1, 1, junk)
    assert exc2.value.code == "not_a_film"


# -- API and CLI --------------------------------------------------------------------------------------
def test_the_api_exports_diffs_and_imports(tmp_path: Path) -> None:
    import io
    import zipfile

    from fastapi.testclient import TestClient

    from storyagent.api.app import create_app

    engine, worker = _episode_rig(tmp_path)
    with serving(worker):
        out = engine.run_stage(1, "compose", {"burn_subtitles": False})
    ledger = engine.spec_store.ledger
    film = engine.settings.home / ledger.get_asset(out["asset_id"]).path
    with TestClient(create_app(engine.settings)) as api:
        res = api.get("/api/projects/1/episodes/1/fcp")
        assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            names = set(zf.namelist())
            xml = zf.read("episode.fcpxml").decode("utf-8")
        assert {"episode.fcpxml", "interchange.json"} <= names and any(n.startswith("media/video/") for n in names)
        assert api.get("/api/projects/1/episodes/1/fcp", params={"absolute": True}).status_code == 422
        assert api.get("/api/projects/1/episodes/9/fcp").status_code == 409
        same = api.post("/api/projects/1/episodes/1/fcp/diff", params={"filename": "e.fcpxml"}, content=xml.encode())
        assert same.status_code == 200 and same.json()["changed"] == 0
        assert api.post("/api/projects/1/episodes/1/fcp/diff", params={"filename": "e.fcpxml"},
                        content=b"<html/>").status_code == 422
        up = api.post("/api/projects/1/episodes/1/fcp/final", params={"filename": "done.mp4", "note": "fcp"},
                      content=film.read_bytes())
        assert up.status_code == 201 and up.json()["provenance"]["origin"] == "external_edit"
        bad = api.post("/api/projects/1/episodes/1/fcp/final", params={"filename": "done.txt"}, content=b"x")
        assert bad.status_code == 422


def test_the_cli_exports_compares_and_imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    from storyagent.cli import main
    from storyagent.config import get_settings

    engine, worker = _episode_rig(tmp_path)
    with serving(worker):
        out = engine.run_stage(1, "compose", {"burn_subtitles": False})
    film = engine.settings.home / engine.spec_store.ledger.get_asset(out["asset_id"]).path
    monkeypatch.setenv("STORY_AGENT_HOME", str(engine.settings.home))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    get_settings(reload=True)
    try:
        pkg = tmp_path / "fcp"
        assert main(["fcp", "export", "--project", "1", "--episode", "1", "--out", str(pkg)]) == 0
        assert "File > Import > XML" in capsys.readouterr().out
        assert main(["fcp", "diff", str(pkg), str(pkg / "episode.fcpxml")]) == 0
        assert "0 changes" in capsys.readouterr().out
        assert main(["fcp", "final", str(film), "--project", "1", "--episode", "1"]) == 0
        assert "version" in capsys.readouterr().out
        assert main(["fcp", "final", str(tmp_path / "x.txt"), "--project", "1"]) == 1
    finally:
        monkeypatch.undo()
        get_settings(reload=True)
