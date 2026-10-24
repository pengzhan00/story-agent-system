"""``/api/setup``: the guide's state, the configuration preview/write, the check."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyagent.api import routes_setup
from storyagent.api.app import create_app
from storyagent.config import load_settings
from storyagent.media.contracts import HealthState
from storyagent.media.healthcheck import Entry, Report

from .setup_kit import FakeMachine


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMachine:
    return FakeMachine(tmp_path, monkeypatch)


@pytest.fixture
def client(machine: FakeMachine) -> Iterator[TestClient]:
    settings = machine.settings()
    app = create_app(settings, loader=lambda: load_settings({"STORY_AGENT_HOME": str(machine.data)}))
    app.state.setup_probe = machine.probe()
    with TestClient(app, raise_server_exceptions=False, headers={"Accept-Language": "en"}) as c:
        yield c


def test_state_lists_profiles_components_steps_and_downloads(client: TestClient) -> None:
    r = client.get("/api/setup", params={"profile": "stills"})
    assert r.status_code == 200
    body = r.json()
    assert body["profile"] == "stills" and body["ready"] is False and body["next_step"] == "homebrew.1"
    assert [p["name"] for p in body["profiles"]] == ["story", "stills", "full", "everything"]
    comp = {c["name"]: c for c in body["components"]}
    assert comp["homebrew"]["state"] == "missing" and comp["homebrew"]["need"] == "required"
    assert comp["uv"]["blocked_by"] == ["homebrew"]
    assert comp["flux_klein_base_4b"]["steps"][-1]["kind"] == "download"
    assert all(d["source"] and d["file"] for d in body["downloads"])
    assert body["download_gib"] > 50 and body["missing_gib"] >= body["download_gib"]
    assert {m["key"] for m in body["machine"]} == {"arch", "system", "macos", "memory", "disk"}


def test_unknown_profile_is_a_404_with_a_localised_reason(client: TestClient) -> None:
    r = client.get("/api/setup", params={"profile": "nope"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "setup_unknown_profile"
    assert "no such setup profile" in r.json()["error"]["message"]


def test_config_preview_writes_nothing_and_write_does_and_audits(client: TestClient, machine: FakeMachine) -> None:
    machine.install_base()
    cfg = machine.data / "config"
    r = client.get("/api/setup/config", params={"profile": "stills", "llm_model": "qwen3:8b"})
    files = {f["name"]: f for f in r.json()["files"]}
    assert files["llm.toml"]["changed"] and files["services.toml"]["exists"]
    assert not (cfg / "llm.toml").exists()

    w = client.post("/api/setup/config", json={"profile": "stills", "llm_model": "qwen3:8b", "write": True})
    assert w.status_code == 200
    written = {Path(x["path"]).name: x["backup"] for x in w.json()["written"]}
    assert "llm.toml" in written and ".bak-setup-" in written["services.toml"]
    assert (cfg / "llm.toml").is_file()
    again = client.post("/api/setup/config", json={"profile": "stills", "llm_model": "qwen3:8b", "write": True}).json()
    assert again["written"] == []                                       # nothing left to change


def test_a_configuration_the_system_would_refuse_is_not_written(
        client: TestClient, machine: FakeMachine, monkeypatch: pytest.MonkeyPatch) -> None:
    from storyagent.setup import configgen

    real = configgen.compute

    def bad(*a: object, **k: object) -> dict[str, dict[str, object]]:
        out = real(*a, **k)                                    # type: ignore[arg-type]
        out.setdefault("services", {}).setdefault("healthcheck", {})["required"] = ["bogus"]
        return out

    monkeypatch.setattr(configgen, "compute", bad)
    r = client.post("/api/setup/config", json={"profile": "story", "write": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "setup_config_invalid"
    assert not list((machine.data / "config").glob("*.bak-setup-*"))
    assert "bogus" not in (machine.data / "config" / "services.toml").read_text()


def test_verify_runs_the_healthcheck_with_the_profiles_requirements(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[object] = []

    def fake(settings: object, **kw: object) -> Report:
        asked.append(kw["require"])
        return Report([Entry("ollama", "service", HealthState.NOT_RUNNING, "x", required=True),
                       Entry("tool.ffmpeg", "tool", HealthState.OK, "", required=True)])

    monkeypatch.setattr(routes_setup.health, "run_checks", fake)
    r = client.post("/api/setup/verify", params={"profile": "story"})
    assert r.status_code == 200 and r.json()["ok"] is False and asked == [["tool.ffmpeg", "ollama"]]
    assert {e["name"]: e["ok"] for e in r.json()["entries"]} == {"ollama": False, "tool.ffmpeg": True}
