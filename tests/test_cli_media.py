"""End-to-end through the CLI: project -> spec -> regenerate -> worker -> jobs/show -> adopt."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyagent.cli import main
from storyagent.config import get_settings

from .conftest import FAKE_MODELS, FAKE_SERVICES


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "models.toml").write_text(FAKE_MODELS)
    (home / "config" / "services.toml").write_text(FAKE_SERVICES)
    monkeypatch.setenv("STORY_AGENT_HOME", str(home))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    monkeypatch.setenv("STORY_AGENT_ENABLE_FAKE", "1")
    monkeypatch.delenv("STORY_AGENT_LEGACY_DB", raising=False)
    get_settings(reload=True)
    assert main(["db", "migrate"]) == 0
    yield home
    monkeypatch.undo()
    get_settings(reload=True)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_full_creative_loop(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "project", "create", "Demo Film")[0] == 0
    code, out = run(capsys, "project", "list")
    assert code == 0 and "demo-film" in out

    subj = ("--project", "1", "character.portrait", "hero")
    code, out = run(capsys, "spec", "create", *subj, "--kind", "video.generate", "--model", "fake-video",
                    "--prompt", "a brave knight", "--seed", "7", "--param", "fps=12")
    assert code == 0 and "(v1)" in out

    assert run(capsys, "spec", "edit", *subj, "--prompt", "a brave knight, dusk", "--note", "mood")[0] == 0
    code, out = run(capsys, "spec", "show", *subj, "--history")
    assert code == 0 and out.count("\n") == 2
    code, out = run(capsys, "spec", "diff", *subj, "1", "2")
    assert code == 0 and "dusk" in out
    code, out = run(capsys, "spec", "show", *subj)
    shown = json.loads(out)
    assert shown["version"] == 2 and shown["prompt"].endswith("dusk") and shown["locked"] is False

    code, out = run(capsys, "spec", "regenerate", *subj, "-n", "2", "--tier", "draft")
    assert code == 0
    job_ids = [ln for ln in out.splitlines() if len(ln) == 32 or "-" in ln][-2:]
    assert len(job_ids) == 2

    assert run(capsys, "worker", "--once", "--timeout", "30")[0] == 0
    code, out = run(capsys, "media", "jobs", "--status", "completed")
    assert code == 0 and all(j in out for j in job_ids)

    code, out = run(capsys, "media", "show", job_ids[0])
    assert code == 0
    assert "spec" in out.lower() or job_ids[0] in out


def test_locked_spec_refuses_edit_with_exit_5(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    subj = ("--project", "1", "music.bgm", "main")
    run(capsys, "project", "create", "P")
    assert run(capsys, "spec", "create", *subj, "--kind", "video.generate", "--model", "fake-video",
               "--prompt", "x")[0] == 0
    assert run(capsys, "spec", "lock", *subj, "--note", "final")[0] == 0
    assert run(capsys, "spec", "edit", *subj, "--prompt", "y")[0] == 5
    assert run(capsys, "spec", "unlock", *subj)[0] == 0
    assert run(capsys, "spec", "edit", *subj, "--prompt", "y")[0] == 0
    assert run(capsys, "spec", "revert", *subj, "1")[0] == 0


def test_submit_dedup_cancel_retry(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "project", "create", "P")
    args = ("media", "submit", "--kind", "video.generate", "--model", "fake-video", "--project", "1",
            "--prompt", "hello", "--seed", "3")
    code, first = run(capsys, *args)
    assert code == 0 and first.startswith("Queued")
    code, second = run(capsys, *args)
    assert code == 0 and "already" in second.lower()
    job_id = first.split()[1]
    code, out = run(capsys, "media", "jobs")
    assert code == 0 and job_id in out
    assert run(capsys, "media", "cancel", job_id)[0] == 0
    assert run(capsys, "media", "retry", job_id, "--new-seed")[0] == 0


def test_unknown_spec_is_not_found_exit_1(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "spec", "show", "--project", "1", "music.bgm", "nope")[0] == 1


def test_library_through_the_cli(env: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert run(capsys, "project", "create", "Demo")[0] == 0
    wav = tmp_path / "lin.wav"
    wav.write_bytes(b"RIFFfake")
    code, out = run(capsys, "asset", "import", str(wav), "--project", "1")
    assert code == 0 and "upload.audio" in out
    asset_id = out.split()[2]
    code, out = run(capsys, "library", "add", asset_id, "--asset", "--role", "voice", "--name", "Lin",
                    "--text", "hello", "--consent", "--tag", "zh")
    assert code == 0 and "library:voice/Lin" in out
    code, out = run(capsys, "library", "add", str(wav), "--role", "voice", "--name", "Lin")
    assert code == 1  # name taken
    code, out = run(capsys, "library", "list", "--role", "voice")
    assert code == 0 and "library:voice/Lin" in out and "zh" in out
    item_id = out.split("\t")[0]
    code, out = run(capsys, "library", "show", item_id)
    assert json.loads(out)["meta"] == {"text": "hello", "consent": True}
    code, out = run(capsys, "library", "remove", item_id)
    assert code == 0 and run(capsys, "library", "list")[1].strip() == ""
