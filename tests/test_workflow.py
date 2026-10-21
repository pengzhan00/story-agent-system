from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.llm import LLMError
from storyagent.workflow import DEFAULT_STAGES as STORY
from storyagent.workflow import StageError, WorkflowCancelled, WorkflowStateError

from .story_kit import NAMES, StoryClient, make_engine, new_project


def run_all(engine, pid, **params):  # noqa: ANN001, ANN201
    return engine.run(pid, None, {"visual_prompts": False, **params})


def test_full_pipeline_builds_the_whole_story(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    out = run_all(engine, pid)
    assert list(out) == list(STORY)
    store = engine.store
    assert {c["name"] for c in store.list_characters(pid)} == set(NAMES)
    assert store.find_character(pid, NAMES[0])["appearance"].startswith("short black hair")
    script = store.get_script(pid, 1)
    assert script["title"] == "Rain Night" and script["total_scenes"] == 4
    assert all(sc["shots"] for act in script["acts"] for sc in act["scenes"])
    assert {loc["name"] for loc in store.list_locations(pid)} == {"Office", "Pier"}
    kinds = sorted(m["kind"] for m in store.list_music(pid))
    assert kinds[0] == "bgm" and kinds.count("theme") == 1
    theme = next(m for m in store.list_music(pid) if m["kind"] == "theme")
    assert "[chorus]" in theme["lyrics"] and theme["duration_sec"] == 90
    assert len(store.list_sfx(pid)) == 1
    shots = store.list_shots(pid)
    assert len(shots) == 8 and all(s["status"] == "ready" and s["payload_version"] == "shot.v3" for s in shots)
    p = shots[0]["render_payload"]
    assert p["scene_id"] == "act01_scene01_shot01" and p["output_spec"]["width"] == 720
    assert p["subject"]["characters"][0]["appearance"].startswith("short black hair")
    assert p["camera"]["duration_sec"] >= 2.0 and p["audio"]["tts_required"] is True
    expected = {**{s: "completed" for s in STORY}, "produce": "pending", "compose": "pending"}
    assert {s["stage"]: s["status"] for s in engine.state.stages(pid)} == expected
    assert engine.projects.get(pid).genre == "mystery"


def test_specs_are_seeded_with_dependencies(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    run_all(engine, pid)
    sp = engine.spec_store
    char = engine.store.find_character(pid, NAMES[0])
    portrait = sp.current(pid, "character.portrait", str(char["id"]))
    assert portrait.author == "agent" and "grey coat" in portrait.prompt and portrait.negative_prompt == "blurry, text"
    assert sp.current(pid, "character.voice", str(char["id"])).text_payload == "It is raining again."
    shot = engine.store.list_shots(pid)[0]
    key = sp.current(pid, "shot.keyframe", str(shot["id"]))
    assert {d["subject_type"] for d in key.depends_on} == {"character.portrait", "scene.keyframe"}
    assert sp.current(pid, "shot.video", str(shot["id"])).depends_on[0]["subject_type"] == "shot.keyframe"
    line = sp.current(pid, "dialogue.line", f"{shot['id']}#0")
    assert line.text_payload == "You are late." and line.prompt == "cold"
    assert sp.current(pid, "music.theme_song", "1") is not None or any(
        s.subject_type == "music.theme_song" for s in sp.list_current(pid))
    types = {s.subject_type for s in sp.list_current(pid)}
    assert {"character.portrait", "character.voice", "scene.keyframe", "shot.keyframe", "shot.video",
            "dialogue.line", "music.theme_song", "music.bgm", "sfx.cue"} <= types


def test_a_second_run_does_nothing_and_asks_the_llm_nothing(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    run_all(engine, pid)
    before = len(client.calls)
    assert run_all(engine, pid) == {}              # resume: every stage is completed
    # an explicit rerun of the stages after analyze skips what already exists (analyze itself re-plans by design)
    out = engine.run(pid, list(STORY[1:]), {"visual_prompts": False})
    assert len(client.calls) == before and out["cast"]["created"] == 0 and out["specs"]["created"] == 0


def test_failure_keeps_partial_progress_and_resumes(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    client.fail_on["shots"] = 2          # the third scene breakdown fails
    with pytest.raises(LLMError):
        run_all(engine, pid)
    st = {s["stage"]: s for s in engine.state.stages(pid)}
    assert st["cast"]["status"] == "completed" and st["script"]["status"] == "failed"
    assert st["script"]["error_code"] == "llm.unreachable"
    script = engine.store.get_script(pid, 1)
    done = [bool(sc.get("shots")) for act in script["acts"] for sc in act["scenes"]]
    assert done == [True, True, False, False]          # saved after every call
    client.fail_on.clear()
    shots_before = client.calls.count("shots")
    run_all(engine, pid)
    assert client.calls.count("shots") - shots_before == 2      # only the two missing scenes were generated
    assert client.calls.count("outline") == 1                  # the outline was not redone
    assert {s["stage"]: s["status"] for s in engine.state.stages(pid)}["specs"] == "completed"


def test_rerunning_a_stage_makes_later_stages_stale(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    run_all(engine, pid)
    engine.run(pid, ["script"], {"force": True, "visual_prompts": False})
    status = {s["stage"]: s["status"] for s in engine.state.stages(pid)}
    assert status["analyze"] == status["cast"] == status["script"] == "completed"
    assert status["scenes"] == status["music"] == status["shotplan"] == status["specs"] == "stale"
    assert engine.stages_to_run(pid, None) == ["scenes", "music", "shotplan", "specs"]


def test_prerequisites_are_named(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    with pytest.raises(StageError) as exc:
        engine.run_stage(pid, "cast")
    assert exc.value.code == "needs_stage" and exc.value.params["stage"] == "analyze"
    assert {s["stage"]: s for s in engine.state.stages(pid)}["cast"]["error_code"] == "workflow.needs_stage"
    empty = engine.projects.create("Empty").id
    with pytest.raises(StageError) as exc2:
        engine.run_stage(empty, "analyze")
    assert exc2.value.code == "no_premise"


def test_shotplan_replaces_only_unlocked_shots_of_its_episode(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    run_all(engine, pid)
    store = engine.store
    first, second = store.list_shots(pid)[:2]
    store.set_shot_lock(first["id"], True)
    store.update_shot(first["id"], {"narration": "hand written"}, force=True)
    # a second episode with its own script and shots
    engine.run(pid, ["script", "shotplan"], {"episode": 2, "visual_prompts": False})
    assert len(store.list_episodes(pid)) == 2
    ep1 = store.list_shots(pid, store.list_episodes(pid)[0]["id"])
    assert len(ep1) == 8                                # episode 2 did not touch episode 1
    engine.run(pid, ["shotplan"], {"episode": 1})
    ep1 = store.list_shots(pid, store.list_episodes(pid)[0]["id"])
    assert len(ep1) == 8 and any(s["id"] == first["id"] and s["narration"] == "hand written" for s in ep1)
    assert second["id"] in {s["id"] for s in ep1}          # unlocked ones are rebuilt in place: ids (and specs) survive
    from storyagent.story import ShotLocked
    with pytest.raises(ShotLocked):
        store.update_shot(first["id"], {"mood": "x"})
    store.update_shot(first["id"], {"locked": False})


def test_unknown_speakers_are_reported_and_reassignable(tmp_path: Path) -> None:
    client = StoryClient()
    client.unknown_speaker = True
    engine, _, _ = make_engine(tmp_path, client)
    pid = new_project(engine)
    out = run_all(engine, pid)
    assert out["script"]["unknown_speakers"] == ["Nobody"]
    assert out["shotplan"]["unresolved_speakers"] == ["Nobody"]
    lines = engine.store.dialogue_lines(pid)
    assert lines and all(line["character_id"] is None for line in lines)
    sid, idx = lines[0]["shot_id"], lines[0]["index"]
    engine.store.reassign_speaker(sid, idx, NAMES[1])
    fixed = next(line for line in engine.store.dialogue_lines(pid) if line["id"] == f"{sid}#{idx}")
    assert fixed["character_id"] is not None


def test_user_edited_specs_survive_a_reseed_but_agent_drafts_follow_the_story(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    run_all(engine, pid)
    sp, store = engine.spec_store, engine.store
    shots = store.list_shots(pid)
    followed, edited = shots[0], shots[1]       # the first shot has spoken lines
    sp.edit(pid, "shot.video", str(edited["id"]), prompt="my own wording", author="user")
    store.update_shot(followed["id"], {"dialogue": [{"character": NAMES[0], "line": "New line!", "emotion": "calm"}]})
    out = engine.run(pid, ["specs"], {"visual_prompts": False, "force": True})["specs"]
    assert sp.current(pid, "shot.video", str(edited["id"])).prompt == "my own wording"
    assert out["kept_user_edits"] >= 1
    line = sp.current(pid, "dialogue.line", f"{followed['id']}#0")
    assert line.text_payload == "New line!" and line.version == 2 and line.author == "agent"


def test_visual_prompts_come_from_the_llm_with_a_fallback(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, ["analyze", "cast", "script", "scenes", "shotplan"], {"visual_prompts": False})
    out = engine.run(pid, ["specs"], {"visual_prompts": True})["specs"]
    shot = engine.store.list_shots(pid)[0]
    assert engine.spec_store.current(pid, "shot.keyframe", str(shot["id"])).prompt.startswith("a woman in a grey coat")
    assert out["visual_prompt_fallbacks"] == 0 and client.calls.count("visual") == 8
    client2 = StoryClient()
    engine2, _, _ = make_engine(tmp_path / "b", client2)
    pid2 = new_project(engine2)
    engine2.run(pid2, ["analyze", "cast", "script", "scenes", "shotplan"], {"visual_prompts": False})
    client2.fail_on["visual"] = 0
    out2 = engine2.run(pid2, ["specs"], {"visual_prompts": True})["specs"]
    assert out2["visual_prompt_fallbacks"] == 8
    spec = engine2.spec_store.current(pid2, "shot.keyframe", str(engine2.store.list_shots(pid2)[0]["id"]))
    assert "short black hair" in spec.prompt and "neon noir" in spec.prompt      # the deterministic prompt


def test_adapting_an_existing_text_uses_the_adapt_agent(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    params = {"source_text": "Once upon a rainy night ...", "visual_prompts": False}
    engine.run(pid, ["analyze", "cast", "script"], params)
    assert any("Once upon a rainy night" in p for p in client.prompts)
    assert engine.store.get_script(pid, 1)["title"] == "Rain Night"


def test_cancel_stops_between_llm_calls_and_resume_continues(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    flag = {"n": 0}

    def cancelled() -> bool:
        flag["n"] += 1
        return flag["n"] > 6

    with pytest.raises(WorkflowCancelled):
        engine.run(pid, None, {"visual_prompts": False}, cancelled=cancelled)
    stages = {s["stage"]: s for s in engine.state.stages(pid)}
    assert any(s["error_code"] == "cancelled" for s in stages.values())
    run_all(engine, pid)
    assert all(s["status"] == "completed" for s in engine.state.stages(pid) if s["stage"] in STORY)


# ---- the run queue ---------------------------------------------------------------------------------------
def test_run_queue_lifecycle(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    st = engine.state
    run = st.enqueue(pid, ["analyze", "cast"], {"visual_prompts": False})
    assert run["status"] == "queued"
    with pytest.raises(WorkflowStateError, match="active run"):
        st.enqueue(pid, ["script"])
    with pytest.raises(WorkflowStateError):
        st.enqueue(engine.projects.create("Other").id, ["nonsense"])
    claimed = st.claim_next("w1", 30)
    assert claimed and claimed["id"] == run["id"] and claimed["status"] == "running"
    assert st.claim_next("w2", 30) is None
    engine.execute_run(claimed, "w1", 30, 0.05)
    done = st.get_run(run["id"])
    assert done["status"] == "completed" and done["lease_owner"] is None
    assert {s["stage"]: s["status"] for s in st.stages(pid)}["cast"] == "completed"
    assert done["progress"]["stage"] == "cast"


def test_run_failure_and_cancel_are_recorded(tmp_path: Path) -> None:
    client = StoryClient()
    client.fail_on["plan"] = 0
    engine, _, _ = make_engine(tmp_path, client)
    pid = new_project(engine)
    st = engine.state
    st.enqueue(pid, ["analyze"])
    claimed = st.claim_next("w", 30)
    engine.execute_run(claimed, "w", 30, 0.05)
    run = st.list_runs(pid)[0]
    assert run["status"] == "failed" and run["error_code"] == "llm.unreachable"
    queued = st.enqueue(pid, ["analyze"])
    assert st.request_cancel(queued["id"]) == "cancelled"
    again = st.enqueue(pid, ["analyze"])
    claimed = st.claim_next("w", 30)
    st.request_cancel(again["id"])
    assert st.get_run(again["id"])["cancel_requested"] is True


def test_expired_run_leases_are_requeued(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    st = engine.state
    run = st.enqueue(pid, ["analyze"])
    st.claim_next("dead-worker", -1)           # a lease that is already over
    assert st.requeue_expired() == [run["id"]]
    assert st.get_run(run["id"])["status"] == "queued"


def test_the_worker_runs_queued_workflow_runs(tmp_path: Path) -> None:
    from storyagent.media.registry import AdapterRegistry
    from storyagent.media.worker import Worker

    engine, _, settings = make_engine(tmp_path)
    pid = new_project(engine)
    worker = Worker(settings, engine.db, AdapterRegistry(), probes=[], workflow=engine)
    try:
        run = engine.state.enqueue(pid, list(STORY), {"visual_prompts": False})
        assert worker.drain(timeout=20)
    finally:
        worker.shutdown()
    done = engine.state.get_run(run["id"])
    assert done["status"] == "completed" and done["progress"]["stage"] == "specs"
    assert all(s["status"] == "completed" for s in engine.state.stages(pid) if s["stage"] in STORY)


# ---- progress you can see ---------------------------------------------------------------------------------
def test_progress_tracker_reports_percent_stage_bars_and_eta() -> None:
    from storyagent.workflow.progress import RunProgress

    now = [0.0]
    p = RunProgress(["analyze", "cast", "script"], {"analyze": 1, "cast": 1, "script": 2}, clock=lambda: now[0])
    snap = p.snapshot()
    assert snap["percent"] == 0 and snap["eta_sec"] is None and snap["stage_count"] == 3
    assert [s["status"] for s in snap["stages"]] == ["pending"] * 3
    now[0] = 10
    p.update("analyze", 0, 0, "")                       # started, no items known yet: running, indeterminate
    snap = p.snapshot()
    assert snap["stages"][0] == {"stage": "analyze", "status": "running", "percent": None, "seconds": None}
    p.update("analyze", 2, 2, "done")
    p.update("cast", 1, 2, "Mara")                       # analyze closes when the next stage reports
    now[0] = 20
    snap = p.snapshot()
    assert snap["stages"][0]["status"] == "completed" and snap["stages"][0]["seconds"] == 0.0
    assert snap["stage_index"] == 2 and snap["percent"] == 37.5 and snap["message"] == "Mara"
    assert snap["eta_sec"] == 33.3            # 20 s for 37.5 % -> 33.3 s left
    p.finish("failed")
    assert p.snapshot()["stages"][1]["status"] == "failed"
    q = RunProgress(["a", "b"], {"a": 1, "b": 1})
    q.update("a", 1, 2)
    q.finish("cancelled")
    assert q.snapshot()["stages"][0]["status"] == "cancelled"
    q2 = RunProgress(["a"], {"a": 1})
    q2.finish()
    assert q2.snapshot()["percent"] == 100 and q2.snapshot()["eta_sec"] is None


def test_a_run_publishes_progress_from_the_first_second_to_100_percent(tmp_path: Path) -> None:
    import threading
    import time

    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    seen: list[dict] = []
    gate = threading.Event()
    original = client.chat

    def slow_chat(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        time.sleep(0.15)                       # long enough for the progress writer to sample
        return original(*a, **kw)

    client.chat = slow_chat                    # type: ignore[method-assign]
    run = engine.state.enqueue(pid, ["analyze", "cast"], {"visual_prompts": False})
    claimed = engine.state.claim_next("w", 30)
    worker = threading.Thread(target=lambda: (engine.execute_run(claimed, "w", 30, 0.05), gate.set()))
    worker.start()
    while not gate.is_set():
        seen.append(engine.state.get_run(run["id"])["progress"])
        time.sleep(0.05)
    worker.join()
    seen = [s for s in seen if s]
    assert seen, "progress must be readable while the run is working"
    percents = [s["percent"] for s in seen]
    assert percents == sorted(percents), "the bar never goes backwards"
    assert any(0 < p < 100 for p in percents)
    final = engine.state.get_run(run["id"])["progress"]
    assert final["percent"] == 100 and [s["status"] for s in final["stages"]] == ["completed", "completed"]
    assert final["elapsed_sec"] > 0


def test_a_stage_can_reshape_its_weight_without_the_bar_going_back() -> None:
    from storyagent.workflow.progress import RunProgress

    p = RunProgress(["a", "b"], {"a": 1, "b": 1})
    p.update("a", 1, 1, "")
    p.update("b", 1, 2, "")
    assert p.percent() == 75.0
    p.set_weight("b", 9)                       # b turns out to be much bigger than guessed
    assert p.percent() == 75.0                 # plateau, not a rewind
    p.update("b", 2, 2, "")
    assert p.percent() == 100.0


def test_an_edited_line_reaches_its_spec_without_force(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    run_all(engine, pid)
    shot = engine.store.list_shots(pid)[0]
    engine.store.update_shot(shot["id"], {"dialogue": [{"character": NAMES[0], "line": "Edited!", "emotion": "calm"}]})
    out = engine.run(pid, ["specs"], {"visual_prompts": False})["specs"]       # no force
    line = engine.spec_store.current(pid, "dialogue.line", f"{shot['id']}#0")
    assert line.text_payload == "Edited!" and line.version == 2 and out["refreshed"] >= 1
    assert engine.spec_store.current(pid, "shot.keyframe", str(shot["id"])).version == 1     # prompts were left alone


def test_script_review_is_on_demand_and_changes_nothing(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    with pytest.raises(WorkflowStateError):
        engine.review_script(pid)                                   # no script yet
    for stage in STORY[:5]:
        engine.run_stage(pid, stage, {"visual_prompts": False})
    before = [r["status"] for r in engine.state.stages(pid)]
    review = engine.review_script(pid)
    assert review["score"] == 7 and review["issues"] == []
    assert client.calls[-1] == "review" and "Rain Night" in client.prompts[-1]
    assert [r["status"] for r in engine.state.stages(pid)] == before


def test_the_target_length_sizes_the_story_and_the_planned_shots(tmp_path: Path) -> None:
    engine, client, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.projects.set_settings(pid, {"target_sec": 30})               # a 30 s pilot, set on the project
    run_all(engine, pid)
    outline = next(p for shape, p in zip(client.calls, client.prompts, strict=True) if shape == "outline")
    assert "exactly 2 acts" in outline                                  # 30 s: two acts, not the default four
    planned = sum(s["render_payload"]["camera"]["duration_sec"] for s in engine.store.list_shots(pid))
    assert 25 <= planned <= 40                                          # shots planned to add up to about 30 s
    with pytest.raises(StageError) as e:
        engine.run_stage(pid, "script", {"target_sec": "soon", "force": True})
    assert e.value.code == "bad_target"


def test_a_project_target_is_validated(tmp_path: Path) -> None:
    from storyagent.config import ConfigError

    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    assert engine.projects.set_settings(pid, {"target_sec": 5400}).settings["target_sec"] == 5400   # a feature film
    with pytest.raises(ConfigError):
        engine.projects.set_settings(pid, {"target_sec": -3})
    assert "target_sec" not in engine.projects.set_settings(pid, {"target_sec": None}).settings
