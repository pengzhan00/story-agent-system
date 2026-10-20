"""Episode QC measured with the real ffmpeg on tiny synthetic files (skipped where ffmpeg is missing)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from storyagent.compose.qc import run_qc
from storyagent.media.probe import find_tool

from .adapter_kit import make_settings

PROFILE = {"width": 72, "height": 128, "min_episode_sec": 0, "max_episode_sec": 0}


def render(ffmpeg: str, dest: Path, *, video: str, audio: str | None, seconds: float = 4) -> Path:
    vsrc = f"{video}:s=360x640:r=12:d={seconds}" if "=" in video else f"{video}=s=360x640:r=12:d={seconds}"
    argv = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", vsrc]
    if audio:
        asrc = f"{audio}:d={seconds}" if "=" in audio else f"{audio}=d={seconds}"
        argv += ["-f", "lavfi", "-i", asrc, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac"]
    subprocess.run([*argv, "-pix_fmt", "yuv420p", "-shortest", str(dest)], check=True)
    return dest


def codes(result: dict, ok: bool) -> set[str]:
    return {c["code"] for c in result["checks"] if c["ok"] is ok}


def test_a_healthy_episode_passes(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    ff = find_tool(settings, "ffmpeg")
    if ff is None:
        pytest.skip("ffmpeg not installed")
    path = render(str(ff), tmp_path / "ok.mp4", video="testsrc2", audio="sine=frequency=300:sample_rate=48000")
    res = run_qc(settings, path, {**PROFILE, "width": 360, "height": 640})
    assert res["passed"], res["checks"]
    assert res["metrics"]["duration_sec"] == pytest.approx(4, abs=0.2) and res["metrics"]["width"] == 360


def test_black_silent_and_wrong_shaped_episodes_fail_the_right_checks(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    ff = find_tool(settings, "ffmpeg")
    if ff is None:
        pytest.skip("ffmpeg not installed")
    black = render(str(ff), tmp_path / "black.mp4", video="color=c=black", audio="anullsrc=r=48000:cl=stereo")
    res = run_qc(settings, black, {**PROFILE, "width": 640, "height": 360})
    assert not res["passed"]
    assert {"black_ratio", "mean_volume", "aspect"} <= codes(res, False)
    mute = render(str(ff), tmp_path / "mute.mp4", video="testsrc2", audio=None)
    res2 = run_qc(settings, mute, {**PROFILE, "width": 360, "height": 640})
    assert "audio_track" in codes(res2, False)


def test_a_missing_or_garbage_file_is_a_failure_not_a_crash(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    assert run_qc(settings, tmp_path / "nope.mp4", PROFILE)["passed"] is False
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"x" * 8192)
    res = run_qc(settings, junk, PROFILE)
    assert res["passed"] is False
