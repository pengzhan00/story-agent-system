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


def timeline(m: dict[str, Path], *, with_bgm: bool = True, label: dict[str, Any] | None = None,
             metadata: dict[str, str] | None = None) -> dict[str, Any]:
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
                                                 bgm=ref("bgm.wav", "audio.music", 2.0) if with_bgm else None,
                                                 label=label, metadata=metadata or {})))


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


def test_the_aigc_label_and_metadata_are_written(tmp_path: Path) -> None:
    ff = ffmpeg_of(tmp_path)
    if not has_libass(ff):
        pytest.skip("this ffmpeg has no libass")
    m = make_media(tmp_path, ff)
    tl = timeline(m, label={"text": "AI-generated content", "mode": "both"}, metadata={"comment": "AIGC; test"})
    plain = timeline(m)
    assert tl["total_sec"] == pytest.approx(plain["total_sec"] + 2.0)           # the end card
    assert tl["segments"][-1]["label"] == "card" and tl["label"]["card_start"] == plain["total_sec"]
    rig = make_rig(make_settings(tmp_path), AssembleAdapter(poll_interval_sec=0.02))
    rec, assets = rig.run(job(MediaKind.COMPOSE_ASSEMBLE, "ffmpeg-assemble", {"timeline": tl}, seed=1), timeout=120)
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    main = next(a for a in assets if a.slot == "main")
    assert main.metadata["label"] is True
    out = subprocess.run([find_tool(rig.settings, "ffprobe") or "ffprobe", "-v", "error", "-show_entries",
                          "format_tags=comment", "-of", "default=nw=1:nk=1", str(rig.settings.home / main.path)],
                         capture_output=True, text=True, check=True).stdout
    assert "AIGC; test" in out


def test_a_required_label_without_libass_is_refused_up_front(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ff = ffmpeg_of(tmp_path)
    m = make_media(tmp_path, ff)
    tl = timeline(m, label={"text": "AI", "mode": "overlay"})
    monkeypatch.setattr("storyagent.media.adapters.assemble.has_libass", lambda _f: False)
    rig = make_rig(make_settings(tmp_path), AssembleAdapter(poll_interval_sec=0.02))
    rec, _ = rig.run(job(MediaKind.COMPOSE_ASSEMBLE, "ffmpeg-assemble", {"timeline": tl}, seed=1))
    assert rec.status is JobStatus.FAILED_VALIDATION and rec.error_code == ErrorCode.MISSING_TOOL.value


def test_cards_and_scene_fades_render(tmp_path: Path) -> None:
    from storyagent.compose.timeline import Card

    ff = ffmpeg_of(tmp_path)
    m = make_media(tmp_path, ff)

    def ref(name: str, kind: str, dur: float | None) -> AssetRef:
        return AssetRef(str(m[name]), kind, dur)

    shots = [ShotInput(1, "1.1.1", 1.2, video=ref("clip.mp4", "video.generate", 1.0), scene="1.1"),
             ShotInput(2, "1.2.1", 1.2, image=ref("still.png", "image.generate", None), scene="1.2")]
    profile = {"width": 72, "height": 128, "fps": 12, "crf": 28, "pix_fmt": "yuv420p"}
    tl = as_paths(build_timeline(TimelineInput(
        shots, profile, {"lead_in_sec": 0.3, "loudnorm_i": -16.0}, {"font": "Arial", "font_size": 12,
        "bold": True, "colour": "FFEE00", "border_style": 3},
        bgm=ref("bgm.wav", "audio.music", 2.0), scene_fade_sec=0.3,
        intro_cards=[Card("title", ("Saga", "S1 E1 Rain"), 1.0)],
        outro_cards=[Card("next", ("To be continued",), 1.0)])))
    rig = make_rig(make_settings(tmp_path), AssembleAdapter(poll_interval_sec=0.02))
    rec, assets = rig.run(job(MediaKind.COMPOSE_ASSEMBLE, "ffmpeg-assemble", {"timeline": tl}, seed=3), timeout=120)
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    main = next(a for a in assets if a.slot == "main")
    info = probe(rig.settings, rig.settings.home / main.path)
    assert info is not None and info.duration_sec is not None and abs(info.duration_sec - tl["total_sec"]) < 0.4
    assert tl["total_sec"] == pytest.approx(1.0 + 1.2 + 1.2 + 1.0)
    codes = [w["code"] for w in main.metadata["warnings"]]
    assert ("card_without_libass" in codes) is (not has_libass(ff))


def test_the_subtitle_style_reads_the_config() -> None:
    from storyagent.media.runners.assemble_episode import ass_colour, subtitle_style

    assert ass_colour("FFEE00", "FFFFFF") == "&H0000EEFF" and ass_colour("zz", "000000") == "&H00000000"
    style = subtitle_style({"font": "X", "font_size": 20, "bold": True, "colour": "FFEE00", "border_style": 3,
                            "alignment": 8, "margin_v": 30, "outline": 2})
    assert "PrimaryColour=&H0000EEFF" in style and "Bold=1" in style and "BorderStyle=3" in style
    assert "Alignment=8" in style and "MarginV=30" in style
    assert "Bold=0" in subtitle_style({}) and "BorderStyle=1" in subtitle_style({"border_style": 2})


def test_a_sound_effect_is_trimmed_to_its_shot_but_speech_is_not() -> None:
    from storyagent.media.runners.assemble_episode import audio_cmd

    tl = {"total_sec": 6.0, "audio": {"rate": 48000, "voice_gain": 1.0, "sfx_gain": 0.5,
                                      "loudnorm": {"i": -16.0, "tp": -1.5, "lra": 11.0}, "codec": "aac",
                                      "bitrate": "128k"},
          "speech": [{"path": "v.wav", "start": 0.4, "duration": 2.0}],
          "sfx": [{"path": "s.flac", "start": 1.0, "duration": 2.0}], "music": []}
    cmd = audio_cmd("ffmpeg", tl, Path("out.m4a"))
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "atrim=0:2.0,afade=t=out:st=1.900:d=0.1,adelay=1000|1000,volume=0.5[x0]" in graph
    assert "atrim=0:2.0" not in graph.split("[x0]")[0].split("[v0]")[0]            # the voice chain has no cut


def mean_volume(ffmpeg: str, film: Path, start: float, length: float) -> float:
    done = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-ss", f"{start}", "-t", f"{length}", "-i", str(film),
                           "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True, check=True)
    line = next(x for x in done.stderr.splitlines() if "mean_volume" in x)
    return float(line.split("mean_volume:")[1].split("dB")[0])


def test_room_tone_music_cues_swell_j_cut_and_dissolve_render_together(tmp_path: Path) -> None:
    from storyagent.compose.timeline import MusicCue

    ff = ffmpeg_of(tmp_path)
    m = make_media(tmp_path, ff)

    def ref(name: str, kind: str, dur: float | None) -> AssetRef:
        return AssetRef(str(m[name]), kind, dur)

    shots = [
        ShotInput(1, "1.1.1", 2.0, video=ref("clip.mp4", "video.generate", 1.0), scene="1.1",
                  lines=[("One", ref("line1.wav", "audio.tts", 1.0))]),
        ShotInput(2, "1.1.2", 2.0, video=ref("clip.mp4", "video.generate", 1.0), scene="1.1",
                  lines=[("Two", ref("line2.wav", "audio.tts", 0.8))]),
        ShotInput(3, "1.2.1", 5.0, video=ref("clip.mp4", "video.generate", 1.0), scene="1.2"),
        ShotInput(4, "1.2.2", 2.0, image=ref("still.png", "image.generate", None), scene="1.2"),
    ]
    profile = {"width": 72, "height": 128, "fps": 12, "crf": 28, "pix_fmt": "yuv420p"}
    mix = {"lead_in_sec": 0.3, "bgm_gain": 0.2, "loudnorm_i": -16.0, "room_tone": True, "room_tone_db": -40,
           "music_swell_db": 3.0, "music_swell_min_sec": 1.5}
    cues = (MusicCue(ref("bgm.wav", "audio.music", 2.0), "1.1"), MusicCue(ref("bgm.wav", "audio.music", 2.0), "1.2"))
    tl = as_paths(build_timeline(TimelineInput(shots, profile, mix, {"font": "Arial", "font_size": 12},
                                               music_cues=cues, j_cut_sec=0.3, dissolve_sec=0.5)))
    assert len(tl["music"]) == 2 and tl["music_swell"] and tl["room_tone"]["blocks"][0]["scene"] == "1.1"
    assert tl["segments"][1]["cut_in"] == "j" and tl["segments"][1]["dissolve_out"] == 0.5
    rig = make_rig(make_settings(tmp_path), AssembleAdapter(poll_interval_sec=0.02))
    rec, assets = rig.run(job(MediaKind.COMPOSE_ASSEMBLE, "ffmpeg-assemble", {"timeline": tl}, seed=5), timeout=180)
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    main = next(a for a in assets if a.slot == "main")
    film = rig.settings.home / main.path
    info = probe(rig.settings, film)
    assert info is not None and info.has_video and info.has_audio
    # the dissolve overlapped the two shots instead of adding to them: same length as the timeline
    assert info.duration_sec is not None and abs(info.duration_sec - tl["total_sec"]) < 0.3
    assert "dissolve_unavailable" not in {w["code"] for w in main.metadata["warnings"]}
    # in the last scene nobody speaks (the music cue and the room tone are all there is) and it is not silent
    assert mean_volume(ff, film, tl["segments"][2]["start"] + 1.0, 1.5) > -80.0
