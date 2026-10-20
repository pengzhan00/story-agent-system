"""produce (specs -> media) and compose (media -> episode): a real worker, fake media adapters, real ffmpeg."""
from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from storyagent.media.adapters.assemble import AssembleAdapter
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.contracts import AssetRecord
from storyagent.media.ledger import Ledger, NewAsset
from storyagent.media.probe import find_tool, probe
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker
from storyagent.workflow import STAGES, StageError, WorkflowCancelled, WorkflowEngine

from .adapter_kit import FAST, wav_bytes
from .story_kit import make_engine, new_project

FAKE_MODELS = """
[defaults]
"image.generate" = "f-img"
"video.generate" = "f-vid"
"audio.tts" = "f-tts"
"audio.music" = "f-mus"
[models."f-img"]
kind = "image.generate"
provider = "fake"
timeout_sec = 30
[models."f-vid"]
kind = "video.generate"
provider = "fake"
timeout_sec = 30
[models."f-tts"]
kind = "audio.tts"
provider = "fake"
timeout_sec = 30
[models."f-mus"]
kind = "audio.music"
provider = "fake"
timeout_sec = 30
[models."f-bad"]
kind = "video.generate"
provider = "fake"
commercial_ok = false
license_tag = "restricted"
timeout_sec = 30
"""
MADE = 2 + 2 + 2 + 8 + 8        # portraits, locations, bgm + theme, keyframes, videos
SERVICES = FAST + '\n[workflow]\npoll_seconds = 0.02\ndefault_voice = ""\n'


@contextmanager
def serving(worker: Worker) -> Iterator[None]:
    stop = threading.Event()
    t = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=10)


def story(tmp_path: Path, adapters: list[Any]) -> tuple[WorkflowEngine, Worker, FakeAdapter | None]:
    engine, _, settings = make_engine(tmp_path, toml=FAKE_MODELS, services=SERVICES)
    pid = new_project(engine)
    engine.run(pid, None, {"visual_prompts": False})
    reg = AdapterRegistry()
    for a in adapters:
        reg.register(a)
    ledger = engine.spec_store.ledger
    worker = Worker(settings, engine.db, reg, ledger=ledger, probes=[], workflow=engine)
    return engine, worker, next((a for a in adapters if isinstance(a, FakeAdapter)), None)


def test_produce_makes_every_spec_in_dependency_order_and_is_resumable(tmp_path: Path) -> None:
    fake = FakeAdapter()
    engine, worker, _ = story(tmp_path, [fake])
    seen: list[tuple[str, int, int, str]] = []
    with serving(worker):
        out = engine.run_stage(1, "produce", {}, progress=lambda *a: seen.append(a))
    # 2 portraits, 2 locations, music + theme, 8 keyframes, 8 videos; sfx has no model, dialogue has no voice
    assert out["made"] == MADE and out["failed"] == 0 and out["tier"] == "draft"
    reasons = sorted({s["reason"] for s in out["skipped_detail"]})
    assert reasons == ["no_model", "no_voice"]
    ledger: Ledger = engine.spec_store.ledger
    jobs = {j.spec_id: j for j in ledger.list_jobs(project_id=1, limit=500)}
    shot = engine.store.list_shots(1)[0]
    key = engine.spec_store.current(1, "shot.keyframe", str(shot["id"]))
    vid = engine.spec_store.current(1, "shot.video", str(shot["id"]))
    assert jobs[key.id].finished_at <= jobs[vid.id].created_at          # the video waited for its keyframe
    frame = next(a for a in ledger.list_assets(project_id=1, spec_id=key.id))
    video_refs = fake.seen[jobs[vid.id].id][0]["refs"]
    assert video_refs == [str(tmp_path / "home" / frame.path)]       # first frame = the keyframe, as a file path
    # progress: monotonic item counts, labelled with i18n keys
    counts = [d for _, d, _, _ in seen]
    assert counts == sorted(counts) and seen[-1][1] == seen[-1][2]
    assert any(m.startswith("workflow.progress.phase.3|") for _, _, _, m in seen)
    # second run: nothing to do
    again = engine.run_stage(1, "produce", {})
    assert again["made"] == 0 and again["reused"] == MADE


def test_a_failed_job_fails_the_stage_but_keeps_the_rest_and_a_rerun_fixes_it(tmp_path: Path) -> None:
    engine, worker, _ = story(tmp_path, [FakeAdapter()])
    shots = engine.store.list_shots(1)
    engine.spec_store.edit(1, "shot.video", str(shots[0]["id"]), model_ref="f-bad", author="user")
    with serving(worker):
        with pytest.raises(StageError) as exc:
            engine.run_stage(1, "produce", {})
        assert exc.value.code == "produce_failed" and exc.value.params["count"] == 1
        stage = {s["stage"]: s for s in engine.state.stages(1)}["produce"]
        assert stage["status"] == "failed" and stage["error_code"] == "workflow.produce_failed"
        assets = engine.spec_store.ledger.list_assets(project_id=1, current_only=True, limit=500)
        assert len(assets) == MADE - 1                                # everything else was made
        engine.spec_store.edit(1, "shot.video", str(shots[0]["id"]), model_ref="f-vid", author="user")
        out = engine.run_stage(1, "produce", {})
    assert out["made"] == 1 and out["failed"] == 0


def test_cancelling_produce_cancels_its_jobs(tmp_path: Path) -> None:
    engine, _, _ = story(tmp_path, [])                                          # no worker: jobs just queue
    flag = {"n": 0}

    def cancelled() -> bool:
        flag["n"] += 1
        return flag["n"] > 40

    with pytest.raises(WorkflowCancelled):
        engine.run_stage(1, "produce", {}, cancelled=cancelled)
    states = {j.status.value for j in engine.spec_store.ledger.list_jobs(project_id=1, limit=500)}
    assert states == {"cancelled"}


def test_stage_order_and_what_a_plain_resume_runs() -> None:
    assert STAGES[-2:] == ("produce", "compose")


def test_resume_runs_the_story_only(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    assert engine.stages_to_run(pid, None) == list(STAGES[:7])
    engine.run(pid, None, {"visual_prompts": False})
    assert engine.stages_to_run(pid, None) == []
    assert engine.stages_to_run(pid, ["compose", "produce"]) == ["produce", "compose"]


# ---- compose with the real ffmpeg --------------------------------------------------------------------
def _ffmpeg(tmp_path: Path) -> str:
    from .adapter_kit import make_settings

    tool = find_tool(make_settings(tmp_path / "probe"), "ffmpeg")
    if tool is None:
        pytest.skip("ffmpeg not installed")
    return str(tool)


def _register(
    engine: WorkflowEngine, spec: Any, src: Path, kind: str, *, dur: float | None, shot: int | None,
) -> AssetRecord:
    home = engine.settings.home
    dest = home / "projects" / "demo" / src.name.replace(".", f"_{spec.id[:6]}.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    ledger: Ledger = engine.spec_store.ledger
    with engine.db.transaction() as conn:
        aid = ledger._insert_asset(  # noqa: SLF001 - test setup registers spec-linked assets without running jobs
            conn, NewAsset(kind=kind, slot="main", path=str(dest.relative_to(home)), bytes=dest.stat().st_size,
                           duration_sec=dur), project_id=1, shot_id=shot, job_id=None, spec_id=spec.id,
            spec_version=spec.version, auto_adopt=True)
    return ledger.get_asset(aid)


def test_compose_assembles_the_episode_from_the_assets_that_exist(tmp_path: Path) -> None:
    ff = _ffmpeg(tmp_path)
    engine, worker, _ = story(tmp_path, [AssembleAdapter(poll_interval_sec=0.02)])
    media = tmp_path / "src"
    media.mkdir()
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=128x224:rate=12:duration=1",
                    "-pix_fmt", "yuv420p", str(media / "clip.mp4")], check=True)
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=128x224", "-frames:v", "1",
                    str(media / "still.png")], check=True)
    (media / "line.wav").write_bytes(wav_bytes(1.0))
    (media / "bgm.wav").write_bytes(wav_bytes(2.0))
    shots = engine.store.list_shots(1)
    sp = engine.spec_store
    for i, shot in enumerate(shots):
        sid = str(shot["id"])
        if i % 2 == 0:       # even shots have a clip, odd ones only a keyframe
            _register(engine, sp.current(1, "shot.video", sid), media / "clip.mp4", "video.generate", dur=1.0,
                      shot=shot["id"])
        _register(engine, sp.current(1, "shot.keyframe", sid), media / "still.png", "image.generate", dur=None,
                  shot=shot["id"])
        for idx in range(len(shot["dialogue"])):
            _register(engine, sp.current(1, "dialogue.line", f"{sid}#{idx}"), media / "line.wav", "audio.tts", dur=1.0,
                      shot=shot["id"])
    bgm = next(m for m in engine.store.list_music(1) if m["kind"] == "bgm")
    _register(engine, sp.current(1, "music.bgm", str(bgm["id"])), media / "bgm.wav", "audio.music", dur=2.0, shot=None)
    seen: list[tuple[str, int, int, str]] = []
    with serving(worker):
        out = engine.run_stage(1, "compose", {"burn_subtitles": False}, progress=lambda *a: seen.append(a))
    assert out["shots"] == 8 and out["asset_id"] and "no_picture" not in {w["code"] for w in out["warnings"]}
    asset = engine.spec_store.ledger.get_asset(out["asset_id"])
    info = probe(engine.settings, engine.settings.home / asset.path)
    assert info and info.has_video and info.has_audio and (info.width, info.height) == (720, 1280)
    assert abs((info.duration_sec or 0) - out["duration_sec"]) < 0.5
    assert seen[-1][1] == seen[-1][2] == 100 and any(m.startswith("workflow.progress.assembling|") for *_, m in seen)
