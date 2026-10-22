"""The creation wizard on the command line: estimate, create with a kind, change the plan, health for a plan."""
from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.cli import main
from storyagent.config import get_settings


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    monkeypatch.setenv("STORY_AGENT_HOME", str(home))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    monkeypatch.delenv("STORY_AGENT_LEGACY_DB", raising=False)
    get_settings(reload=True)
    yield home
    monkeypatch.undo()
    get_settings(reload=True)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_estimate_create_update_and_list_with_a_kind_and_plan(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "project", "estimate", "--kind", "drama_series", "--seasons", "1", "--episodes", "4",
                       "--episode-sec", "60")
    assert code == 0 and "4 episodes" in out and "image.edit" in out and "Machine time" in out
    code, out, _ = run(capsys, "project", "create", "Saga", "--kind", "drama_series", "--seasons", "2",
                       "--episodes", "5", "--episode-sec", "90")
    assert code == 0 and "vertical_drama" in out and "9:16" in out and "10 episodes" in out
    assert "drama_series\tvertical_drama" in run(capsys, "project", "list")[1]
    code, out, _ = run(capsys, "project", "update", "1", "--pipeline", "landscape_drama", "--episodes", "8")
    assert code == 0 and "landscape_drama" in out and "16:9" in out and "16 episodes" in out
    assert run(capsys, "project", "update", "1", "--pipeline", "nope")[0] == 2           # a bad plan: config error
    assert run(capsys, "project", "estimate", "--kind", "advert", "--seasons", "3")[0] == 2


def test_health_can_be_asked_for_one_plan(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain = run(capsys, "media", "healthcheck", "--json")[1]
    planned = run(capsys, "media", "healthcheck", "--json", "--pipeline", "advert")[1]
    assert planned.count('"required": true') > plain.count('"required": true')
    assert run(capsys, "media", "healthcheck", "--pipeline", "nope")[0] == 2
