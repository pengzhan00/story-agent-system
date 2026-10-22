"""Flicker and banding measured on synthetic clips made with ffmpeg, and the QC checks built on them."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from storyagent.compose.artifacts import ArtifactReport, FrameStats, analyse, frame_stats, measure, shots_hit
from storyagent.compose.qc import ADVISORY, run_qc
from storyagent.config import Settings, load_settings
from storyagent.media.probe import find_tool

SECONDS = 3


@pytest.fixture
def cfg(tmp_path: Path) -> tuple[Settings, Path]:
    s = load_settings({"STORY_AGENT_HOME": str(tmp_path / "h")})
    ffmpeg = find_tool(s, "ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None and find_tool(s, "ffprobe") is None:
        pytest.skip("ffmpeg not available")
    return s, ffmpeg


def clip(ffmpeg: Path, dest: Path, expr: str) -> Path:
    """A 3 s 128x224 clip whose luma is the ``geq`` expression (X, Y = position, N = frame number)."""
    subprocess.run([str(ffmpeg), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=gray:s=128x224:r=24:d={SECONDS},geq=lum='{expr}':cb=128:cr=128",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "12", str(dest)], check=True)
    return dest


CLEAN = "90+0.25*X+0.15*Y"
STRIPED = "90+0.25*X+0.15*Y+if(lt(mod(Y,8),4),22,-22)"
PUMPING = "90+0.25*X+0.15*Y+if(eq(mod(N,2),0),14,-14)"


def test_a_smooth_picture_is_clean_stripes_are_banding_and_alternating_brightness_is_flicker(cfg, tmp_path: Path) -> None:  # noqa: ANN001
    s, ffmpeg = cfg
    qc = s.delivery["qc"]
    clean = measure(ffmpeg, clip(ffmpeg, tmp_path / "c.mp4", CLEAN), qc, native_fps=24)
    assert clean.flicker_sec == 0 and clean.banding_sec == 0 and clean.frames >= 70
    banded = measure(ffmpeg, clip(ffmpeg, tmp_path / "b.mp4", STRIPED), qc, native_fps=24)
    assert banded.banding_sec >= SECONDS - 0.5 and banded.flicker_sec == 0 and banded.stripe_peak > 8
    pumping = measure(ffmpeg, clip(ffmpeg, tmp_path / "f.mp4", PUMPING), qc, native_fps=24)
    assert pumping.flicker_sec >= SECONDS - 1.0 and pumping.banding_sec == 0


def test_a_cut_and_a_fade_are_not_flicker() -> None:
    cfg: dict[str, Any] = {"flicker_amp": 6.0, "flicker_window_sec": 1.0, "stripe_amp": 8.0, "stripe_ratio": 3.0,
                           "stripe_min_sec": 0.25}
    cut = [FrameStats(60.0, 0, 0)] * 24 + [FrameStats(160.0, 0, 0)] * 24
    fade = [FrameStats(10.0 * i, 0, 0) for i in range(24)]
    assert analyse(cut, 24, cfg).flicker_sec == 0 and analyse(fade, 24, cfg).flicker_sec == 0
    pump = [FrameStats(100.0 + (12 if i % 2 else 0), 0, 0) for i in range(48)]
    assert analyse(pump, 24, cfg).flicker_sec > 1.0


def test_frame_stats_see_row_stripes_but_not_column_stripes() -> None:
    w, h = 64, 112
    rows = bytes(100 + (30 if (y // 2) % 2 else 0) for y in range(h) for _ in range(w))
    cols = bytes(100 + (30 if (x // 2) % 2 else 0) for _ in range(h) for x in range(w))
    flat = bytes([100]) * (w * h)
    r, c, f = frame_stats(rows, w, h), frame_stats(cols, w, h), frame_stats(flat, w, h)
    assert r.stripe > 8 and r.stripe_ratio > 3 and c.stripe < 1 and f.stripe == 0


def test_windows_name_the_shots_they_touch() -> None:
    segments = [{"shot_id": 0, "start": 0, "duration": 2}, {"shot_id": 5, "start": 2, "duration": 3},
                {"shot_id": 6, "start": 5, "duration": 3}]
    assert shots_hit([(2.5, 3.0)], segments) == [5]
    assert shots_hit([(0.0, 1.0)], segments) == []            # a card is not a shot
    assert shots_hit([(4.5, 5.5)], segments) == [5, 6]
    assert isinstance(ArtifactReport().as_dict()["flicker_windows"], list)


def test_qc_reports_the_artifact_checks_as_advisory_and_names_the_shots(cfg, tmp_path: Path) -> None:  # noqa: ANN001
    s, ffmpeg = cfg
    film = clip(ffmpeg, tmp_path / "e.mp4", STRIPED)
    profile = dict(s.delivery_profile(None))
    out = run_qc(s, film, profile, [{"shot_id": 3, "start": 0, "duration": 3}])
    codes = {c["code"]: c for c in out["checks"]}
    assert codes["banding"]["ok"] is False and codes["flicker"]["ok"] is True
    assert "banding" in ADVISORY and "flicker" in ADVISORY
    assert out["metrics"]["artifact_shots"]["banding"] == [3] and out["metrics"]["artifacts"]["banding_sec"] > 2
    blocking = [c for c in out["checks"] if c["code"] not in ADVISORY and c["ok"] is False]
    assert out["passed"] == (not blocking)        # the artifacts alone never fail an episode


def test_the_check_can_be_switched_off(cfg, tmp_path: Path) -> None:  # noqa: ANN001
    s, ffmpeg = cfg
    home = tmp_path / "h2"
    (home / "config").mkdir(parents=True)
    (home / "config" / "delivery.toml").write_text("[qc]\nartifacts = false\n")
    off = load_settings({"STORY_AGENT_HOME": str(home)})
    film = clip(ffmpeg, tmp_path / "e2.mp4", STRIPED)
    out = run_qc(off, film, dict(off.delivery_profile(None)))
    assert "banding" not in {c["code"] for c in out["checks"]}


def test_the_cli_reports_a_clip(cfg, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    from storyagent.cli import main
    from storyagent.config import get_settings

    s, ffmpeg = cfg
    film = clip(ffmpeg, tmp_path / "e3.mp4", STRIPED)
    monkeypatch.setenv("STORY_AGENT_HOME", str(s.home))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    get_settings(reload=True)
    try:
        assert main(["qc", "artifacts", str(film)]) == 0
        out = capsys.readouterr().out
        assert "banding" in out and "frames analysed" in out
        assert main(["qc", "artifacts", str(tmp_path / "missing.mp4")]) == 1
    finally:
        monkeypatch.undo()
        get_settings(reload=True)
