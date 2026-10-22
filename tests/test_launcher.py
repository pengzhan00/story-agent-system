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


# ---- stop ---------------------------------------------------------------------------------------
def _listener(port: int) -> Any:
    import subprocess
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import time
    for _ in range(50):                                    # until it listens
        if _port_open(port):
            return proc
        time.sleep(0.1)
    proc.kill()
    raise AssertionError("test server did not start")


def _port_open(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def stop_settings(tmp_path: Path, port: int, stop: str) -> Settings:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "services.toml").write_text(
        f'[services.demo]\nurl = "http://127.0.0.1:{port}"\nhealth_path = "/"\n'
        f'[launch.demo]\n{SLEEP}stop = "{stop}"\n')
    loaded = load_settings({"STORY_AGENT_HOME": str(home)})
    return replace(loaded, launch={k: v for k, v in loaded.launch.items() if k == "demo"})


def test_stop_ends_only_the_listener_of_a_service_that_allows_it(tmp_path: Path) -> None:
    port = _free_port()
    proc = _listener(port)
    try:
        s = stop_settings(tmp_path, port, "port")
        lc = Launcher(s)
        assert lc.status("demo").stoppable and [p for p, _ in lc.listeners("demo")] == [proc.pid]
        st = lc.stop("demo")
        assert proc.wait(5) is not None and not _port_open(port) and st.state in ("stopped", "disabled")
        with pytest.raises(LaunchError) as gone:
            lc.stop("demo")
        assert gone.value.code == "launch_not_running"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_a_service_without_stop_is_never_touched(tmp_path: Path) -> None:
    port = _free_port()
    proc = _listener(port)
    try:
        lc = Launcher(stop_settings(tmp_path, port, ""))
        assert not lc.status("demo").stoppable
        with pytest.raises(LaunchError) as err:
            lc.stop("demo")
        assert err.value.code == "launch_not_stoppable" and proc.poll() is None and _port_open(port)
    finally:
        proc.kill()


def test_stop_over_the_api_is_audited_and_refused_while_jobs_wait(
        fsettings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Launcher, "_answering", lambda self, name: (False, "down"))
    calls: list[bool] = []

    def fake_stop(self: Launcher, name: str, *, force: bool = False, wait_sec: float = 15.0) -> Any:
        calls.append(force)
        return self.status(name)

    monkeypatch.setattr(Launcher, "stop", fake_stop)
    monkeypatch.setattr(Launcher, "listeners", lambda self, name: [(4242, "demo")])
    monkeypatch.setitem(fsettings.launch, "acestep", dict(fsettings.launch["acestep"], stop="port"))
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as c:
        db = c.app.state.ctx.db                                              # type: ignore[attr-defined]
        db.conn.execute(
            "INSERT INTO media_jobs(id, kind, provider, model_ref, status, idempotency_key) "
            "VALUES ('j1','audio.music','acestep','m','queued','k1')")
        busy = c.post("/api/launch/acestep/stop", json={})
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "service_busy" and calls == []
        assert c.post("/api/launch/acestep/stop", json={"force": True}).status_code == 200 and calls == [True]
        db.conn.execute("DELETE FROM media_jobs")
        assert c.post("/api/launch/acestep/stop", json={}).status_code == 200 and calls == [True, False]
        actions = [r["action"] for r in db.conn.execute("SELECT action FROM audit_log")]
        assert actions.count("service.stop") == 2
        body = c.get("/api/launch").json()
        assert {s["name"]: s for s in body["services"]}["acestep"]["stoppable"] is True
        assert set(body["memory"]) == {"total_gib", "free_gib", "swap_total_gib", "swap_used_gib", "swap_percent"}


def test_swap_usage_is_read_from_sysctl_text() -> None:
    from storyagent.media.broker import parse_swapusage
    got = parse_swapusage("total = 34816.00M  used = 34035.06M  free = 780.94M  (encrypted)")
    assert got is not None and round(got[0]) == 34 and round(got[1]) == 33
    assert parse_swapusage("nonsense") is None
