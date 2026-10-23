"""``storyagent tool import comfy``: preview, save, refuse to overwrite, and the saved model is usable."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyagent.cli import main
from storyagent.config import get_settings

from .test_comfy_import import sd_graph


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    monkeypatch.setenv("STORY_AGENT_HOME", str(home))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    get_settings(reload=True)
    yield home
    monkeypatch.undo()
    get_settings(reload=True)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_import_previews_then_saves_a_model_the_system_can_load(
        home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wf = tmp_path / "My SD.json"
    wf.write_text(json.dumps(sd_graph()))
    code, out, _ = run(capsys, "tool", "import", "comfy", str(wf), "--kind", "image.generate", "--dry-run")
    assert code == 0 and "prompt  <-  2.text" in out and "Preview only" in out
    assert not (home / "config" / "workflows").exists()

    code, out, _ = run(capsys, "tool", "import", "comfy", str(wf), "--kind", "image.generate", "--name", "my-sd",
                       "--family", "sd15")
    assert code == 0 and "Saved the template" in out
    assert json.loads((home / "config" / "workflows" / "my-sd.json").read_text())["output"] == "7"
    model = get_settings(reload=True).models["my-sd"]
    assert (model.kind, model.provider, model.family, model.extra["workflow"]) == (
        "image.generate", "comfy", "sd15", "my-sd")
    assert model.params["steps"] == 20 and model.commercial_ok is False

    code, _, err = run(capsys, "tool", "import", "comfy", str(wf), "--kind", "image.generate", "--name", "my-sd")
    assert code == 1 and "already exists" in err
    code, _, _ = run(capsys, "tool", "import", "comfy", str(wf), "--kind", "image.generate", "--name", "my-sd",
                     "--force", "--timeout", "900")
    assert code == 0
    text = (home / "config" / "models.toml").read_text()
    assert text.count('[models."my-sd"]') == 1 and "timeout_sec = 900" in text
    assert get_settings(reload=True).models["my-sd"].timeout_sec == 900


def test_the_other_models_in_the_file_survive_a_replace(
        home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (home / "config" / "models.toml").write_text('[models."keep"]\nkind = "image.generate"\nprovider = "comfy"\n')
    wf = tmp_path / "a.json"
    wf.write_text(json.dumps(sd_graph()))
    for _ in range(2):
        assert run(capsys, "tool", "import", "comfy", str(wf), "--kind", "image.generate", "--name", "a",
                   "--force")[0] == 0
    models = get_settings(reload=True).models
    assert "keep" in models and "a" in models


def test_bad_input_is_refused_with_a_reason(home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ui = tmp_path / "ui.json"
    ui.write_text(json.dumps({"nodes": [], "links": []}))
    code, _, err = run(capsys, "tool", "import", "comfy", str(ui), "--kind", "image.generate")
    assert code == 1 and "not the API format" in err
    code, _, err = run(capsys, "tool", "import", "comfy", str(tmp_path / "missing.json"), "--kind", "image.generate")
    assert code == 1 and "Cannot read" in err
    wf = tmp_path / "ok.json"
    wf.write_text(json.dumps(sd_graph()))
    code, _, err = run(capsys, "tool", "import", "comfy", str(wf), "--kind", "image.generate", "--name", "Bad Name")
    assert code == 1 and "lower-case" in err
    code, _, err = run(capsys, "tool", "import", "comfy", str(wf), "--kind", "image.generate", "--name", "x",
                       "--ref", "flux2-klein-base-4b")
    assert code == 1 and "ships with the system" in err
