"""compose.assemble with the real ffmpeg on tiny synthetic media (skipped where ffmpeg is missing)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from storyagent.compose.timeline import AssetRef, ShotInput, TimelineInput, build_timeline
from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.assemble import AssembleAdapter, has_libass
from storyagent.media.probe import find_tool, probe

from .adapter_kit import job, make_rig, make_settings, wav_bytes


def ffmpeg_of(tmp_path: Path) -> str:
    tool = find_tool(make_settings(tmp_path / "probe"), "ffmpeg")
    if tool is None:
        pytest.skip("ffmpeg not installed")
    return str(tool)


def make_media(tmp_path: Path, ffmpeg: str) -> dict[str, Path]:
    d = tmp_path / "media"
    d.mkdir()
    run = lambda *a: subprocess.run([ffmpeg, "-y", "-loglevel", "error", *a], check=True)  # noqa: E731
    run("-f", "lavfi", "-i", "testsrc=size=128x224:rate=12:duration=1", "-pix_fmt", "yuv420p", str(d / "clip.mp4"))
    run("-f", "lavfi", "-i", "color=c=blue:s=128x224", "-frames:v", "1", str(d / "still.png"))
    (d / "line1.wav").write_bytes(wav_bytes(1.0))
    (d / "line2.wav").write_bytes(wav_bytes(0.8))
    (d / "bgm.wav").write_bytes(wav_bytes(2.0))
    return {p.name: p for p in d.iterdir()}


def as_paths(tl: dict[str, Any]) -> dict[str, Any]:
    """The worker turns ``asset:<id>`` into file paths before the adapter runs; here the ids already are paths."""
    return json.loads(json.dumps(tl).replace('"asset:', '"'))


def timeline(m: dict[str, Path], *, with_bgm: bool = True) -> dict[str, Any]:
    def ref(name: str, kind: str, dur: float | None) -> AssetRef:
        return AssetRef(str(m[name]), kind, dur)

    shots = [
        ShotInput(1, "1.1.1", 1.5, video=ref("clip.mp4", "video.generate", 1.0),
                  lines=[("Where were you?", ref("line1.wav", "audio.tts", 1.0))]),
        ShotInput(2, "1.1.2", 1.2, image=ref("still.png", "image.generate", None),
                  lines=[("Nowhere.", ref("line2.wav", "audio.tts", 0.8))]),
        ShotInput(3, "1.1.3", 1.0),                                       # no picture at all: a black card
    ]
    profile = {"width": 72, "height": 128, "fps": 12, "crf": 28, "pix_fmt": "yuv420p"}
    mix = {"lead_in_sec": 0.3, "max_slowdown": 2.2, "bgm_gain": 0.2, "sfx_gain": 0.5, "loudnorm_i": -16.0}
    return as_paths(build_timeline(TimelineInput(shots, profile, mix, {"font": "Arial", "font_size": 12},
                                                 bgm=ref("bgm.wav", "audio.music", 2.0) if with_bgm else None)))


def test_renders_a_playable_episode_with_the_planned_duration(tmp_path: Path) -> None:
    ff = ffmpeg_of(tmp_path)
    m = make_media(tmp_path, ff)
    tl = timeline(m)
    rig = make_rig(make_settings(tmp_path), AssembleAdapter(poll_interval_sec=0.02))
    rec, assets = rig.run(job(MediaKind.COMPOSE_ASSEMBLE, "ffmpeg-assemble", {"timeline": tl}, seed=1), timeout=120)
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    main = next(a for a in assets if a.slot == "main")
    info = probe(rig.settings, rig.settings.home / main.path)
    assert info is not None and info.has_video and info.has_audio
    assert (info.width, info.height) == (72, 128) and info.fps == 12
    assert info.duration_sec is not None and abs(info.duration_sec - tl["total_sec"]) < 0.3
    srt = next(a for a in assets if a.slot == "srt")
    text = (rig.settings.home / srt.path).read_text("utf-8")
    assert "Where were you?" in text and "00:00:00,300 --> 00:00:01,300" in text
    assert {w["code"] for w in main.metadata["warnings"]} == {"no_picture", "no_video"}
    assert main.metadata["burned_subtitles"] is has_libass(ff)


def test_a_timeline_without_music_or_speech_still_renders(tmp_path: Path) -> None:
    ff = ffmpeg_of(tmp_path)
    m = make_media(tmp_path, ff)
    tl = as_paths(build_timeline(TimelineInput(
        [ShotInput(1, "a", 1.0, image=AssetRef(str(m["still.png"]), "image.generate"))],
        {"width": 72, "height": 128, "fps": 12, "crf": 28}, {"loudnorm_i": -16.0})))
    rig = make_rig(make_settings(tmp_path), AssembleAdapter(poll_interval_sec=0.02))
    rec, assets = rig.run(job(MediaKind.COMPOSE_ASSEMBLE, "ffmpeg-assemble", {"timeline": tl}, seed=2), timeout=120)
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert [a.slot for a in assets] == ["main"]                       # no subtitles -> no srt


def test_validation(tmp_path: Path) -> None:
    ffmpeg_of(tmp_path)
    rig = make_rig(make_settings(tmp_path), AssembleAdapter())
    rec, _ = rig.run(job(MediaKind.COMPOSE_ASSEMBLE, "ffmpeg-assemble", {"timeline": {"segments": []}}))
    assert rec.error_code == ErrorCode.INVALID_INPUT.value
    bad = {"segments": [{"shot_id": 7, "source": {"type": "video", "asset": str(tmp_path / "nope.mp4")}}]}
    rec2, _ = rig.run(job(MediaKind.COMPOSE_ASSEMBLE, "ffmpeg-assemble", {"timeline": bad}, seed=2))
    assert rec2.error_code == ErrorCode.INVALID_INPUT.value and "[7]" in (rec2.error_message or "")


def test_progress_reads_the_runner_log() -> None:
    a = AssembleAdapter()
    assert a.progress(None, "")[1] == "starting"       # type: ignore[arg-type]
    log = json.dumps({"event": "step", "done": 3, "total": 6, "msg": "shot 3/3"})
    assert a.progress(None, log + "\n") == (0.5, "shot 3/3")   # type: ignore[arg-type]
