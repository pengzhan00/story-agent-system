"""The story pipeline from the command line, with the progress bar a person watches."""
from __future__ import annotations

import pytest

from storyagent import cli
from storyagent.agents import AgentRunner
from storyagent.llm import LLMRouter
from storyagent.progressbar import bar, duration, render_run, render_stages
from storyagent.workflow import WorkflowEngine

from .story_kit import StoryClient
from .test_cli_media import env, run  # noqa: F401  (fixture re-use)


def fake_engine(settings, db, **kw):  # noqa: ANN001, ANN003, ANN202
    router = LLMRouter(settings, db, clients={"ollama": StoryClient()})
    return WorkflowEngine(settings, db, router, agents=AgentRunner(settings, router), **kw)


def test_inline_run_draws_progress_and_finishes(env, capsys, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    monkeypatch.setattr(cli, "WorkflowEngine", fake_engine)
    assert run(capsys, "project", "create", "Rain")[0] == 0
    # give the project a premise through the store (the CLI has no edit command for it yet)
    from storyagent.config import get_settings
    from storyagent.db import Database
    from storyagent.projects import ProjectStore

    s = get_settings()
    ProjectStore(Database(s.db_path), s).update(1, description="A detective hunts a secret in the rain.")
    code, out = run(capsys, "workflow", "run", "--project", "1", "--inline", "--interval", "0.05",
                    "--param", "visual_prompts=false")
    assert code == 0, out
    assert "Queued run" in out and "Done" in out
    lines = [x for x in out.splitlines() if x.startswith("[")]
    assert lines and lines[0].startswith("[") and "%" in lines[0]
    assert any("100.0%" in x for x in lines) or "100.0%" in out
    code, out = run(capsys, "workflow", "status", "--project", "1")
    assert code == 0 and out.count("Done") == 7
    code, out = run(capsys, "workflow", "run", "--project", "1")
    assert code == 0 and "already completed" in out


def test_queue_only_and_unknown_project(env, capsys) -> None:  # noqa: ANN001, F811
    run(capsys, "project", "create", "Rain")
    code, out = run(capsys, "workflow", "run", "--project", "1", "--no-wait", "--stage", "analyze")
    assert code == 0 and "Queued run" in out
    code, out = run(capsys, "workflow", "status", "--project", "1")
    assert code == 0 and "Waiting for a worker" in out
    assert run(capsys, "workflow", "run", "--project", "99")[0] == 1


def test_rendering() -> None:
    assert bar(0, 10) == "[----------]" and bar(50, 10) == "[#####-----]" and bar(150, 4) == "[####]"
    assert duration(None) == "-" and duration(8) == "8s" and duration(80) == "1m20s" and duration(3700) == "1h01m"
    queued = {"status": "queued", "progress": {}}
    assert "Waiting for a worker" in render_run(queued)
    running = {"status": "running", "progress": {
        "percent": 42.0, "stage": "script", "stage_index": 3, "stage_count": 7, "done": 2, "total": 4,
        "message": "act 2", "elapsed_sec": 80, "eta_sec": 110,
        "stages": [{"stage": "analyze", "status": "completed", "percent": 100.0, "seconds": 8.0},
                   {"stage": "script", "status": "running", "percent": None, "seconds": None}]}}
    text = render_run(running)
    assert "42.0%" in text and "Step 3 of 7" in text and "Script" in text and "2 of 4" in text
    assert "Elapsed 1m20s" in text and "About 1m50s left" in text
    assert "Estimating" in render_run({**running, "progress": {**running["progress"], "eta_sec": None}})
    stages = render_stages(running)
    assert "100.0%" in stages[0] and "..." in stages[1]
