"""``storyagent setup``: check, plan, config, run, verify on a made-up machine."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from storyagent.cli import main
from storyagent.config import get_settings
from storyagent.media.contracts import HealthState
from storyagent.media.healthcheck import Entry, Report
from storyagent.setup import cli as setup_cli

from .setup_kit import FakeMachine


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMachine:
    m = FakeMachine(tmp_path, monkeypatch)
    monkeypatch.setattr(setup_cli, "make_probe", m.probe)
    get_settings(reload=True)
    yield m
    monkeypatch.undo()
    get_settings(reload=True)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_check_says_what_is_missing_and_exits_1(machine: FakeMachine, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "setup", "check", "--profile", "stills")
    assert code == 1 and "Homebrew" in out and "missing: brew" in out and "Something is missing" in out
    code, out, _ = run(capsys, "setup", "check", "--profile", "stills", "--json")
    data = json.loads(out)
    assert data["profile"] == "stills" and data["ready"] is False
    assert {c["name"] for c in data["components"]} >= {"homebrew", "cosyvoice_runtime"}


def test_check_exits_0_when_the_profile_is_there(machine: FakeMachine, capsys: pytest.CaptureFixture[str]) -> None:
    machine.install_base()
    machine.install_llm()
    code, out, _ = run(capsys, "setup", "check", "--profile", "story")
    assert code == 0 and "in place" in out


def test_plan_prints_commands_and_downloads_and_runs_nothing(
        machine: FakeMachine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from storyagent.setup import runner

    boom = SimpleNamespace(run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("a plan must not run anything")))
    monkeypatch.setattr(runner, "subprocess", boom)
    code, out, _ = run(capsys, "setup", "plan", "--profile", "stills")
    assert code == 0
    assert "$ brew install uv" in out and "[download]" in out
    assert "hf download black-forest-labs/FLUX.2-klein-base-4B" in out
    assert "Downloads waiting for your OK" in out and "Nothing has been downloaded" in out
    assert str(machine.home / "models/flux2-klein-base-4B") in out
    code, everything, _ = run(capsys, "setup", "plan", "--profile", "story", "--all")
    assert "CosyVoice" in everything and "MiniMax H3 weights" in everything


def test_config_previews_by_default_and_writes_on_request(
        machine: FakeMachine, capsys: pytest.CaptureFixture[str]) -> None:
    machine.install_base()
    before = (machine.data / "config" / "services.toml").read_text()
    code, out, _ = run(capsys, "setup", "config", "--profile", "stills", "--llm-model", "qwen3:8b")
    assert code == 0 and "Not written" in out and "healthcheck.required" in out and "llm.roles.writer.model" in out
    assert (machine.data / "config" / "services.toml").read_text() == before
    assert not (machine.data / "config" / "llm.toml").exists()
    code, out, _ = run(capsys, "setup", "config", "--profile", "stills", "--llm-model", "qwen3:8b", "--write")
    assert code == 0 and "Written:" in out and "previous version kept as" in out
    assert (machine.data / "config" / "llm.toml").is_file()
    assert list((machine.data / "config").glob("services.toml.bak-setup-*"))
    get_settings(reload=True)
    assert get_settings().llm_roles["writer"].model == "qwen3:8b"
    code, out, _ = run(capsys, "setup", "config", "--profile", "stills", "--llm-model", "qwen3:8b")
    assert "nothing to write" in out                                          # a second run changes nothing


def test_run_refuses_without_confirmation_and_runs_one_step_with_yes(
        machine: FakeMachine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from storyagent.setup import runner

    machine.install_base()
    seen: list[list[str]] = []

    def fake_run(argv: Any, **kw: Any) -> Any:
        seen.append(list(argv))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=fake_run))
    code, out, err = run(capsys, "setup", "run", "flux_klein_base_4b.2", "--profile", "stills")
    assert code == 1 and "not confirmed" in err.lower() or "confirm" in err.lower()
    assert "download:" in out and seen == []                                 # source, file and size were shown first
    code, out, _ = run(capsys, "setup", "run", "flux_klein_base_4b.2", "--profile", "stills", "--yes")
    assert code == 0 and len(seen) == 1 and seen[0][1] == "download" and "finished with exit code 0" in out
    code, _, err = run(capsys, "setup", "run", "homebrew.1", "--yes")
    assert code == 1 and "by hand" in err and len(seen) == 1


def test_verify_counts_what_the_profile_needs_as_required(
        machine: FakeMachine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[Any] = []

    def fake_checks(settings: Any, **kw: Any) -> Report:
        asked.append(kw.get("require"))
        return Report([Entry("ollama", "service", HealthState.NOT_RUNNING, "x", required=True)])

    monkeypatch.setattr(setup_cli.health, "run_checks", fake_checks)
    code, _, _ = run(capsys, "setup", "verify", "--profile", "story")
    assert code == 1 and asked == [["tool.ffmpeg", "ollama"]]


def test_the_guided_walk_prints_everything_and_asks_nothing_without_a_terminal(
        machine: FakeMachine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    ok = Report([Entry("ollama", "service", HealthState.OK)])
    monkeypatch.setattr(setup_cli.health, "run_checks", lambda s, **kw: ok)
    code, out, _ = run(capsys, "setup", "--profile", "story")
    assert "Setup guide" in out and "What to install" in out and "Configuration for this machine" in out
    assert "Not written" in out and not (machine.data / "config" / "llm.toml").exists()
    run(capsys, "setup", "--profile", "story", "--write-config")
    assert "healthcheck" in (machine.data / "config" / "services.toml").read_text()
    code, out, _ = run(capsys, "setup", "profiles")
    assert code == 0 and "stills" in out and "full" in out
