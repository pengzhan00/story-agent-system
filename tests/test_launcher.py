"""Launch screen: start the configured background services, never touch anything that is up or unconfigured."""
from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient

from storyagent import launcher as launcher_mod
from storyagent.api.app import create_app
from storyagent.config import ConfigError, Settings, load_settings
from storyagent.launcher import Launcher, LaunchError
from storyagent.media.contracts import HealthState
from storyagent.media.healthcheck import Entry, Report


class _Resp:
    def __init__(self, status: int) -> None:
        self.status_code = status


def make_settings(tmp_path: Path, launch_toml: str) -> Settings:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "services.toml").write_text(
        '[services.demo]\nurl = "http://demo.invalid:1"\nhealth_path = "/h"\n' + launch_toml)
    loaded = load_settings({"STORY_AGENT_HOME": str(home)})
    # the packaged [launch.*] entries start real programs: a test must only ever see its own
    return replace(loaded, launch={k: v for k, v in loaded.launch.items() if k == "demo"})


def down(*_a: Any, **_k: Any) -> Any:
    raise requests.ConnectionError("down")


@pytest.fixture(autouse=True)
def _reap() -> Any:
    yield
    for child in launcher_mod._children.values():
        if child.poll() is None:
            child.terminate()
            child.wait(5)
    launcher_mod._children.clear()


SLEEP = f'argv = ["{Path(sys.executable).as_posix()}", "-c", "import time; time.sleep(30)"]\n'


def test_start_records_the_process_and_is_idempotent(tmp_path: Path) -> None:
    s = make_settings(tmp_path, "[launch.demo]\n" + SLEEP + "timeout_sec = 30\n")
    lc = Launcher(s, http_get=down)
    assert lc.status("demo").state == "stopped" and lc.status("demo").startable
    st = lc.start("demo")
    assert st.state == "starting" and st.pid
    assert (s.home / "run" / "launch_demo.json").is_file() and (s.logs_dir / "launch_demo.log").is_file()
    assert lc.start("demo").pid == st.pid                      # a second press does not start a second process


def test_a_service_that_answers_is_left_alone(tmp_path: Path) -> None:
    s = make_settings(tmp_path, "[launch.demo]\n" + SLEEP)
    lc = Launcher(s, http_get=lambda *a, **k: _Resp(200))
    assert lc.start("demo").state == "running"
    assert not (s.home / "run" / "launch_demo.json").exists()  # nothing was spawned
    assert lc.start_all() == [("demo", "already", "running")]


def test_a_process_that_dies_is_reported_failed(tmp_path: Path) -> None:
    argv = f'argv = ["{Path(sys.executable).as_posix()}", "-c", "raise SystemExit(3)"]\n'
    s = make_settings(tmp_path, "[launch.demo]\n" + argv)
    lc = Launcher(s, http_get=down)
    lc.start("demo")
    launcher_mod._children["demo"].wait(10)
    assert lc.status("demo").state == "failed"
    assert lc.start_all()[0][1] == "started"                   # a failed service may be tried again


def test_a_launcher_command_that_exits_counts_as_starting_until_its_timeout(tmp_path: Path) -> None:
    argv = f'argv = ["{Path(sys.executable).as_posix()}", "-c", "pass"]\nlauncher = true\ntimeout_sec = 60\n'
    s = make_settings(tmp_path, "[launch.demo]\n" + argv)
    lc = Launcher(s, http_get=down)
    lc.start("demo")
    launcher_mod._children["demo"].wait(10)
    assert lc.status("demo").state == "starting"
    s2 = make_settings(tmp_path / "b", "[launch.demo]\n" + argv.replace("60", "0"))
    lc2 = Launcher(s2, http_get=down)
    lc2.start("demo")
    launcher_mod._children["demo"].wait(10)
    assert lc2.status("demo").state == "failed"


def test_errors(tmp_path: Path) -> None:
    s = make_settings(tmp_path, '[launch.demo]\nargv = ["no-such-program-xyz"]\n')
    lc = Launcher(s, http_get=down)
    with pytest.raises(LaunchError) as missing:
        lc.start("demo")
    assert missing.value.code == "launch_missing"
    with pytest.raises(LaunchError) as unknown:
        lc.start("nope")
    assert unknown.value.code == "launch_unknown"
    plain = Launcher(make_settings(tmp_path / "c", ""), http_get=down)
    with pytest.raises(LaunchError) as manual:
        plain.start("demo")
    assert manual.value.code == "launch_not_startable"
    assert plain.status("demo").startable is False


def test_config_validation(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="names no service"):
        make_settings(tmp_path, '[launch.ghost]\nargv = ["x"]\n')
    with pytest.raises(ConfigError, match="argv"):
        make_settings(tmp_path / "b", "[launch.demo]\nargv = []\n")


def test_packaged_defaults_cover_the_needed_services(settings: Settings) -> None:
    assert {"ollama", "acestep", "phosphene"} <= set(settings.launch)
    assert set(settings.healthcheck_required) <= set(settings.services) | {"tool.ffmpeg"}


# ---- API ----------------------------------------------------------------------------------------
@pytest.fixture
def api(fsettings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(Launcher, "_answering", lambda self, name: (False, "down"))
    rep = Report([Entry("tool.h3", "tool", HealthState.MISSING_MODEL, "weights", required=False)])
    monkeypatch.setattr("storyagent.api.routes_launch.health.run_checks", lambda *a, **k: rep)
    monkeypatch.setattr("storyagent.api.routes_launch._model_issues", lambda *a, **k: [])
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as c:
        yield c


def test_launch_screen_state(api: TestClient) -> None:
    body = api.get("/api/launch", headers={"Accept-Language": "en"}).json()
    by = {s["name"]: s for s in body["services"]}
    ace = by["acestep"]
    assert ace["startable"] and ace["state"] == "stopped" and ace["label"] == "Not running"
    assert by["acestep"]["hint"]                                     # the localized note
    assert not by["llamacpp"]["startable"]
    assert [i["name"] for i in body["issues"]] == ["tool.h3"]
    assert body["ready"] is False and "acestep" in body["startable"]


def test_start_errors_use_the_envelope(api: TestClient) -> None:
    r = api.post("/api/launch/llamacpp/start", headers={"Accept-Language": "en"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "launch_not_startable"
    assert api.post("/api/launch/ghost/start").status_code == 404


def test_start_one_through_the_api(api: TestClient, fsettings: Settings, tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    spec = dict(fsettings.launch["acestep"], argv=[sys.executable, "-c", "import time; time.sleep(30)"], cwd="",
                env={})
    monkeypatch.setitem(fsettings.launch, "acestep", spec)
    body = api.post("/api/launch/acestep/start").json()
    state = {s["name"]: s for s in body["services"]}["acestep"]
    assert state["state"] == "starting" and state["pid"]
    assert "acestep" not in body["startable"] and body["starting"] is True
