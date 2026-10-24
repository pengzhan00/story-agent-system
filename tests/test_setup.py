"""The install guide: detection, what is missing, partial installs, the configuration it writes, the step it can run.

Everything runs against a made-up machine (``tests/setup_kit.py``): an empty home, a PATH with stub programs, canned
answers. What a real new Mac does with the commands is not covered here (see docs/INSTALL.md)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from storyagent.config import ConfigError, load_settings
from storyagent.setup import configgen
from storyagent.setup.layout import Layout, SetupError
from storyagent.setup.report import build_report
from storyagent.setup.runner import run_step
from storyagent.setup.tomlout import dumps

from .setup_kit import SUBTITLES, FakeMachine


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMachine:
    return FakeMachine(tmp_path, monkeypatch)


def state(report: Any, name: str) -> str:
    return str(report.component(name).state)


# -- the catalog itself ----------
def test_packaged_catalog_is_consistent(machine: FakeMachine) -> None:
    s = machine.settings()
    cfg = s.setup
    assert set(cfg.profiles) >= {"story", "stills", "full", "everything"}
    layout = Layout(s)
    for comp in cfg.components.values():
        for ref in comp.layout:
            kind, name, key = layout.split(ref)
            if kind == "tool":
                assert key in s.tools[name], f"{comp.name}: {ref} is not a key of [tools.{name}]"
            elif kind == "model":
                assert key in s.models[name].extra, f"{comp.name}: {ref} is not a key of models.{name}"
            else:
                assert key in s.launch[name], f"{comp.name}: {ref}"
        for step in comp.steps:
            for part in (*step.argv, step.cwd, step.show):
                layout.expand(part, item="x")           # every placeholder resolves
    healthy = {h for c in cfg.components.values() for h in c.health}
    known = set(s.services) | {f"tool.{t}" for t in s.tools}
    assert healthy <= known


def test_downloads_are_declared_and_nothing_dangerous_is_runnable(machine: FakeMachine) -> None:
    s = machine.settings()
    fetchers = {"hf", "ollama"}
    for comp in s.setup.components.values():
        for step in comp.steps:
            head = step.argv[0] if step.argv else ""
            if head in fetchers or "acestep-download" in step.argv:
                assert step.kind == "download", f"{step.id} fetches model files, so it is a download step"
            if step.kind == "download":
                assert step.source and step.file
            assert head not in {"sh", "bash", "zsh", "sudo", "rm", "kill", "pkill", "killall", "open"}, step.id
            assert not any(a in ("-c", "|", "&&", ";") for a in step.argv), f"{step.id}: no shell"


def test_a_broken_catalog_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = FakeMachine(tmp_path, monkeypatch)
    (m.data / "config" / "setup.toml").write_text(
        '[components.bad]\ncapability = "nope"\n[[components.bad.detect]]\nkind = "command"\nname = "x"\n')
    with pytest.raises(ConfigError, match="unknown capability"):
        m.settings()
    (m.data / "config" / "setup.toml").write_text(
        '[components.bad]\ncapability = "base"\n[[components.bad.detect]]\nkind = "psychic"\n')
    with pytest.raises(ConfigError, match="detect kind"):
        m.settings()
    (m.data / "config" / "setup.toml").write_text(
        '[components.bad]\ncapability = "base"\n[[components.bad.detect]]\nkind = "command"\nname = "x"\n'
        '[[components.bad.steps]]\nkind = "download"\nargv = ["hf"]\n')
    with pytest.raises(ConfigError, match="source and file"):
        m.settings()


# -- an empty machine ----------
def test_empty_machine_lists_everything_missing_with_steps(machine: FakeMachine) -> None:
    before = sorted(p.relative_to(machine.root) for p in machine.root.rglob("*"))
    r = build_report(machine.settings(), profile="full", probe=machine.probe())
    for name in ("homebrew", "uv", "ffmpeg_full", "ollama", "mflux", "cosyvoice_runtime", "acestep_runtime",
                 "phosphene"):
        assert state(r, name) in ("missing", "partial"), name
    assert not r.ready and r.next_step == "homebrew.1"
    assert state(r, "uv") == "missing" and r.component("uv").blocked_by == ("homebrew",)
    # the report only looks: nothing was created, and only read-only commands were asked about
    assert before == sorted(p.relative_to(machine.root) for p in machine.root.rglob("*"))
    assert {Path(a[0]).name for a in machine.runs} <= {"xcode-select", "brew", "uv", "ffmpeg"}
    assert not any(a[:2] == ["brew", "install"] for a in machine.runs)
    # steps are resolved against the machine's own home
    flux = next(s for s in r.component("flux_klein_base_4b").steps if s.kind == "download")
    assert str(machine.home / "models/flux2-klein-base-4B") in flux.shell and flux.shell.startswith("hf download")
    assert r.downloads and r.missing_gib > 100
    assert {d.id for d in r.downloads} >= {flux.id}


def test_the_download_list_names_file_source_and_size(machine: FakeMachine) -> None:
    r = build_report(machine.settings(), profile="full", probe=machine.probe())
    llm = next(d for d in r.downloads if d.component == "llm_model")
    assert llm.shell == "ollama pull gemma4:31b" and llm.size_gib == 20.0 and "ollama.com" in llm.source
    assert all(d.source and d.file for d in r.downloads) and all(d.kind == "download" for d in r.downloads)
    assert any(d.needs_service == "ollama" for d in r.downloads)


# -- a complete machine and partial installs ----------
def test_a_complete_machine_is_ready_for_every_profile(machine: FakeMachine) -> None:
    machine.install_everything_for_full()
    s = machine.settings()
    for profile in ("story", "stills", "full"):
        r = build_report(s, profile=profile, probe=machine.probe())
        todo = [c.name for c in r.components if c.need != "optional" and c.state not in ("ok", "unverified")]
        assert r.ready and not todo, (profile, todo)
    full = build_report(s, profile="full", probe=machine.probe())
    assert set(full.health_required) == {"tool.ffmpeg", "ollama", "tool.mflux", "tool.cosyvoice", "acestep",
                                         "phosphene"}
    assert state(full, "sfx_models") == "ok" and state(full, "klein_distilled_models") == "missing"


def test_partial_install_without_video_is_ready_for_stills_but_not_for_full(machine: FakeMachine) -> None:
    machine.install_base()
    machine.install_llm()
    machine.install_image()
    machine.install_voice()
    s = machine.settings()
    stills = build_report(s, profile="stills", probe=machine.probe())
    assert stills.ready                               # asr, music and sound effects are only recommended there
    assert [c.name for c in stills.components if c.need == "required" and c.state in ("missing", "partial")] == []
    assert stills.next_step.startswith("whisper_model")  # and the guide still points at what would come next
    full = build_report(s, profile="full", probe=machine.probe())
    assert not full.ready
    assert {c.name for c in full.components if c.need == "required" and c.state in ("missing", "partial")} == {
        "acestep_runtime", "acestep_weights", "pinokio", "phosphene"}
    assert full.component("acestep_weights").blocked_by == ("acestep_runtime",)


def test_story_profile_needs_only_the_language_model(machine: FakeMachine) -> None:
    machine.install_base()
    machine.install_llm()
    r = build_report(machine.settings(), profile="story", probe=machine.probe())
    assert r.ready and r.health_required == ("tool.ffmpeg", "ollama")
    assert state(r, "cosyvoice_runtime") == "missing" and r.component("cosyvoice_runtime").need == "optional"
    assert r.downloads == ()


def test_extra_capability_counts_as_required(machine: FakeMachine) -> None:
    machine.install_base()
    machine.install_llm()
    r = build_report(machine.settings(), profile="story", extra=("music",), probe=machine.probe())
    assert r.component("acestep_runtime").need == "required" and not r.ready
    with pytest.raises(SetupError, match="unknown_capability"):
        build_report(machine.settings(), profile="story", extra=("nonsense",), probe=machine.probe())
    with pytest.raises(SetupError, match="unknown_profile"):
        build_report(machine.settings(), profile="nope", probe=machine.probe())


# -- the individual checks ----------
def test_ffmpeg_without_libass_is_not_enough(machine: FakeMachine) -> None:
    machine.install_base()
    machine.filters = " .. scale  V->V\n"
    r = build_report(machine.settings(), profile="story", probe=machine.probe())
    assert state(r, "ffmpeg_full") == "missing"
    machine.filters = SUBTITLES
    assert state(build_report(machine.settings(), profile="story", probe=machine.probe()), "ffmpeg_full") == "ok"


def test_comfy_files_are_unverified_until_comfy_runs_then_seen(machine: FakeMachine) -> None:
    machine.install_base()
    (machine.apps / "Comfy Desktop.app").mkdir()
    s = machine.settings()
    assert state(build_report(s, profile="full", probe=machine.probe()), "sfx_models") == "unverified"
    machine.install_comfy({"CheckpointLoaderSimple": ["stable_audio_3_medium.safetensors"], "CLIPLoader": []})
    r = build_report(s, profile="full", probe=machine.probe())
    assert state(r, "sfx_models") == "partial"
    assert [(c.label, c.state) for c in r.component("sfx_models").checks] == [
        ("stable_audio_3_medium.safetensors", "ok"), ("t5gemma_b_b_ul2.safetensors", "missing")]


def test_ollama_models_come_from_the_running_server_when_there_is_one(machine: FakeMachine) -> None:
    machine.install_base()
    s = machine.settings()
    assert state(build_report(s, profile="story", probe=machine.probe()), "llm_model") == "missing"
    machine.services["11434/api/tags"] = {"models": [{"name": "gemma4:31b"}]}
    assert state(build_report(s, profile="story", probe=machine.probe()), "llm_model") == "ok"


def test_a_custom_llm_model_is_what_gets_pulled(machine: FakeMachine) -> None:
    machine.install_base()
    over = machine.data / "config" / "llm.toml"
    over.write_text('[llm.roles.default]\nmodel = "qwen3:8b"\n')
    r = build_report(machine.settings(), profile="story", probe=machine.probe())
    pulls = [s.shell for s in r.component("llm_model").steps]
    assert "ollama pull qwen3:8b" in pulls and "ollama pull gemma4:31b" in pulls


def test_machine_checks(machine: FakeMachine) -> None:
    machine.memory_gib = 32
    r = build_report(machine.settings(), profile="full", probe=machine.probe())
    mem = next(m for m in r.machine if m.key == "memory")
    assert mem.state == "fail" and mem.params["min"] == "36" and not r.ready       # video needs 36 GiB at least
    machine.memory_gib = 28
    story = build_report(machine.settings(), profile="story", probe=machine.probe())
    assert next(m for m in story.machine if m.key == "memory").state == "warn"       # over the 24 GiB minimum, under 32
    machine.memory_gib = 128
    big = build_report(machine.settings(), profile="full", probe=machine.probe())
    assert [m.state for m in big.machine] == ["ok"] * len(big.machine)


# -- install root ----------
def test_root_moves_the_paths_of_the_chosen_components(machine: FakeMachine, tmp_path: Path) -> None:
    root = tmp_path / "tools"
    s = machine.settings()
    r = build_report(s, profile="stills", root=root, probe=machine.probe())
    clone = next(x for x in r.component("cosyvoice_runtime").steps if x.argv[:2] == ("git", "clone"))
    assert clone.argv[-1] == str(root.resolve() / "cosyvoice/CosyVoice")
    dl = next(x for x in r.component("cosyvoice3_weights").steps if x.kind == "download")
    assert dl.argv[-1] == str(root.resolve() / "models/cosyvoice3/Fun-CosyVoice3-0.5B")
    files = {f.name: f for f in configgen.preview(s, r, probe=machine.probe(), root=root)}
    written = tomllib.loads(files["services.toml"].text)
    assert written["tools"]["cosyvoice"]["root"] == str(root.resolve() / "cosyvoice")
    assert tomllib.loads(files["models.toml"].text)["models"]["cosyvoice3-0.5b"]["model_dir"].endswith(
        "models/cosyvoice3/Fun-CosyVoice3-0.5B")
    configgen.write(list(files.values()))
    again = machine.settings()
    assert again.tool_path("cosyvoice", "python") == root.resolve() / "cosyvoice/venv/bin/python"
    assert str(again.models["cosyvoice3-0.5b"].extra["model_dir"]) == str(
        root.resolve() / "models/cosyvoice3/Fun-CosyVoice3-0.5B")
    # install into the root, and the guide finds it there with the configuration just written
    (root / "models/cosyvoice3/Fun-CosyVoice3-0.5B").mkdir(parents=True)
    (root / "models/cosyvoice3/Fun-CosyVoice3-0.5B/cosyvoice3.yaml").write_text("x")
    (root / "models/cosyvoice3/Fun-CosyVoice3-0.5B/llm.pt").write_text("x")
    assert state(build_report(again, profile="stills", probe=machine.probe()), "cosyvoice3_weights") == "ok"


# -- the configuration ----------
def test_config_for_a_profile_without_video_trims_the_plans(machine: FakeMachine) -> None:
    machine.install_base()
    machine.install_llm()
    machine.install_image()
    machine.install_voice()
    s = machine.settings()
    r = build_report(s, profile="stills", probe=machine.probe())
    files = {f.name: f for f in configgen.preview(s, r, probe=machine.probe(), root=None)}
    plans = tomllib.loads(files["pipelines.toml"].text)["pipelines"]
    assert "phosphene" not in plans["vertical_drama"]["requires"]
    assert plans["vertical_drama"]["workflow"] == {"video_share": 0.0, "video_min_per_episode": 0}
    assert tomllib.loads(files["services.toml"].text)["healthcheck"]["required"] == [
        "tool.ffmpeg", "ollama", "tool.mflux", "tool.cosyvoice"]
    configgen.write(list(files.values()))
    after = machine.settings()                                                   # the written files load
    assert "phosphene" not in after.pipelines["vertical_drama"].requires
    assert after.pipelines["vertical_drama"].workflow["video_share"] == 0.0
    from storyagent.workflow.tiering import policy_from
    pol = policy_from(after.for_pipeline("vertical_drama").workflow, None, 240)
    assert pol.share == 0.0 and pol.min_per_episode == 0                         # so no shot is picked for video


def test_config_picks_the_ffmpeg_that_has_libass_and_sets_the_memory_budget(machine: FakeMachine) -> None:
    machine.install_base()
    machine.ffmpeg.unlink()                                  # the configured path is gone ...
    machine.program("ffmpeg")                                # ... and the one on PATH has no libass
    brew_ff = machine.exe(machine.root / "brewprefix" / "bin" / "ffmpeg")
    machine.exe(brew_ff.with_name("ffprobe"))
    machine.brew_prefix = str(machine.root / "brewprefix")   # but Homebrew's ffmpeg-full is installed
    machine.memory_gib = 64
    s = machine.settings()
    r = build_report(s, profile="story", probe=machine.probe())
    files = {f.name: f for f in configgen.preview(s, r, probe=machine.probe(), root=None)}
    svc = tomllib.loads(files["services.toml"].text)
    assert svc["tools"]["ffmpeg"]["binary"] == str(brew_ff) and svc["tools"]["ffmpeg"]["ffprobe"].endswith("ffprobe")
    assert svc["runtime"]["memory_budget_gib"] == 48
    assert "[tools.ffmpeg]" in files["services.toml"].text


def test_config_for_a_machine_that_already_fits_is_empty(machine: FakeMachine) -> None:
    machine.install_everything_for_full()
    machine.services["11434/api/tags"] = {"models": [{"name": "gemma4:31b"}]}
    s = machine.settings()
    r = build_report(s, profile="full", probe=machine.probe())
    files = configgen.preview(s, r, probe=machine.probe(), root=None)
    names = {f.name for f in files if f.changed}
    assert names <= {"services.toml"}                        # only the healthcheck's required list moves
    assert configgen.write([f for f in files if not f.changed]) == []


def test_write_keeps_the_replaced_file_and_validates_first(machine: FakeMachine) -> None:
    machine.install_base()
    s = machine.settings()
    r = build_report(s, profile="story", probe=machine.probe())
    svc = machine.data / "config" / "services.toml"
    original = svc.read_text()
    files = configgen.preview(s, r, probe=machine.probe(), root=None, llm_model="qwen3:8b")
    assert {f.name for f in files} >= {"llm.toml", "services.toml"}
    configgen.validate(s, files)
    written = dict(configgen.write(files))
    assert svc in written and written[svc] is not None and written[svc].read_text() == original
    assert machine.settings().llm_roles["writer"].model == "qwen3:8b"
    kept = tomllib.loads(svc.read_text())["tools"]["ffmpeg"]["binary"]
    assert kept == str(machine.ffmpeg)                                               # the user's own key survived
    bad = [configgen.ConfigFile("services.toml", svc, True, True, (), '[healthcheck]\nrequired = ["bogus"]\n', "")]
    with pytest.raises(SetupError, match="config_invalid"):
        configgen.validate(s, bad)
    assert svc.read_text() != '[healthcheck]\nrequired = ["bogus"]\n'


def test_toml_writer_round_trips() -> None:
    data = {"a": 1, "b": {"c": "x\"y é", "d": [1, 2], "e": {"f": True, "g": 1.5}},
            "list": [{"k": "v", "n": 2}, {"k": "w"}], "weird key": {"models": {"x.y-1": {"m": "n"}}}}
    assert tomllib.loads(dumps(data)) == data


# -- the one step it can run ----------
def test_a_step_runs_only_when_confirmed_and_only_as_an_argv_list(machine: FakeMachine) -> None:
    machine.install_base()
    s = machine.settings()
    r = build_report(s, profile="stills", probe=machine.probe())
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def spawn(argv: Any, **kw: Any) -> Any:
        calls.append((list(argv), kw))
        return SimpleNamespace(returncode=0)

    step = next(x for x in r.component("flux_klein_base_4b").steps if x.kind == "download")
    up = lambda name: False                                                              # noqa: E731
    with pytest.raises(SetupError, match="not_confirmed"):
        run_step(r, step.id, probe=machine.probe(), service_up=up, confirmed=False, spawn=spawn)
    assert calls == []
    assert run_step(r, step.id, probe=machine.probe(), service_up=up, confirmed=True, spawn=spawn,
                    extra_path=[machine.home / ".local/bin"]) == 0
    argv, kw = calls[0]
    assert argv[0] == str(machine.bin / "hf") and argv[1:3] == ["download", "black-forest-labs/FLUX.2-klein-base-4B"]
    assert "shell" not in kw and str(machine.home / ".local/bin") in kw["env"]["PATH"]

    manual = r.component("homebrew").steps[0]
    with pytest.raises(SetupError, match="not_runnable"):
        run_step(r, manual.id, probe=machine.probe(), service_up=up, confirmed=True, spawn=spawn)
    pull = next(x for x in r.component("llm_model").steps)
    with pytest.raises(SetupError, match="service_down"):
        run_step(r, pull.id, probe=machine.probe(), service_up=up, confirmed=True, spawn=spawn)
    with pytest.raises(SetupError, match="unknown_step"):
        run_step(r, "nope.1", probe=machine.probe(), service_up=up, confirmed=True, spawn=spawn)
    (machine.bin / "hf").unlink()
    with pytest.raises(SetupError, match="tool_missing"):
        run_step(r, step.id, probe=machine.probe(), service_up=up, confirmed=True, spawn=spawn)
    assert len(calls) == 1


def test_defaults_still_parse_with_a_real_settings_object(tmp_path: Path) -> None:
    """No fake machine: the packaged catalog loads and the guard on literals holds for it."""
    s = load_settings({"STORY_AGENT_HOME": str(tmp_path / "h")})
    assert len(s.setup.components) >= 25 and s.setup.expand(["video"]) == ["base", "video"]
