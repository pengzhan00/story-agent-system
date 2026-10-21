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
from storyagent.workflow import DEFAULT_STAGES, STAGES, StageError, WorkflowCancelled, WorkflowEngine

from .adapter_kit import FAST, wav_bytes
from .story_kit import make_engine, new_project

FAKE_MODELS = """
[defaults]
"image.generate" = "f-img"
"image.edit" = "f-edit"
"video.generate" = "f-vid"
"audio.tts" = "f-tts"
"audio.music" = "f-mus"
[models."f-img"]
kind = "image.generate"
provider = "fake"
timeout_sec = 30
[models."f-edit"]
kind = "image.edit"
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
SERVICES = FAST + '\n[workflow]\npoll_seconds = 0.02\ndefault_voice = ""\nvideo_share = 1.0\n'


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
    assert engine.stages_to_run(pid, None) == list(DEFAULT_STAGES)
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


def _episode_rig(tmp_path: Path) -> tuple[WorkflowEngine, Worker]:
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
    return engine, worker


def test_compose_assembles_the_episode_from_the_assets_that_exist(tmp_path: Path) -> None:
    engine, worker = _episode_rig(tmp_path)
    seen: list[tuple[str, int, int, str]] = []
    with serving(worker):
        out = engine.run_stage(1, "compose", {"burn_subtitles": False}, progress=lambda *a: seen.append(a))
    assert out["shots"] == 8 and out["asset_id"] and "no_picture" not in {w["code"] for w in out["warnings"]}
    asset = engine.spec_store.ledger.get_asset(out["asset_id"])
    info = probe(engine.settings, engine.settings.home / asset.path)
    assert info and info.has_video and info.has_audio and (info.width, info.height) == (720, 1280)
    assert abs((info.duration_sec or 0) - out["duration_sec"]) < 0.5
    assert seen[-1][1] == seen[-1][2] == 100 and any(m.startswith("workflow.progress.assembling|") for *_, m in seen)
    assert out["qc"] and {c["code"] for c in out["qc"]["checks"]} >= {"duration", "aspect", "loudness", "black_ratio"}


def test_compliance_label_goes_into_the_film_and_the_package_describes_it(tmp_path: Path) -> None:
    import json

    from storyagent.compose.export import build_package

    engine, worker = _episode_rig(tmp_path)
    engine.db.conn.execute("UPDATE projects SET settings=? WHERE id=1",
                           (json.dumps({"compliance": {"explicit_label": "overlay"}}),))
    with serving(worker):
        out = engine.run_stage(1, "compose", {})
    ledger = engine.spec_store.ledger
    asset = ledger.get_asset(out["asset_id"])
    assert asset.metadata["label"] is True
    tl = ledger.get_job(out["job_id"]).inputs["timeline"]
    assert tl["label"]["text"] == "AI-generated content" and tl["metadata"]["comment"].startswith("AIGC")
    dest = tmp_path / "package"
    manifest = build_package(engine.settings, ledger, 1, 1, dest, dict(engine.settings.delivery_profile(None)))
    names = {f["name"] for f in manifest["files"]}
    assert {"episode.mp4", "provenance.json"} <= names and any(n.startswith("stems/speech_") for n in names)
    assert any(n.startswith("stems/music_") for n in names)
    prov = json.loads((dest / "provenance.json").read_text("utf-8"))
    assert prov["aigc"] and prov["sources"] and "inputs" not in json.dumps(prov)
    assert json.loads((dest / "manifest.json").read_text("utf-8"))["qc"]["checks"]


def test_a_keyframe_is_composed_from_the_made_portraits_and_scene_image(tmp_path: Path) -> None:
    fake = FakeAdapter()
    engine, worker, _ = story(tmp_path, [fake])
    with serving(worker):
        engine.run_stage(1, "produce", {})
    ledger: Ledger = engine.spec_store.ledger
    jobs = {j.spec_id: j for j in ledger.list_jobs(project_id=1, limit=500)}
    shot = engine.store.list_shots(1)[0]
    key = engine.spec_store.current(1, "shot.keyframe", str(shot["id"]))
    job = jobs[key.id]
    assert job.kind.value == "image.edit" and job.model_ref == "f-edit"
    refs = fake.seen[job.id][0]["refs"]
    made = {a.id: a for a in ledger.list_assets(project_id=1, limit=500)}
    deps = {d["subject_type"] for d in key.depends_on}
    used = [a for a in made.values() if str(tmp_path / "home" / a.path) in refs]
    assert {a.kind for a in used} == {"image.generate"} and len(used) == len(
        [d for d in key.depends_on if d["subject_type"] in ("character.portrait", "scene.keyframe")])
    assert deps == {"character.portrait", "scene.keyframe"}


def test_the_plan_lists_what_would_be_made_and_only_the_confirmed_part_runs(tmp_path: Path) -> None:
    from storyagent.domain import QualityTier
    from storyagent.workflow.plan import produce_plan, totals

    fake = FakeAdapter()
    engine, worker, _ = story(tmp_path, [fake])
    ledger: Ledger = engine.spec_store.ledger
    plan = produce_plan(engine.settings, engine.store, engine.spec_store, ledger, 1, QualityTier.DRAFT)
    by_type = {t: [p for p in plan if p.subject_type == t] for t in {p.subject_type for p in plan}}
    assert len(by_type["character.portrait"]) == 2 and len(by_type["shot.video"]) == 8
    assert all(p.state in ("new", "blocked") for p in plan) and totals(plan)["count"] == MADE
    blocked = {p.reason for p in plan if p.state == "blocked"}
    assert blocked == {"no_model", "no_voice"}                       # said up front, not discovered at run time
    assert all(p.label for p in plan)
    assert all(p.needs for p in by_type["shot.keyframe"])             # a keyframe builds on portraits and scene image
    only = [p.spec_id for p in plan if p.subject_type == "character.portrait"]
    with serving(worker):
        out = engine.run_stage(1, "produce", {"only": only})
    assert out["made"] == 2 and out["failed"] == 0                  # the two confirmed portraits, nothing else
    after = produce_plan(engine.settings, engine.store, engine.spec_store, ledger, 1, QualityTier.DRAFT)
    assert {p.state for p in after if p.subject_type == "character.portrait"} == {"done"}
    assert totals(after)["count"] == MADE - 2


def test_the_plan_names_the_edit_model_and_its_cost_for_keyframes_made_from_references(tmp_path: Path) -> None:
    from storyagent.domain import QualityTier
    from storyagent.workflow.plan import produce_plan

    engine, _worker, _ = story(tmp_path, [FakeAdapter()])
    ledger: Ledger = engine.spec_store.ledger
    args = (engine.settings, engine.store, engine.spec_store, ledger, 1, QualityTier.DRAFT)
    frames = lambda plan: [p for p in plan if p.subject_type == "shot.keyframe"]  # noqa: E731
    composed = frames(produce_plan(*args))
    assert composed and {p.kind for p in composed} == {"image.edit"} and {p.model_ref for p in composed} == {"f-edit"}
    assert all(p.est_seconds >= 600 for p in composed)               # what a composed keyframe really costs
    engine.settings.workflow["keyframe_from_references"] = False
    plain = frames(produce_plan(*args))
    assert {p.kind for p in plain} == {"image.generate"} and all(p.est_seconds < 600 for p in plain)


def test_a_project_voice_unblocks_the_dialogue_in_the_plan(tmp_path: Path) -> None:
    from storyagent.domain import QualityTier
    from storyagent.workflow.plan import produce_plan

    engine, _worker, _ = story(tmp_path, [FakeAdapter()])
    ledger: Ledger = engine.spec_store.ledger
    args = (engine.settings, engine.store, engine.spec_store, ledger, 1, QualityTier.DRAFT)
    before = produce_plan(*args)
    after = produce_plan(*args, project_settings={"default_voice": "library:voice/narrator"})
    lines = lambda plan: [p for p in plan if p.subject_type == "dialogue.line"]  # noqa: E731
    assert any(p.reason == "no_voice" for p in lines(before))
    assert not any(p.reason == "no_voice" for p in lines(after))


def test_the_series_board_counts_match_the_shots_and_assets_behind_them(tmp_path: Path) -> None:
    from storyagent.workflow.series import series_board

    fake = FakeAdapter()
    engine, worker, _ = story(tmp_path, [fake])
    engine.store.update_season(1, 1, {"planned_episodes": 3})
    ledger: Ledger = engine.spec_store.ledger

    def board() -> dict[str, Any]:
        return series_board(engine.store, engine.state, engine.spec_store, ledger, 1)

    before = board()
    eps = before["seasons"][0]["episodes"]
    assert [(e["number"], e["exists"]) for e in eps] == [(1, True), (2, False), (3, False)]
    assert eps[0]["shots"] == len(engine.store.list_shots(1)) == 8 and eps[0]["keyframes"] == 0
    assert eps[0]["stages"]["script"] == "completed" and eps[1]["stages"] == {}
    with serving(worker):
        engine.run_stage(1, "produce", {})
    after = board()
    ep1 = after["seasons"][0]["episodes"][0]
    made = ledger.list_assets(project_id=1, current_only=True, limit=1000)
    assert ep1["keyframes"] == sum(1 for a in made if a.kind.startswith("image.") and a.shot_id) == 8
    assert ep1["videos"] == sum(1 for a in made if a.kind == "video.generate") == 8
    assert ep1["outdated"] == 0 and after["totals"]["planned"] == 3 and after["totals"]["episodes"] == 3
    shot = engine.store.list_shots(1)[0]
    spec = engine.spec_store.current(1, "shot.keyframe", str(shot["id"]))
    assert spec is not None
    engine.spec_store.edit(1, "shot.keyframe", str(shot["id"]), prompt="changed")   # now older than its spec
    assert board()["seasons"][0]["episodes"][0]["outdated"] == 1


def test_compose_adds_the_cards_the_project_asks_for_in_its_language(tmp_path: Path) -> None:
    import json

    engine, worker = _episode_rig(tmp_path)
    engine.store.update_episode(engine.store.ensure_episode(1, 1)["id"], {"title": "Rain Night"})
    engine.store.ensure_episode(1, 2, title="The Pier", summary="Two people, one case.")
    engine.db.conn.execute("UPDATE projects SET content_language='zh-CN', name='Saga', settings=? WHERE id=1", (
        json.dumps({"edit": {"title_card": True, "next_card": True, "scene_fade_sec": 0.2}}),))
    with serving(worker):
        out = engine.run_stage(1, "compose", {"burn_subtitles": False})
    tl = engine.spec_store.ledger.get_job(out["job_id"]).inputs["timeline"]
    cards = [s for s in tl["segments"] if s["source"]["type"] == "card"]
    assert [c["label"] for c in cards] == ["title", "next"] and tl["segments"][0]["label"] == "title"
    assert cards[0]["source"]["lines"] == ["Saga", "第 1 季 第 1 集 Rain Night"]
    assert cards[1]["source"]["lines"] == ["未完待续", "下一集:The Pier"]
    assert any("fade_in" in s for s in tl["segments"][1:-1])                    # scene changes dip through black
    on = {s["label"] for s in tl["segments"]}
    assert "recap" not in on                                                    # not asked for
    # a run parameter overrides the project: ask for the recap of episode 1 on episode 2's film
    from storyagent.workflow.media import edit_options

    assert edit_options(type("C", (), {"settings": engine.settings, "project": engine.projects.get(1),
                                       "params": {"edit": {"recap_card": True, "next_card": False}}})()
                        )["recap_card"] is True



def test_an_edited_spec_keeps_its_old_asset_in_use_and_shows_as_outdated(tmp_path: Path) -> None:
    from storyagent.domain import QualityTier
    from storyagent.workflow.flow import describe_flow
    from storyagent.workflow.plan import produce_plan
    from storyagent.workflow.stages import Ctx

    engine, _worker = _episode_rig(tmp_path)
    ledger: Ledger = engine.spec_store.ledger
    ctx = Ctx(engine.settings, engine.db, engine.store, engine.state, engine.agents, engine.projects,
              engine.projects.get(1), {}, spec_store=engine.spec_store)
    from storyagent.workflow.media import build_episode_timeline

    shot = engine.store.list_shots(1)[0]
    key_before = [s["source"]["type"] for s in build_episode_timeline(ctx, 1)["segments"]]
    engine.spec_store.edit(1, "shot.keyframe", str(shot["id"]), prompt="a different picture")
    engine.spec_store.edit(1, "shot.video", str(shot["id"]), prompt="a different clip")
    timeline = build_episode_timeline(ctx, 1)
    assert [s["source"]["type"] for s in timeline["segments"]] == key_before          # not a black card meanwhile
    assert "no_picture" not in {w["code"] for w in timeline["warnings"]}
    plan = produce_plan(engine.settings, engine.store, engine.spec_store, ledger, 1, QualityTier.DRAFT)
    edited = {p.subject_type: p.state for p in plan if p.subject_id == str(shot["id"])}
    assert edited["shot.keyframe"] == "stale" and edited["shot.video"] == "stale"      # outdated, not "new"
    steps = {s["stage"]: s for s in describe_flow(engine.settings, engine.db, engine.state, ledger, 1)["steps"]}
    produce = steps["produce"]["media"]
    counts = next(m for m in produce if m["subject"] == "shot.keyframe")["counts"]
    assert counts["stale"] == 1 and counts["made"] == counts["specs"]                  # all made, one of them outdated
