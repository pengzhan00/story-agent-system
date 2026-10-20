from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import requests

from storyagent.config import ConfigError, Settings, load_settings
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.contracts import HealthReport, HealthState
from storyagent.media.healthcheck import format_report, main, run_checks
from storyagent.media.registry import AdapterRegistry

from .fakes.http_server import FakeServer


def cfg(tmp_path: Path, services: str = "", **tools: str) -> Settings:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    toml = services
    for name, kv in tools.items():
        toml += f"\n[tools.{name}]\n{kv}\n"
    (home / "config" / "services.toml").write_text(toml)
    return load_settings({"STORY_AGENT_HOME": str(home)})


def exe(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def by_name(report) -> dict:  # noqa: ANN001
    return {e.name: e for e in report.entries}


NO_LLM: list = []


def services_toml(urls: dict[str, str], required: list[str]) -> str:
    out = ""
    for name, url in urls.items():
        out += f'\n[services.{name}]\nurl = "{url}"\nhealth_path = "/health"\n'
    return out + f"\n[healthcheck]\nrequired = {json.dumps(required)}\n"


def test_service_states(tmp_path: Path) -> None:
    with FakeServer() as up, FakeServer() as sick:
        up.route("GET", "/health", {"ok": True})
        sick.route("GET", "/health", (503, {"error": "x"}))
        s = cfg(tmp_path, services_toml(
            {"comfy": up.url, "acestep": sick.url, "ollama": "http://127.0.0.1:9", "nodus": up.url},
            ["comfy"]))
        rep = run_checks(s, probes=NO_LLM)
    e = by_name(rep)
    assert e["comfy"].state is HealthState.OK
    assert e["acestep"].state is HealthState.DEGRADED and "503" in e["acestep"].detail
    assert e["ollama"].state is HealthState.NOT_RUNNING
    assert e["nodus"].state is HealthState.PROBE_ONLY  # alive, but we never manage it
    assert e["llamacpp"].state is HealthState.DISABLED
    assert rep.exit_code == 0


def test_required_entries_drive_the_exit_code(tmp_path: Path) -> None:
    with FakeServer() as up:
        up.route("GET", "/health", {})
        s = cfg(tmp_path, services_toml({"comfy": up.url, "acestep": "http://127.0.0.1:9"}, ["comfy", "acestep"]))
        rep = run_checks(s, probes=NO_LLM)
        assert rep.exit_code == 1 and [e.name for e in rep.failing_required] == ["acestep"]
        assert run_checks(s, probes=NO_LLM, require=["comfy"]).exit_code == 1
        s2 = cfg(tmp_path / "b", services_toml({"comfy": up.url, "acestep": "http://127.0.0.1:9"}, ["comfy"]))
        assert run_checks(s2, probes=NO_LLM).exit_code == 0  # acestep down but not required


def test_unknown_required_name_is_a_config_error(tmp_path: Path) -> None:
    s = cfg(tmp_path)
    with pytest.raises(ConfigError, match="do not exist"):
        run_checks(s, probes=NO_LLM, require=["tool.nope"])
    with pytest.raises(ConfigError, match="unknown entries"):
        cfg(tmp_path / "b", '[healthcheck]\nrequired = ["bogus"]')


def test_tool_states_distinguish_missing_tool_from_missing_model(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    h3_dir = root / "MiniMax-H3"
    s = cfg(
        tmp_path,
        h3=f'binary = "{exe(root / "h3")}"\nmodel_dir = "{h3_dir}"',
        longcat=f'root = "{root / "lc"}"\npython = "{root / "nope"}"\nweights_dir = "{root}"\nvariant_dir = "v"',
        cosyvoice=(f'python = "{exe(root / "cpy")}"\nscript = "{exe(root / "clone.py")}"\n'
                   f'model_dir = "{root / "missing_model"}"'),
        lora_train=f'command = "{exe(root / "mflux-train")}"\nbase_model_dir = "{root}"',
    )
    e = by_name(run_checks(s, probes=NO_LLM))
    assert e["tool.h3"].state is HealthState.MISSING_MODEL  # binary there, weights not
    (h3_dir).mkdir()
    (h3_dir / "model_index.json").write_text("{}")
    assert by_name(run_checks(s, probes=NO_LLM))["tool.h3"].state is HealthState.OK
    assert e["tool.longcat"].state is HealthState.MISSING_TOOL
    assert e["tool.cosyvoice"].state is HealthState.MISSING_MODEL and "CosyVoice2-0.5B" in e["tool.cosyvoice"].missing
    assert e["tool.lora_train"].state is HealthState.OK
    assert e["tool.fastvideo"].state in (HealthState.MISSING_TOOL, HealthState.MISSING_MODEL)


def test_ffmpeg_needs_libass_filter(tmp_path: Path) -> None:
    ff = exe(tmp_path / "ffmpeg")
    s = cfg(tmp_path, ffmpeg=f'binary = "{ff}"\nffprobe = ""')
    good = run_checks(s, probes=NO_LLM, run=lambda argv: " .. subtitles  V->V\n .. whisper  A->A\n")
    assert by_name(good)["tool.ffmpeg"].state is HealthState.OK and "whisper=yes" in by_name(good)["tool.ffmpeg"].detail
    bad = run_checks(s, probes=NO_LLM, run=lambda argv: " .. scale V->V\n")
    assert by_name(bad)["tool.ffmpeg"].state is HealthState.DEGRADED
    assert by_name(bad)["tool.ffmpeg"].missing == ("libass subtitles filter",)


def test_resident_llms_and_disk_are_reported(tmp_path: Path) -> None:
    class P:
        name = "p"

        def resident(self) -> list[str]:
            return ["gemma4:31b"]

        def unload(self, models: list[str]) -> list[str]:
            return []

    s = cfg(tmp_path)
    e = by_name(run_checks(s, probes=[P()]))
    assert "gemma4:31b" in e["llm.resident"].detail
    assert e["system.disk"].state is HealthState.OK
    huge = cfg(tmp_path / "x", "[healthcheck]\nmin_free_gib = 99999999")
    assert by_name(run_checks(huge, probes=NO_LLM))["system.disk"].state is HealthState.DEGRADED


def test_adapter_health_is_included(tmp_path: Path) -> None:
    class Sick(FakeAdapter):
        provider = "sick"

        def health(self) -> HealthReport:
            return HealthReport(HealthState.MISSING_MODEL, "no weights", ("w.safetensors",))

    reg = AdapterRegistry()
    reg.register(Sick())
    e = by_name(run_checks(cfg(tmp_path), registry=reg, probes=NO_LLM))
    assert e["adapter.sick"].state is HealthState.MISSING_MODEL and e["adapter.sick"].missing == ("w.safetensors",)


def test_report_formats_and_serialises(tmp_path: Path) -> None:
    from storyagent.i18n import set_locale

    set_locale("en")
    s = cfg(tmp_path, services_toml({"comfy": "http://127.0.0.1:9"}, ["comfy"]))
    rep = run_checks(s, probes=NO_LLM)
    text = format_report(rep)
    assert "Pipeline health" in text and "Not running" in text and "Required entries not available: comfy" in text
    data = rep.to_dict()
    assert data["exit_code"] == 1 and any(x["name"] == "comfy" and x["required"] for x in data["entries"])
    json.dumps(data)  # fully serialisable


def test_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from storyagent.config import get_settings

    with FakeServer() as up:
        up.route("GET", "/health", {})
        home = tmp_path / "home"
        (home / "config").mkdir(parents=True)
        (home / "config" / "services.toml").write_text(services_toml({"comfy": up.url}, ["comfy"]))
        monkeypatch.setenv("STORY_AGENT_HOME", str(home))
        monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
        monkeypatch.setattr("storyagent.media.healthcheck.build_probes", lambda s: [])
        get_settings(reload=True)
        assert main(["--json"]) == 0
        assert json.loads(capsys.readouterr().out)["exit_code"] == 0
        assert main(["--require", "bogus"]) == 2
    get_settings(reload=True)


def test_http_failure_modes_do_not_crash(tmp_path: Path) -> None:
    def boom(url: str, timeout: float) -> None:
        raise requests.Timeout("slow")

    s = cfg(tmp_path)
    e = by_name(run_checks(s, http_get=boom, probes=NO_LLM))
    assert e["comfy"].state is HealthState.NOT_RUNNING
