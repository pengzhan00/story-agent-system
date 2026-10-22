"""Delivery per platform: SRT handling, the reframe filters, a real re-encode (frame, loudness, cover, teaser) and the
subtitle translation through the translator agent."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.compose.delivery import (
    Cue,
    DeliveryError,
    deliver,
    format_srt,
    parse_srt,
    translate_cues,
    video_chain,
)
from storyagent.config import ConfigError, Settings, load_settings
from storyagent.config.platforms import build_platforms
from storyagent.media.probe import probe

from . import test_api as base_tests
from .test_api import project
from .test_workflow_media import _episode_rig, serving

client = base_tests.client

TINY = {
    "default": {"platform": "tiny_wide"},
    "platforms": {
        "tiny_wide": {"width": 320, "height": 180, "fps": 12, "reframe": "blur", "loudnorm_i": -20, "crf": 30,
                      "cover_sec": 0.5, "trailer_sec": 1},
        "tiny_tall": {"width": 180, "height": 320, "fps": 12, "reframe": "crop", "loudnorm_i": -16, "crf": 30,
                      "cover_sec": 0.5, "trailer_sec": 0, "max_duration_sec": 1},
        "tiny_pad": {"width": 320, "height": 180, "fps": 12, "reframe": "pad", "crf": 30, "trailer_sec": 0},
    },
}


def tiny(settings: Settings) -> Settings:
    return dataclasses.replace(settings, platforms=build_platforms(TINY, ConfigError))


def test_the_packaged_platforms_are_valid_and_a_bad_one_is_refused(tmp_path: Path) -> None:
    s = load_settings({"STORY_AGENT_HOME": str(tmp_path / "h")})
    assert s.platforms.default in s.platforms.items and {"vertical_feed", "broadcast_tv"} <= set(s.platforms.items)
    assert s.platforms.get("broadcast_tv").loudnorm_i == -23.0
    home = tmp_path / "h2"
    (home / "config").mkdir(parents=True)
    (home / "config" / "platforms.toml").write_text('[platforms.vertical_feed]\nreframe = "stretch"\n')
    with pytest.raises(ConfigError):
        load_settings({"STORY_AGENT_HOME": str(home)})
    (home / "config" / "platforms.toml").write_text('[platforms.vertical_feed]\nwidth = 1081\n')
    with pytest.raises(ConfigError):
        load_settings({"STORY_AGENT_HOME": str(home)})


def test_subtitles_round_trip_and_translation_keeps_timing_and_checks_the_count() -> None:
    text = "1\n00:00:00,500 --> 00:00:02,000\nHello.\n\n2\n00:01:03,250 --> 00:01:05,000\nTwo\nlines\n\n"
    cues = parse_srt(text)
    assert [(c.start, c.end, c.text) for c in cues] == [(0.5, 2.0, "Hello."), (63.25, 65.0, "Two\nlines")]
    assert format_srt(cues) == text
    seen: list[int] = []

    def tr(lines: list[str], lang: str) -> list[str]:
        seen.append(len(lines))
        return [f"{lang}:{x}" for x in lines]

    many = [Cue(i, i + 1, f"l{i}") for i in range(95)]
    out = translate_cues(many, "en", tr)
    assert seen == [40, 40, 15] and out[94] == Cue(94, 95, "en:l94")
    with pytest.raises(DeliveryError) as err:
        translate_cues(many[:3], "en", lambda lines, lang: ["only one"])
    assert err.value.code == "translation_mismatch"


def test_the_reframe_chain_fits_the_picture_into_the_frame(tmp_path: Path) -> None:
    p = tiny(load_settings({"STORY_AGENT_HOME": str(tmp_path / "h")})).platforms
    same = video_chain(320, 180, p.get("tiny_wide"))
    assert "scale=320:180" in same and "crop" not in same and "boxblur" not in same
    assert "boxblur" in video_chain(720, 1280, p.get("tiny_wide"))
    assert "crop=180:320" in video_chain(1280, 720, p.get("tiny_tall"))
    assert "pad=320:180" in video_chain(720, 1280, p.get("tiny_pad"))


def _episode(tmp_path: Path) -> tuple[Any, Settings]:
    engine, worker = _episode_rig(tmp_path)
    with serving(worker):
        out = engine.run_stage(1, "compose", {"burn_subtitles": False})
    assert out["asset_id"]
    return engine, tiny(engine.settings)


def test_the_episode_is_re_encoded_for_a_platform_with_cover_teaser_and_subtitles(tmp_path: Path) -> None:
    engine, settings = _episode(tmp_path)
    ledger = engine.spec_store.ledger
    dest = tmp_path / "out"
    rec = deliver(settings, ledger, 1, 1, "tiny_wide", dest, content_language="en")
    folder = dest / "tiny_wide"
    info = probe(settings, folder / "episode.mp4")
    assert info and (info.width, info.height) == (320, 180) and info.has_audio and abs((info.fps or 0) - 12) < 0.5
    assert rec["result"]["reframed"] and rec["result"]["loudness_lufs"] is not None
    assert abs(rec["result"]["loudness_lufs"] - (-20)) <= 3.0
    assert (folder / "cover.jpg").stat().st_size > 0
    trailer = probe(settings, folder / "trailer.mp4")
    assert trailer and abs((trailer.duration_sec or 0) - 1.0) < 0.3
    names = {f["name"] for f in rec["files"]}
    assert {"episode.mp4", "cover.jpg", "trailer.mp4"} <= names
    saved = json.loads((folder / "delivery.json").read_text("utf-8"))
    assert saved["schema"] == "storyagent.delivery.v1" and saved["platform"]["name"] == "tiny_wide"
    assert saved["source"]["asset"] and saved["files"] == rec["files"]
    srt = [n for n in names if n.startswith("subtitles.")]
    assert srt == ["subtitles.en.srt"] and parse_srt((folder / srt[0]).read_text("utf-8"))


def test_nothing_is_cut_to_fit_and_a_crop_is_flagged(tmp_path: Path) -> None:
    engine, settings = _episode(tmp_path)
    ledger = engine.spec_store.ledger
    engine.db.conn.execute("UPDATE projects SET settings=? WHERE id=1",
                           (json.dumps({"compliance": {"explicit_label": "overlay"}}),))
    rec = deliver(settings, ledger, 1, 1, "tiny_tall", tmp_path / "out")
    assert "over_duration" in rec["warnings"] and "trailer.mp4" not in {f["name"] for f in rec["files"]}
    assert rec["result"]["duration_sec"] > 1.0
    assert (rec["result"]["width"], rec["result"]["height"]) == (180, 320)
    assert "crop_label" not in rec["warnings"]      # the same 9:16 aspect: nothing is cropped
    wide = deliver(settings, ledger, 1, 1, "tiny_pad", tmp_path / "out")
    assert wide["result"]["reframed"] and "crop_label" not in wide["warnings"]


def test_unknown_platform_and_missing_episode_are_reported(tmp_path: Path) -> None:
    engine, worker = _episode_rig(tmp_path)
    settings, ledger = tiny(engine.settings), engine.spec_store.ledger
    with pytest.raises(DeliveryError) as err:
        deliver(settings, ledger, 1, 1, "nope", tmp_path / "o")
    assert err.value.code == "unknown_platform"
    with pytest.raises(DeliveryError) as err:
        deliver(settings, ledger, 1, 1, "tiny_wide", tmp_path / "o")          # nothing assembled yet
    assert err.value.code == "episode_not_ready"


def test_extra_subtitle_languages_come_from_the_translator_agent(tmp_path: Path) -> None:
    engine, settings = _episode(tmp_path)
    ledger = engine.spec_store.ledger
    rec = deliver(settings, ledger, 1, 1, "tiny_pad", tmp_path / "out", languages=["ja", "en"], content_language="en",
                  translate=lambda lines, lang: engine.translate_lines(1, lines, lang))
    assert sorted(rec["subtitles"]) == ["en", "ja"]
    base = parse_srt((tmp_path / "out" / "tiny_pad" / "subtitles.en.srt").read_text("utf-8"))
    ja = parse_srt((tmp_path / "out" / "tiny_pad" / "subtitles.ja.srt").read_text("utf-8"))
    assert [(c.start, c.end) for c in ja] == [(c.start, c.end) for c in base]
    assert all(c.text.startswith("[xx] ") for c in ja)
    with pytest.raises(DeliveryError) as err:
        deliver(settings, ledger, 1, 1, "tiny_pad", tmp_path / "out2", languages=["ja"], content_language="en")
    assert err.value.code == "translate_unavailable"


def test_platforms_and_delivery_over_http(client: TestClient, fsettings: Settings) -> None:
    listed = client.get("/api/platforms")
    assert listed.status_code == 200
    rows = {p["name"]: p for p in listed.json()}
    assert rows["broadcast_tv"]["loudnorm_i"] == -23.0 and [p["name"] for p in listed.json() if p["default"]] == [
        fsettings.platforms.default]
    pid = project(client)["id"]
    missing = client.post(f"/api/projects/{pid}/episodes/1/delivery", json={"platform": "broadcast_tv"})
    assert missing.status_code == 409
    assert client.post(f"/api/projects/{pid}/episodes/1/delivery", json={"platform": "nope"}).status_code == 404
    assert client.get("/api/deliveries/none.zip").status_code == 404
    assert client.get("/api/deliveries/..%2Fx.zip").status_code == 404


def test_the_cli_lists_platforms_and_reports_a_missing_episode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    from storyagent.cli import main
    from storyagent.config import get_settings

    monkeypatch.setenv("STORY_AGENT_HOME", str(tmp_path / "cli"))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    get_settings(reload=True)
    try:
        assert main(["db", "migrate"]) == 0 and main(["project", "create", "D"]) == 0
        capsys.readouterr()
        assert main(["delivery", "platforms"]) == 0
        out = capsys.readouterr().out
        assert "broadcast_tv" in out and "-23 LUFS" in out
        assert main(["delivery", "make", "--project", "1", "--out", str(tmp_path / "o")]) == 1
        assert "has not been assembled" in capsys.readouterr().err
    finally:
        monkeypatch.undo()
        get_settings(reload=True)
