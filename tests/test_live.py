"""Live smoke tests: real Ollama, real image/music tools, real ffmpeg. Run with ``pytest -m live`` on the dev machine.

They write only into a temporary home and skip themselves when a required service or tool is missing.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from storyagent.config import load_settings
from storyagent.db import Database, migrate
from storyagent.media.contracts import HealthState
from storyagent.media.healthcheck import run_checks
from storyagent.media.probe import find_tool, probe
from storyagent.media.registry import default_registry
from storyagent.media.worker import Worker
from storyagent.workflow import DEFAULT_STAGES, WorkflowEngine

pytestmark = pytest.mark.live


def test_the_machine_reports_a_coherent_health_table(tmp_path: Path) -> None:
    settings = load_settings({"STORY_AGENT_HOME": str(tmp_path / "home")})
    report = run_checks(settings)
    assert report.entries, "healthcheck returned nothing"


def test_one_sentence_to_a_playable_episode(tmp_path: Path) -> None:
    settings = load_settings({"STORY_AGENT_HOME": str(tmp_path / "home"), "STORY_AGENT_LOCALE": "en"})
    state = {e.name: e.state for e in run_checks(settings).entries}
    for needed in ("ollama", "adapter.mflux", "adapter.acestep", "adapter.ffmpeg"):
        if state.get(needed) is not HealthState.OK:
            pytest.skip(f"{needed} is not available: {state.get(needed)}")
    if find_tool(settings, "ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    db = Database(settings.db_path)
    migrate(db)
    engine = WorkflowEngine(settings, db)
    pid = engine.projects.create("Smoke", description="A night clerk finds a stranger who keeps buying umbrellas.").id
    worker = Worker(settings, db, default_registry(settings), ledger=engine.spec_store.ledger)
    stop = threading.Event()
    thread = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True)
    thread.start()
    try:
        story = {"acts": 1, "scenes_per_act": 1, "shots_per_scene": 2, "visual_prompts": False}
        for stage in DEFAULT_STAGES:
            engine.run_stage(pid, stage, story)
        produced = engine.run_stage(pid, "produce", {"skip": ["shot.video"], "tier": "draft"})
        assert produced["failed"] == 0, produced
        composed = engine.run_stage(pid, "compose", {"burn_subtitles": False})
    finally:
        stop.set()
        thread.join(timeout=30)
    asset = engine.spec_store.ledger.get_asset(composed["asset_id"])
    info = probe(settings, settings.home / asset.path)
    assert info and info.has_video and info.has_audio and (info.width, info.height) == (720, 1280)
    assert composed["qc"]["passed"], composed["qc"]["checks"]
