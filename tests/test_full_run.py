"""One run from the idea to the film: the story, every asset, the cut; what it needs is looked at before it starts,
a few failed assets do not stop it, and people without a voice get one."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from storyagent.domain import MediaKind
from storyagent.media.adapters.assemble import AssembleAdapter
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.contracts import ArtifactSpec, HealthReport, HealthState, JobContext, JobRecord
from storyagent.media.probe import find_tool, probe
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker
from storyagent.workflow import STAGES, StageError, WorkflowEngine, preflight

from .adapter_kit import make_settings, wav_bytes
from .story_kit import NAMES, StoryClient, make_engine, new_project
from .test_workflow_media import FAKE_MODELS, SERVICES, serving

STORY = [s for s in STAGES if s not in ("outline", "produce", "compose")]


class RealMedia(FakeAdapter):
    """The fake adapter, but what it makes is a small real picture, sound or clip (so the film can be cut from it).
    ``down`` = its service does not answer; ``fail_subjects`` = spec subject types whose jobs fail."""

    def __init__(self, media: Path) -> None:
        super().__init__()
        self.media, self.down = media, False
        self.fail_kinds: set[str] = set()

    def health(self) -> HealthReport:
        return HealthReport(HealthState.NOT_RUNNING if self.down else HealthState.OK)

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        if job.kind.value in self.fail_kinds:
            return []                                    # claims success, wrote nothing: the worker fails the job
        name = ("still.png" if job.kind.value.startswith("image") else
                "clip.mp4" if job.kind.value.startswith("video") else "line.wav")
        out = ctx.work_dir / name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.media / name, out)
        return [ArtifactSpec(out, metadata={"fake": True})]


def _rig(tmp_path: Path, client: StoryClient | None = None) -> tuple[WorkflowEngine, Worker, RealMedia]:
    tool = find_tool(make_settings(tmp_path / "probe"), "ffmpeg")
    if tool is None:
        pytest.skip("ffmpeg not installed")
    media = tmp_path / "src"
    media.mkdir()
    subprocess.run([str(tool), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=128x224:rate=12:duration=1", "-pix_fmt", "yuv420p", str(media / "clip.mp4")],
                   check=True)
    subprocess.run([str(tool), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=128x224",
                    "-frames:v", "1", str(media / "still.png")], check=True)
    (media / "line.wav").write_bytes(wav_bytes(1.0))
    engine, _, settings = make_engine(tmp_path, client, toml=FAKE_MODELS, services=SERVICES)
    new_project(engine)
    fake = RealMedia(media)
    reg = AdapterRegistry()
    reg.register(fake)
    reg.register(AssembleAdapter(poll_interval_sec=0.02))
    worker = Worker(settings, engine.db, reg, ledger=engine.spec_store.ledger, probes=[], workflow=engine)
    return engine, worker, fake


def _full(engine: WorkflowEngine, worker: Worker, **params: Any) -> dict[str, Any]:
    params = {"full": True, "resume": True, "visual_prompts": False, "burn_subtitles": False, **params}
    plan = engine.state.plan(1, None, params)
    run = engine.state.enqueue(1, preflight.run_stages(plan), params)
    try:
        assert worker.drain(timeout=120)
    finally:
        worker.shutdown()
    return engine.state.get_run(run["id"])


def test_a_plain_resume_stops_at_the_story_and_a_full_one_goes_to_the_film(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    assert [s for _, s in engine.state.plan(pid, None, {})] == STORY
    assert [s for _, s in engine.state.plan(pid, None, {"full": True})] == [*STORY, "produce", "compose"]
    # naming stages is a different request: `full` does not add to it
    assert [s for _, s in engine.state.plan(pid, ["script"], {"full": True})] == ["script"]
    engine.run(pid, None, {"visual_prompts": False})
    assert engine.state.plan(pid, None, {}) == []
    assert [s for _, s in engine.state.plan(pid, None, {"full": True})] == ["produce", "compose"]
    # two episodes: each goes from its own story to its own film, one after the other
    two = engine.state.plan(pid, None, {"full": True, "episodes": "1-2"})
    assert two[:2] == [(1, "produce"), (1, "compose")] and two[2] == (2, "script") and two[-1] == (2, "compose")


def test_a_resume_also_redoes_what_the_stale_stage_makes_stale(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, None, {"visual_prompts": False})
    # the script was written again and only the shot plan and the specs were redone by hand: the locations and the
    # music are stale. One resume redoes them and what is built on them, not just the two stale stages
    engine.run(pid, ["script", "shotplan", "specs"], {"visual_prompts": False, "force": True})
    states = {s["stage"]: s["status"] for s in engine.state.stages(pid)}
    assert states["scenes"] == states["music"] == "stale" and states["specs"] == "completed"
    assert [s for _, s in engine.state.plan(pid, None, {})] == ["scenes", "music", "shotplan", "specs"]
    assert [s for _, s in engine.state.plan(pid, None, {"full": True})] == [
        "scenes", "music", "shotplan", "specs", "produce", "compose"]


def test_one_run_makes_the_film_from_the_idea(tmp_path: Path) -> None:
    engine, worker, _ = _rig(tmp_path)
    run = _full(engine, worker)
    assert run["status"] == "completed", (run["error_code"], run["error_message"])
    stages = {s["stage"]: s for s in engine.state.stages(1)}
    assert all(stages[s]["status"] == "completed" for s in (*STORY, "produce", "compose"))
    assert stages["script"]["summary"]["story_check"]["outline"]["open_high"] == 0
    assert stages["produce"]["summary"]["failed"] == 0 and stages["produce"]["summary"]["made"] > 0
    film = engine.spec_store.ledger.get_asset(stages["compose"]["summary"]["asset_id"])
    info = probe(engine.settings, engine.settings.home / film.path)
    assert info and info.has_video and info.has_audio and (info.duration_sec or 0) > 5
    assert engine.state.plan(1, None, {"full": True}) == []                # nothing left: the next click says so


def test_a_few_failed_assets_do_not_stop_the_film_and_only_they_are_made_again(tmp_path: Path) -> None:
    engine, worker, fake = _rig(tmp_path)
    fake.fail_kinds = {MediaKind.VIDEO_GENERATE.value}
    run = _full(engine, worker)
    assert run["status"] == "completed", (run["error_code"], run["error_message"])
    stages = {s["stage"]: s for s in engine.state.stages(1)}
    produce = stages["produce"]
    # the stage says it failed and keeps what happened: which assets, why, and what was made
    assert produce["status"] == "failed" and produce["error_code"] == "workflow.produce_failed"
    assert produce["summary"]["failed"] == 8 and produce["summary"]["made"] > 0
    assert {f["subject"] for f in produce["summary"]["failures"]} == {"shot.video"}
    # the film was still cut, from the keyframes, and says which shots have no clip
    assert stages["compose"]["status"] == "completed"
    assert {w["code"] for w in stages["compose"]["summary"]["warnings"]} >= {"no_video"}
    # running all the way again makes only the clips, then cuts the film again
    assert [s for _, s in engine.state.plan(1, None, {"full": True})] == ["produce", "compose"]
    fake.fail_kinds = set()
    worker2 = Worker(engine.settings, engine.db, worker.registry, ledger=engine.spec_store.ledger, probes=[],
                     workflow=engine)
    again = _full(engine, worker2)
    assert again["status"] == "completed"
    stages = {s["stage"]: s for s in engine.state.stages(1)}
    assert stages["produce"]["status"] == "completed" and stages["produce"]["summary"]["made"] == 8
    assert "no_video" not in {w["code"] for w in stages["compose"]["summary"]["warnings"]}


def test_when_nothing_at_all_could_be_made_no_film_is_cut_from_nothing(tmp_path: Path) -> None:
    engine, worker, fake = _rig(tmp_path)
    fake.fail_kinds = {k.value for k in MediaKind}
    run = _full(engine, worker)
    assert run["status"] == "failed" and run["error_code"] == "workflow.produce_failed"
    stages = {s["stage"]: s for s in engine.state.stages(1)}
    assert stages["produce"]["summary"]["made"] == 0 and stages["compose"]["status"] == "pending"


def test_without_the_full_flag_a_failed_asset_still_fails_the_run(tmp_path: Path) -> None:
    engine, worker, fake = _rig(tmp_path)
    engine.run(1, None, {"visual_prompts": False})
    fake.fail_kinds = {MediaKind.VIDEO_GENERATE.value}
    with serving(worker), pytest.raises(StageError) as exc:
        engine.run(1, ["produce", "compose"], {"burn_subtitles": False})
    assert exc.value.code == "produce_failed"
    assert {s["stage"]: s["status"] for s in engine.state.stages(1)}["compose"] == "pending"


def test_a_service_that_is_not_running_stops_produce_before_the_first_job(tmp_path: Path) -> None:
    engine, worker, fake = _rig(tmp_path)
    fake.down = True
    run = _full(engine, worker)
    assert run["status"] == "failed" and run["error_code"] == "workflow.not_ready"
    assert "fake: service_down" in run["error_message"]
    assert engine.spec_store.ledger.list_jobs(project_id=1, limit=10) == []      # not one job was queued
    stages = {s["stage"]: s["status"] for s in engine.state.stages(1)}
    assert stages["specs"] == "completed" and stages["produce"] == "failed"      # the story is kept


def test_the_look_ahead_names_what_is_missing(tmp_path: Path) -> None:
    engine, _, fake = _rig(tmp_path)
    reg = AdapterRegistry()
    reg.register(fake)
    cfg = engine.settings
    args: dict[str, Any] = dict(project_id=1, project_settings={}, characters=[
        {"name": "Lin Wan", "voice_ref_audio": ""}, {"name": "Zhao Heng", "voice_ref_audio": "library:voice/Reed"}],
        default_voice="", library_voices=3)
    found = preflight.look_ahead(cfg, cfg, engine.router, reg, stages=["produce", "compose"], specs=None, **args)
    # the assembler has no adapter in this registry; the one person without a voice will be given one
    assert [(f.code, f.level, f.subject) for f in found] == [
        ("no_adapter", "block", "ffmpeg"), ("voices_auto", "warn", "Lin Wan")]
    fake.down = True
    found = preflight.look_ahead(cfg, cfg, engine.router, reg, stages=["produce"], specs=None,
                                 **{**args, "library_voices": 0})
    assert [(f.code, f.level, f.subject) for f in found] == [
        ("service_down", "block", "fake"), ("no_voice", "warn", "Lin Wan")]
    assert "f-img" in found[0].detail and "f-vid" in found[0].detail
    # a speech model that reads without a reference voice: the lines are spoken, all in its one voice
    people = args["characters"]
    assert [f.code for f in preflight.check_voices(people, "", 0, automatic=True)] == ["voice_shared"]
    assert [f.code for f in preflight.check_voices(people, "", 3, automatic=True)] == ["voices_auto"]
    # nothing left to make and nothing to cut: nothing is asked of anyone
    assert preflight.look_ahead(cfg, cfg, engine.router, reg, stages=["produce"], specs=[], **{
        **args, "default_voice": "library:voice/x"}) == []
    # a tool that runs as a subprocess has no service to ask: its missing weights are read from the tool check
    fake.down = False
    tools = {"fake": (HealthState.MISSING_MODEL, "FLUX weights"), "other": (HealthState.MISSING_TOOL, "x")}
    found = preflight.check_media(cfg, reg, None, {}, compose=False, tools=tools)
    assert [(f.code, f.level, f.subject) for f in found] == [("not_installed", "block", "fake")]
    assert found[0].detail.endswith(": FLUX weights")
    assert set(preflight.tool_states(cfg)) >= {"mflux", "cosyvoice", "ffmpeg"}
    gate = [{"reason": "adult_not_confirmed", "name": "Lin Wan", "detail": ""}]
    found = preflight.look_ahead(cfg, cfg, engine.router, reg, stages=["produce"], specs=[], gates=gate,
                                 **{**args, "default_voice": "library:voice/x"})
    assert [(f.code, f.subject, f.detail) for f in found] == [("gate", "adult_not_confirmed", "Lin Wan")]


def test_a_language_model_that_is_missing_is_named_with_its_role(tmp_path: Path) -> None:
    client = StoryClient()
    client.list_models = lambda: ["some-other-model"]                  # type: ignore[method-assign]
    engine, _, settings = make_engine(tmp_path, client)
    new_project(engine)
    found = preflight.check_llm(settings, engine.router, 1, STORY)
    assert found and {f.code for f in found} == {"llm_model_missing"} and all(f.level == "block" for f in found)
    assert {f.detail for f in found} >= {"writer", "director"}
    assert preflight.check_llm(settings, engine.router, 1, ["produce", "compose"]) == []     # no agents there


def test_a_full_run_gives_voices_to_the_people_who_have_none(tmp_path: Path) -> None:
    from .test_voices import _voice

    engine, worker, _ = _rig(tmp_path)
    engine.run(1, None, {"visual_prompts": False})
    for name, gender in zip(NAMES, ("female", "male"), strict=True):
        engine.store.update_character(engine.store.find_character(1, name)["id"], {"gender": gender})
    _voice(engine, tmp_path, "Lan", "adult female")
    _voice(engine, tmp_path, "Reed", "adult male")
    run = _full(engine, worker)
    assert run["status"] == "completed", (run["error_code"], run["error_message"])
    produce = {s["stage"]: s for s in engine.state.stages(1)}["produce"]["summary"]
    assert produce["voices_given"] == NAMES and produce["voices_missing"] == []
    assert "no_voice" not in {s["reason"] for s in produce["skipped_detail"]}
    spoken = [j for j in engine.spec_store.ledger.list_jobs(project_id=1, limit=500) if j.kind is MediaKind.AUDIO_TTS
              and j.status.value == "completed"]
    assert spoken and all(str(j.inputs.get("voice_ref", "")) or j.params.get("voice_ref") for j in spoken)
