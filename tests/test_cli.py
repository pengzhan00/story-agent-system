from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.cli import main
from storyagent.config import get_settings

from .legacy_db import build_legacy_db


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("STORY_AGENT_HOME", str(home))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    monkeypatch.delenv("STORY_AGENT_LEGACY_DB", raising=False)
    get_settings(reload=True)
    yield home
    monkeypatch.undo()
    get_settings(reload=True)


def test_migrate_then_status(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["db", "migrate"]) == 0
    assert "Migrated to version 1" in capsys.readouterr().out
    assert main(["db", "migrate"]) == 0
    assert "already up to date" in capsys.readouterr().out
    assert main(["db", "status"]) == 0
    assert capsys.readouterr().out.strip() == "1"


def test_import_legacy_cli(cli_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = build_legacy_db(tmp_path / "old.db")
    assert main(["db", "import-legacy", "--from", str(src)]) == 0
    assert "2 projects, 3 shots" in capsys.readouterr().out


def test_import_without_source_exits_2(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["db", "import-legacy"]) == 2


def test_import_missing_file_exits_4(cli_env: Path, tmp_path: Path) -> None:
    assert main(["db", "import-legacy", "--from", str(tmp_path / "nope.db")]) == 4


def test_i18n_check_and_config_show(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["i18n", "check"]) == 0
    assert main(["config", "show"]) == 0
    assert str(cli_env) in capsys.readouterr().out


def test_bad_config_exits_2(cli_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORY_AGENT_OLLAMA_UNLOAD_POLICY", "yolo")
    get_settings.__globals__["_cached"] = None
    assert main(["config", "show"]) == 2
