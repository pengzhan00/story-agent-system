from __future__ import annotations

from storyagent.compose.timeline import AssetRef, ShotInput, TimelineInput, build_timeline

PROFILE = {"width": 720, "height": 1280, "fps": 24, "crf": 18}
MIX = {"lead_in_sec": 0.4, "max_slowdown": 2.2, "bgm_gain": 0.16, "sfx_gain": 0.5}


def clip(i: str, sec: float | None) -> AssetRef:
    return AssetRef(i, "video.generate", sec)


def voice(i: str, sec: float | None) -> AssetRef:
    return AssetRef(i, "audio.tts", sec)


def tl(*shots: ShotInput, bgm: AssetRef | None = None) -> dict:
    return build_timeline(TimelineInput(shots, PROFILE, MIX, {"font": "X"}, bgm=bgm))


def test_shot_is_as_long_as_the_speech_needs_not_the_plan() -> None:
    shot = ShotInput(1, "1.1.1", 3.0, video=clip("v1", 3.0),
                     lines=[("Hello", voice("a1", 2.0)), ("World", voice("a2", 1.5))])
    t = tl(shot)
    seg = t["segments"][0]
    # lead 0.4 + 2.0 + gap 0.25 + 1.5 + tail 0.35 = 4.5
    assert seg["duration"] == 4.5 and t["total_sec"] == 4.5
    assert [s["start"] for s in t["speech"]] == [0.4, 2.65]
    assert [(c["start"], c["end"]) for c in t["subtitles"]["cues"]] == [(0.4, 2.4), (2.65, 4.15)]
    assert t["subtitles"]["font"] == "X"


def test_planned_duration_wins_when_longer() -> None:
    t = tl(ShotInput(1, "a", 6.0, video=clip("v", 6.0), lines=[("Hi", voice("a", 1.0))]))
    assert t["segments"][0]["duration"] == 6.0 and t["segments"][0]["source"]["mode"] == "trim"


def test_short_clips_slow_down_then_hold_and_long_ones_trim() -> None:
    short = tl(ShotInput(1, "a", 4.0, video=clip("v", 2.0)))["segments"][0]["source"]
    assert short["mode"] == "slow" and short["speed"] == 2.0 and short["trim"] == 4.0
    tiny = tl(ShotInput(1, "a", 5.0, video=clip("v", 1.0)))["segments"][0]["source"]
    assert tiny["speed"] == 2.2 and tiny["trim"] == 2.2 and tiny["hold_after"] == 2.8
    long = tl(ShotInput(1, "a", 3.0, video=clip("v", 8.0)))["segments"][0]["source"]
    assert long == {"type": "video", "asset": "asset:v", "mode": "trim", "speed": 1.0, "trim": 3.0}
    unknown = tl(ShotInput(1, "a", 3.0, video=clip("v", None)))["segments"][0]["source"]
    assert unknown["mode"] == "hold"


def test_stills_and_missing_pictures_degrade_with_a_warning() -> None:
    t = tl(ShotInput(1, "a", 3.0, image=AssetRef("i", "image.generate")), ShotInput(2, "b", 2.0))
    assert [s["source"]["type"] for s in t["segments"]] == ["image", "black"]
    assert {w["code"] for w in t["warnings"]} >= {"no_video", "no_picture", "no_bgm"}
    assert t["segments"][1]["start"] == 3.0 and t["total_sec"] == 5.0


def test_missing_speech_keeps_the_subtitle_and_warns() -> None:
    t = tl(ShotInput(1, "a", 2.0, video=clip("v", 2.0), lines=[("Where were you?", None)]))
    assert {"code": "missing_speech", "shot_id": 1, "line": 0} in t["warnings"]
    assert t["speech"] == [] and t["subtitles"]["cues"][0]["text"] == "Where were you?"
    assert t["segments"][0]["duration"] > 2.0                       # long enough to read


def test_music_bed_spans_the_episode_and_sfx_land_in_their_shot() -> None:
    sfx = AssetRef("s", "audio.sfx", 1.2)
    t = tl(ShotInput(1, "a", 2.0, video=clip("v", 2.0)), ShotInput(2, "b", 3.0, video=clip("w", 3.0), sfx=[(sfx, 0.5)]),
           bgm=AssetRef("m", "audio.music", 30.0))
    assert t["music"] == [{"asset": "asset:m", "start": 0.0, "duration": 5.0, "loop": True, "gain": 0.16}]
    assert t["sfx"][0]["start"] == 2.5
    assert t["audio"]["loudnorm"] == {"i": -16.0, "tp": -1.5, "lra": 11.0} and t["video"]["width"] == 720
    assert "no_bgm" not in {w["code"] for w in t["warnings"]}
