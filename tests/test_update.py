"""Update and hot restart: the steps (made-up git and commands), the guards, the restart handshake between server,
worker and ``scripts/start.sh``, the API (admin only) and the supervisor script itself."""
from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent import restart
from storyagent.api import app as api_app
from storyagent.api.app import create_app
from storyagent.auth import AuthStore
from storyagent.config import ConfigError, Settings, UpdateConfig, load_settings
from storyagent.db import Database, migrate
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.contracts import MediaJob
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker
from storyagent.restart import (
    RESTART_EXIT_CODE,
    RestartRequest,
    WorkerGate,
    phase_of,
    read_answer,
    read_request,
    watch_worker,
    write_request,
    write_supervisor,
)
from storyagent.update import STEP_IDS, UpdateError, Updater, running_work

SEP = "\x1f"
ROOT = Path(__file__).resolve().parents[1]


# -- a made-up machine ----------------------------------------------------------------------------------
class FakeMachine:
    """Answers git and the build tools like a production checkout two commits behind its remote."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.cwds: list[Path] = []
        self.git_dir = ".git"
        self.common_dir = ".git"
        self.branch = "main"
        self.dirty: list[str] = []
        self.behind = ["bbb2222", "ccc3333"]
        self.ahead = 0
        self.remote_known = True
        self.fail: dict[str, int] = {}                  # "pnpm build" -> exit code
        self.head = ("a" * 40, "aaa1111", "old version")
        self.on_fetch: Callable[[], None] | None = None

    def commit(self, short: str, subject: str) -> str:
        return SEP.join([short * 6 + "0" * 4, short, subject])

    def run(self, argv: Sequence[str], cwd: Path, on_line: Callable[[str], None], timeout: float) -> int:
        self.calls.append(list(argv))
        self.cwds.append(cwd)
        text = " ".join(argv)
        for prefix, code in self.fail.items():
            if text.startswith(prefix):
                on_line(f"{prefix} exploded")
                return code
        out: list[str] = []
        code = 0
        if text == "git rev-parse --git-dir --git-common-dir":
            out = [self.git_dir, self.common_dir]
        elif text == "git rev-parse --abbrev-ref HEAD":
            out = [self.branch]
        elif text.startswith("git remote get-url"):
            out = ["git@example.invalid:x/y.git"]
        elif text == "git status --porcelain":
            out = list(self.dirty)
        elif text.startswith("git log -1"):
            out = [SEP.join([self.head[0], self.head[1], self.head[2]])]
        elif text.startswith("git rev-parse --verify"):
            code = 0 if self.remote_known else 1
        elif text.startswith("git log -n"):
            out = [self.commit(s[:3], f"commit {s}") for s in self.behind]
        elif text.startswith("git rev-list --count HEAD.."):
            out = [str(len(self.behind))]
        elif text.startswith("git rev-list --count origin/main.."):
            out = [str(self.ahead)]
        elif text.startswith("git fetch"):
            if self.on_fetch:
                self.on_fetch()
            out = ["fetched"]
        elif text.startswith("git merge --ff-only origin/main"):
            self.head = ("b" * 40, "ccc3333", "newest version")
            out = ["Fast-forward"]
        else:
            out = [f"ran {text}"]
        for line in out:
            on_line(line)
        return code

    def steps(self) -> list[str]:
        queries = ("rev-parse", "status", "log", "remote", "rev-list")
        return [" ".join(c) for c in self.calls if not (c[0] == "git" and c[1] in queries)]


def updater(settings: Settings, machine: FakeMachine, *, supervised: bool = False, root: Path | None = None,
            audit: Callable[[str, str, dict[str, Any]], None] | None = None, clock: Callable[[], float] = time.time,
            tools: bool = True) -> Updater:
    root = root or settings.home.parent / "checkout"
    (root / "web").mkdir(parents=True, exist_ok=True)
    (root / "web" / "package.json").write_text("{}")
    parent = 4242
    if supervised:
        write_supervisor(settings, parent, root)
    return Updater(settings, root=root, runner=machine, python="/venv/bin/python",
                   which=(lambda n: f"/bin/{n}") if tools else (lambda n: None), parent_pid=parent, audit=audit,
                   clock=clock)


@pytest.fixture
def machine() -> FakeMachine:
    return FakeMachine()


# -- the constant and the script agree ----------------------------------------------------------------------
def test_the_restart_exit_code_is_75_and_the_script_uses_the_same_number() -> None:
    assert RESTART_EXIT_CODE == 75
    script = (ROOT / "scripts" / "start.sh").read_text("utf-8")
    assert re.search(r"^RESTART_EXIT=75\b", script, re.M)
    docs = (ROOT / "docs" / "RUNBOOK.md").read_text("utf-8")
    assert "75" in docs and "start.sh" in docs


# -- configuration ----------------------------------------------------------------------------------------
def test_update_config_defaults_and_validation(tmp_path: Path) -> None:
    s = load_settings({"STORY_AGENT_HOME": str(tmp_path)})
    assert s.update == UpdateConfig(enabled="auto", remote="origin", branch="main", step_timeout_sec=1800.0,
                                    keep_logs=20)
    cfg = tmp_path / "config"
    cfg.mkdir()
    for text, mode in (("enabled = true", "on"), ("enabled = false", "off"), ('enabled = "AUTO"', "auto")):
        (cfg / "services.toml").write_text(f"[update]\n{text}\n")
        assert load_settings({"STORY_AGENT_HOME": str(tmp_path)}).update.enabled == mode
    for bad in ('enabled = "sometimes"', 'branch = "-x"', "step_timeout_sec = 0"):
        (cfg / "services.toml").write_text(f"[update]\n{bad}\n")
        with pytest.raises(ConfigError):
            load_settings({"STORY_AGENT_HOME": str(tmp_path)})


# -- the steps ----------------------------------------------------------------------------------------------
def test_a_full_update_runs_the_deploy_steps_as_argv_lists_in_order(settings: Settings, machine: FakeMachine) -> None:
    u = updater(settings, machine)
    run = u.start(by="root", restart=False)
    u.wait(10)
    assert run.status == "succeeded" and run.failed_step == ""
    assert [s.status for s in run.steps] == ["ok"] * len(STEP_IDS)
    assert machine.steps() == [
        "git fetch origin",
        "git merge --ff-only origin/main",
        "uv pip install --python /venv/bin/python -e .",
        "pnpm install --frozen-lockfile",
        "pnpm build",
        "/venv/bin/python -m storyagent db migrate",
        "/venv/bin/python -m storyagent i18n check",
    ]
    cwd = {" ".join(c): d for c, d in zip(machine.calls, machine.cwds, strict=True)}
    assert cwd["pnpm build"].name == "web" and cwd["git fetch origin"].name == "checkout"
    assert run.before is not None and run.before.short == "aaa1111"
    assert run.after is not None and run.after.short == "ccc3333"
    assert [c.subject for c in run.behind] == ["commit bbb2222", "commit ccc3333"]
    assert run.restart == "none"
    log = Path(run.log_file).read_text("utf-8")
    assert "== merge" in log and "$ pnpm build" in log and "ran pnpm build" in log


def test_the_first_failing_step_stops_the_run_and_nothing_restarts(settings: Settings, machine: FakeMachine) -> None:
    machine.fail["pnpm build"] = 1
    u = updater(settings, machine, supervised=True)
    run = u.start(by="root", restart=True)
    u.wait(10)
    assert run.status == "failed" and run.failed_step == "web_build"
    status = {s.id: s.status for s in run.steps}
    assert status["merge"] == "ok" and status["web_build"] == "failed"
    assert status["migrate"] == "skipped" and status["i18n"] == "skipped"
    assert not any("migrate" in c for c in machine.steps())
    assert "pnpm build exploded" in run.steps[5].detail
    assert run.restart == "none" and read_request(settings) is None          # the old code keeps running
    assert not u.busy()                                                     # and a new attempt may start


def test_an_up_to_date_checkout_merges_nothing(settings: Settings, machine: FakeMachine) -> None:
    machine.behind = []
    u = updater(settings, machine, supervised=True)
    run = u.start(restart=True)
    u.wait(10)
    assert run.status == "current" and run.restart == "none"
    assert [s.status for s in run.steps][:2] == ["ok", "ok"] and all(s.status == "skipped" for s in run.steps[2:])
    assert machine.steps() == ["git fetch origin"] and read_request(settings) is None


def test_commits_the_remote_lacks_block_the_fast_forward(settings: Settings, machine: FakeMachine) -> None:
    machine.ahead = 1
    u = updater(settings, machine)
    run = u.start(restart=False)
    u.wait(10)
    assert run.status == "failed" and run.failed_step == "compare"
    assert run.steps[1].note == "update.note.diverged"
    assert not any(c.startswith("git merge") for c in machine.steps())


def test_unpushed_local_commits_are_not_a_problem_when_nothing_is_behind(settings: Settings,
                                                                         machine: FakeMachine) -> None:
    machine.behind, machine.ahead = [], 2
    u = updater(settings, machine)
    run = u.start(restart=False)
    u.wait(10)
    assert run.status == "current" and machine.steps() == ["git fetch origin"]


def test_a_dirty_work_tree_is_refused_before_anything_runs(settings: Settings, machine: FakeMachine) -> None:
    machine.dirty = [" M storyagent/cli.py"]
    u = updater(settings, machine)
    with pytest.raises(UpdateError) as err:
        u.start()
    assert err.value.code == "update_dirty" and "cli.py" in err.value.detail
    assert machine.steps() == [] and not u.busy()


def test_missing_tools_are_named(settings: Settings, machine: FakeMachine) -> None:
    u = updater(settings, machine, tools=False)
    with pytest.raises(UpdateError) as err:
        u.start()
    assert err.value.code == "update_tool_missing" and "pnpm" in err.value.detail


def test_only_one_update_runs_at_a_time(settings: Settings, machine: FakeMachine) -> None:
    gate, entered = threading.Event(), threading.Event()
    machine.on_fetch = lambda: (entered.set(), gate.wait(10))
    u = updater(settings, machine)
    u.start()
    assert entered.wait(10)
    with pytest.raises(UpdateError) as err:
        u.start()
    assert err.value.code == "update_busy"
    with pytest.raises(UpdateError) as err2:
        u.check()
    assert err2.value.code == "update_busy"
    with pytest.raises(UpdateError) as err3:
        u.request_restart()
    assert err3.value.code == "update_busy"
    gate.set()
    u.wait(10)
    assert not u.busy()


def test_a_linked_work_tree_or_another_branch_is_left_alone_unless_forced(
        settings: Settings, machine: FakeMachine) -> None:
    machine.git_dir, machine.common_dir = "/main/.git/worktrees/x", "/main/.git"
    u = updater(settings, machine)
    assert u.availability().reason == "linked_worktree"
    with pytest.raises(UpdateError) as err:
        u.start()
    assert err.value.code == "update_disabled" and err.value.status == 403 and err.value.detail == "linked_worktree"
    machine.git_dir = machine.common_dir
    machine.branch = "feature"
    assert u.availability().reason == "branch"
    forced = dataclasses.replace(settings, update=UpdateConfig(enabled="on"))
    assert updater(forced, machine).availability().enabled
    off = dataclasses.replace(settings, update=UpdateConfig(enabled="off"))
    assert updater(off, machine).availability().reason == "off"


def test_an_interrupted_run_is_marked_when_the_next_server_starts(settings: Settings, machine: FakeMachine) -> None:
    gate, entered = threading.Event(), threading.Event()
    machine.on_fetch = lambda: (entered.set(), gate.wait(10))
    first = updater(settings, machine)
    first.start()
    assert entered.wait(10)
    second = updater(settings, FakeMachine())                      # another process, same data home
    assert second.last_run is not None and second.last_run.status == "interrupted"
    gate.set()
    first.wait(10)


def test_check_fetches_and_reports_what_waits(settings: Settings, machine: FakeMachine) -> None:
    u = updater(settings, machine)
    info = u.check()
    assert machine.steps() == ["git fetch origin"]
    assert info.behind_count == 2 and [c.short for c in info.behind] == ["bbb", "ccc"]
    assert info.checked_at is not None and info.clean


def test_a_failed_fetch_in_check_is_a_502(settings: Settings, machine: FakeMachine) -> None:
    machine.fail["git fetch"] = 128
    with pytest.raises(UpdateError) as err:
        updater(settings, machine).check()
    assert err.value.code == "update_fetch_failed" and err.value.status == 502 and "exploded" in err.value.detail


def test_old_update_logs_are_pruned(settings: Settings, machine: FakeMachine) -> None:
    logs = settings.logs_dir
    logs.mkdir(parents=True)
    for i in range(30):
        (logs / f"update-2020010{i % 10}-{i:06d}-x.log").write_text("x")
    cfg = dataclasses.replace(settings, update=UpdateConfig(keep_logs=5))
    u = updater(cfg, machine)
    u.start(restart=False)
    u.wait(10)
    assert len(list(logs.glob("update-*.log"))) == 5


# -- real git (no network): the comparison and the production-checkout test -------------------------------
def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing")
def test_with_real_git_behind_ahead_dirty_and_the_linked_work_tree(settings: Settings, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    prod, dev = tmp_path / "prod", tmp_path / "dev"
    git(tmp_path, "clone", str(remote), str(prod))
    git(prod, "checkout", "-b", "main")
    (prod / "a.txt").write_text("1")
    git(prod, "add", "a.txt")
    git(prod, "commit", "-m", "first")
    git(prod, "push", "-u", "origin", "main")
    git(tmp_path, "clone", str(remote), str(dev))
    (dev / "b.txt").write_text("2")
    git(dev, "add", "b.txt")
    git(dev, "commit", "-m", "second: add b")
    git(dev, "push")

    u = Updater(settings, root=prod, which=lambda n: f"/bin/{n}")
    assert u.availability().enabled
    before = u.inspect(force=True)
    assert before.behind_count == 0 and before.clean and before.current is not None
    after = u.check()
    assert after.behind_count == 1 and after.behind[0].subject == "second: add b" and after.ahead_count == 0
    (prod / "a.txt").write_text("changed")
    assert u.inspect(force=True).clean is False
    git(prod, "checkout", "--", "a.txt")
    (prod / "local.txt").write_text("x")
    git(prod, "add", "local.txt")
    git(prod, "commit", "-m", "local only")
    diverged = u.inspect(force=True)
    assert diverged.ahead_count == 1 and diverged.behind_count == 1    # diverged: a fast-forward would fail

    wt = tmp_path / "wt"
    git(prod, "worktree", "add", "-b", "dev-branch", str(wt))
    assert Updater(settings, root=wt, which=lambda n: "x").availability().reason == "linked_worktree"
    assert Updater(settings, root=tmp_path, which=lambda n: "x").availability().reason == "not_git"


# -- the restart handshake ------------------------------------------------------------------------------
class FakeWorker:
    def __init__(self, busy: int = 0) -> None:
        self.n, self.paused = busy, False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def busy(self) -> int:
        return self.n


def test_the_worker_waits_for_running_work_by_default_and_then_leaves(settings: Settings) -> None:
    worker, now = FakeWorker(busy=2), [1000.0]
    gate = WorkerGate(settings, worker, clock=lambda: now[0])
    assert gate.poll() is False and not worker.paused                          # no request: business as usual
    write_request(settings, RestartRequest("r1", "wait", 1001.0))
    now[0] = 1002.0
    assert gate.poll() is False and worker.paused                              # stops claiming, keeps running
    answer = read_answer(settings)
    assert answer is not None and answer.state == "draining" and answer.running == 2
    assert phase_of(read_request(settings), answer) == "draining"
    worker.n = 0
    assert gate.poll() is True and gate.leaving
    stopped = read_answer(settings)
    assert stopped is not None and stopped.state == "stopped"
    assert phase_of(read_request(settings), stopped) == "restarting"


def test_interrupt_now_does_not_wait_and_a_cancelled_request_resumes(settings: Settings) -> None:
    worker = FakeWorker(busy=3)
    gate = WorkerGate(settings, worker, clock=lambda: 1000.0)
    write_request(settings, RestartRequest("r1", "wait", 1001.0))
    assert gate.poll() is False and worker.paused
    restart.clear_request(settings)
    assert gate.poll() is False and not worker.paused                         # cancelled: claims work again
    write_request(settings, RestartRequest("r2", "now", 1002.0))
    assert gate.poll() is True                                                # does not wait for the 3 running


def test_a_request_older_than_the_worker_is_a_leftover_and_ignored(settings: Settings) -> None:
    write_request(settings, RestartRequest("old", "wait", 500.0))
    worker = FakeWorker()
    assert WorkerGate(settings, worker, clock=lambda: 1000.0).poll() is False and not worker.paused


def test_watch_worker_stops_the_loop_and_arms_the_hard_exit(settings: Settings) -> None:
    worker, stop, exited = FakeWorker(), threading.Event(), threading.Event()
    gate = WorkerGate(settings, worker, clock=lambda: 0.0)
    watch_worker(gate, stop, interval=0.01, hard_exit=exited.set, hard_exit_sec=0.05)
    write_request(settings, RestartRequest("r1", "now", 1.0))
    assert stop.wait(5) and exited.wait(5) and gate.leaving


def test_the_supervisor_marker_counts_only_for_its_own_children(settings: Settings, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert not restart.supervised(settings, 77, root)
    write_supervisor(settings, 77, root)
    assert restart.supervised(settings, 77, root)
    assert not restart.supervised(settings, 78, root)                         # started by someone else
    assert not restart.supervised(settings, 77, tmp_path / "other")           # another checkout's script
    restart.clear_supervisor(settings, 99)                                    # not ours: stays
    assert restart.supervised(settings, 77, root)
    restart.clear_supervisor(settings, 77)
    assert not restart.supervised(settings, 77, root)


def test_a_successful_update_asks_for_a_restart_that_waits(settings: Settings, machine: FakeMachine) -> None:
    u = updater(settings, machine, supervised=True)
    run = u.start(by="root", restart=True)
    u.wait(10)
    request = read_request(settings)
    assert run.status == "succeeded" and run.restart == "requested"
    assert request is not None and request.mode == "wait" and request.by == "root"
    assert u.restart_info().phase == "requested" and u.restart_info().supervised
    interrupt = updater(settings, FakeMachine(), supervised=True)
    interrupt.start(restart=True, mode="now")
    interrupt.wait(10)
    again = read_request(settings)
    assert again is not None and again.mode == "now" and again.id != request.id


def test_without_a_supervisor_it_only_updates_and_says_to_restart_by_hand(
        settings: Settings, machine: FakeMachine) -> None:
    u = updater(settings, machine, supervised=False)
    run = u.start(restart=True)
    u.wait(10)
    assert run.status == "succeeded" and run.restart == "unsupervised" and read_request(settings) is None
    with pytest.raises(UpdateError) as err:
        u.request_restart()
    assert err.value.code == "update_unsupervised"


def test_a_restart_can_be_cancelled_until_the_worker_has_stopped(settings: Settings, machine: FakeMachine) -> None:
    u = updater(settings, machine, supervised=True)
    with pytest.raises(UpdateError) as none:
        u.cancel_restart()
    assert none.value.code == "update_no_restart"
    request = u.request_restart("wait")
    u.cancel_restart()
    assert read_request(settings) is None
    request = u.request_restart("wait")
    restart.write_answer(settings, restart.WorkerAnswer(request.id, "stopped", 0, 1.0))
    with pytest.raises(UpdateError) as late:
        u.cancel_restart()
    assert late.value.code == "update_restarting"


def test_the_server_leaves_with_75_after_the_worker_stopped(settings: Settings, monkeypatch: pytest.MonkeyPatch,
                                                            tmp_path: Path) -> None:
    monkeypatch.setattr(api_app, "SERVER_GRACE_SEC", 0.05)
    left: list[int] = []
    app = create_app(settings)
    app.state.leave = left.append
    with TestClient(app) as client:
        client.get("/api/ready")
        request = RestartRequest("r1", "wait", time.time())
        write_request(settings, request)
        time.sleep(1.3)
        assert left == []                                                     # the worker has not stopped yet
        restart.write_answer(settings, restart.WorkerAnswer("r1", "stopped", 0, time.time()))
        deadline = time.time() + 6
        while not left and time.time() < deadline:
            time.sleep(0.1)
    assert left == [RESTART_EXIT_CODE]


def test_a_new_server_forgets_the_request_that_brought_it_up(settings: Settings) -> None:
    write_request(settings, RestartRequest("r1", "wait", time.time()))
    with TestClient(create_app(settings)) as client:
        client.get("/api/ready")
    assert read_request(settings) is None


# -- the worker really stops claiming ---------------------------------------------------------------------
def test_a_paused_worker_claims_nothing_until_resumed(fsettings: Settings) -> None:
    from storyagent.domain import MediaKind
    from storyagent.media.ledger import Ledger

    db = Database(fsettings.db_path)
    migrate(db)
    db.conn.execute("INSERT INTO projects(slug, name) VALUES ('demo', 'Demo')")
    ledger = Ledger(db, fsettings)
    ledger.enqueue(MediaJob(kind=MediaKind.VIDEO_GENERATE, inputs={"prompt": "x"}, project_id=1,
                            model_ref="fake-video"))
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    worker = Worker(fsettings, db, reg, ledger=ledger, probes=[])
    worker.pause()
    assert worker.tick() == 0 and len(ledger.queued()) == 1 and worker.busy() == 0
    worker.resume()
    assert worker.drain(15) and not ledger.queued()
    worker.shutdown()


# -- the API ------------------------------------------------------------------------------------------------
@pytest.fixture
def api(settings: Settings, machine: FakeMachine) -> Iterator[tuple[TestClient, FakeMachine, Settings]]:
    with TestClient(create_app(settings), raise_server_exceptions=False, headers={"Accept-Language": "en"}) as c:
        auth = c.app.state.ctx.auth                                            # type: ignore[attr-defined]
        c.app.state.updater = updater(                                         # type: ignore[attr-defined]
            settings, machine, supervised=True,
            audit=lambda action, by, detail: auth.audit(action, actor=by or "local", detail=detail))
        yield c, machine, settings


def wait_idle(client: TestClient) -> dict[str, Any]:
    for _ in range(200):
        body: dict[str, Any] = client.get("/api/update").json()
        if not body["busy"]:
            return body
        time.sleep(0.05)
    raise AssertionError("update did not finish")


def test_the_state_shows_version_pending_commits_and_what_a_restart_would_interrupt(
        api: tuple[TestClient, FakeMachine, Settings]) -> None:
    client, _machine, settings = api
    db = Database(settings.db_path)
    db.conn.execute("INSERT INTO projects(slug, name) VALUES ('demo', 'Demo')")
    db.conn.execute(
        "INSERT INTO media_jobs(id, project_id, kind, provider, model_ref, tier, slot, status, heavy, inputs, params,"
        " idempotency_key, started_at) VALUES ('j1', 1, 'video.generate', 'fake', 'fake-video', 'draft', 'main',"
        " 'running', 1, '{}', '{}', 'k1', '2026-01-01T00:00:00Z')")
    body = client.get("/api/update").json()
    assert body["enabled"] and body["current"]["short"] == "aaa1111" and body["branch"] == "main"
    assert body["behind_count"] == 2 and len(body["behind"]) == 2 and body["clean"] and body["can_update"]
    assert body["restart"]["supervised"] and body["restart"]["phase"] == "none"
    assert body["work"]["heavy"] == 1 and body["work"]["jobs"][0]["id"] == "j1"
    assert running_work(db).count == 1


def test_run_updates_audits_and_requests_a_waiting_restart_while_work_runs(
        api: tuple[TestClient, FakeMachine, Settings]) -> None:
    client, _machine, settings = api
    r = client.post("/api/update/run", json={})
    assert r.status_code == 202
    body = wait_idle(client)
    assert body["run"]["status"] == "succeeded" and body["run"]["restart"] == "requested"
    assert [s["status"] for s in body["run"]["steps"]] == ["ok"] * len(STEP_IDS)
    assert body["restart"]["phase"] == "requested" and body["restart"]["mode"] == "wait"       # waits by default
    assert "pnpm build" in "\n".join(body["log_tail"])
    actions = [r["action"] for r in Database(settings.db_path).conn.execute("SELECT action FROM audit_log")]
    assert "update.start" in actions and "update.finish" in actions and "http.POST" in actions
    # the worker side: running work keeps the worker from leaving
    worker = FakeWorker(busy=1)
    gate = WorkerGate(settings, worker, clock=lambda: 0.0)
    assert gate.poll() is False and worker.paused
    assert client.get("/api/update").json()["restart"]["phase"] == "draining"


def test_interrupt_now_and_cancel_and_restart_only(api: tuple[TestClient, FakeMachine, Settings]) -> None:
    client, _machine, settings = api
    assert client.post("/api/update/restart", json={"interrupt": True}).json()["restart"]["mode"] == "now"
    assert client.delete("/api/update/restart").json()["restart"]["phase"] == "none"
    again = client.delete("/api/update/restart")
    assert again.status_code == 409 and again.json()["error"]["code"] == "update_no_restart"
    assert read_request(settings) is None


def test_refusals_are_localised_api_errors(api: tuple[TestClient, FakeMachine, Settings]) -> None:
    client, machine, _settings = api
    machine.dirty = [" M x"]
    r = client.post("/api/update/run", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "update_dirty"
    assert "uncommitted" in r.json()["error"]["message"] and r.json()["error"]["remediation"]
    machine.dirty = []
    machine.git_dir = "/m/.git/worktrees/x"
    machine.common_dir = "/m/.git"
    client.app.state.updater._cache = None                                     # type: ignore[attr-defined]
    r = client.post("/api/update/run", json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "update_disabled"
    assert client.get("/api/update").json()["reason"] == "linked_worktree"
    assert client.post("/api/update/check").status_code == 403


def test_a_second_update_request_while_one_runs_is_a_409(api: tuple[TestClient, FakeMachine, Settings]) -> None:
    client, machine, _settings = api
    gate, entered = threading.Event(), threading.Event()
    machine.on_fetch = lambda: (entered.set(), gate.wait(10))
    assert client.post("/api/update/run", json={}).status_code == 202
    assert entered.wait(10)
    r = client.post("/api/update/run", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "update_busy"
    assert client.get("/api/update").json()["busy"] is True
    gate.set()
    wait_idle(client)


def test_only_admins_may_use_it_when_accounts_are_on(fsettings: Settings, machine: FakeMachine) -> None:
    s = load_settings({"STORY_AGENT_HOME": str(fsettings.home), "STORY_AGENT_AUTH": "token"})
    store = AuthStore(Database(s.db_path), s)
    with TestClient(create_app(s), raise_server_exceptions=False, headers={"Accept-Language": "en"}) as client:
        migrate(Database(s.db_path))
        client.app.state.updater = updater(s, machine, supervised=True)       # type: ignore[attr-defined]
        tokens = {}
        for role in ("admin", "producer", "editor", "viewer"):
            store.create_user(f"u_{role}", "correct horse", role)
            tokens[role] = client.post("/api/auth/login", json={"username": f"u_{role}", "password": "correct horse"}
                                       ).json()["token"]
        client.cookies.clear()                                                # the logins set a cookie
        assert client.get("/api/update").status_code == 401
        for role in ("producer", "editor", "viewer"):
            h = {"Authorization": f"Bearer {tokens[role]}"}
            assert client.get("/api/update", headers=h).status_code == 403
            assert client.post("/api/update/run", json={}, headers=h).status_code == 403
            assert client.post("/api/update/check", headers=h).status_code == 403
            assert client.post("/api/update/restart", json={}, headers=h).status_code == 403
            assert client.delete("/api/update/restart", headers=h).status_code == 403
        assert machine.steps() == [] and read_request(s) is None
        admin = {"Authorization": f"Bearer {tokens['admin']}"}
        assert client.get("/api/update", headers=admin).status_code == 200
        assert client.post("/api/update/run", json={}, headers=admin).status_code == 202
        updater_ = client.app.state.updater                                    # type: ignore[attr-defined]
        updater_.wait(10)
        assert updater_.last_run.by == "u_admin"


def test_even_if_auth_toml_forgets_the_prefix_the_route_still_wants_an_admin(fsettings: Settings,
                                                                             machine: FakeMachine) -> None:
    cfg = fsettings.home / "config"
    (cfg / "auth.toml").write_text('[auth]\nmode = "token"\nadmin_prefixes = ["/api/users"]\n')
    s = load_settings({"STORY_AGENT_HOME": str(fsettings.home)})
    assert "/api/update" not in s.auth.admin_prefixes
    store = AuthStore(Database(s.db_path), s)
    with TestClient(create_app(s), raise_server_exceptions=False) as client:
        client.app.state.updater = updater(s, machine)                         # type: ignore[attr-defined]
        store.create_user("vee", "correct horse", "viewer")
        token = client.post("/api/auth/login", json={"username": "vee", "password": "correct horse"}).json()["token"]
        assert client.get("/api/update", headers={"Authorization": f"Bearer {token}"}).status_code == 403


# -- the supervisor script, for real ------------------------------------------------------------------------
FAKE_PYTHON = """#!/bin/sh
# stands in for .venv/bin/python: `python -m storyagent <command> ...`
R="$FAKE_ROOT"
case "$3" in
  db) echo "migrate" >> "$R/calls.log" ;;
  update) echo "marker $5 $6 $7" >> "$R/calls.log" ;;
  worker)
    n=$(cat "$R/count" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$R/count"
    echo "worker $n" >> "$R/calls.log"
    if [ "$n" = 1 ]; then sleep 1; exit 75; fi
    trap 'exit 0' TERM
    while true; do sleep 0.1; done ;;
  serve)
    echo "serve $*" >> "$R/calls.log"
    trap 'exit 0' TERM
    while true; do sleep 0.1; done ;;
esac
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash missing")
def test_start_sh_restarts_both_on_75_and_stops_on_ctrl_c(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "start.sh", repo / "scripts" / "start.sh")
    py = repo / ".venv" / "bin" / "python"
    py.write_text(FAKE_PYTHON)
    py.chmod(0o755)
    calls = repo / "calls.log"
    env = {**os.environ, "FAKE_ROOT": str(repo), "STORY_AGENT_RESTART_GRACE": "5"}
    proc = subprocess.Popen(["bash", str(repo / "scripts" / "start.sh"), "--port", "9"], cwd=tmp_path, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 40
        while time.time() < deadline and proc.poll() is None:
            if calls.exists() and calls.read_text().count("worker 2") >= 1:
                break
            time.sleep(0.2)
        assert proc.poll() is None, proc.communicate()
        time.sleep(0.5)
    finally:
        proc.terminate()                                   # the test's own child: what Ctrl-C does through the trap
        try:
            _, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
    lines = calls.read_text().splitlines()
    assert lines.count("migrate") == 2                                          # before each start
    assert [x for x in lines if x.startswith("worker")] == ["worker 1", "worker 2"]
    assert len([x for x in lines if x.startswith("serve")]) == 2 and "--port 9" in lines[-1] + " ".join(lines)
    markers = [x for x in lines if x.startswith("marker")]
    assert markers[0].startswith("marker --pid") and markers[-1].endswith("--clear")      # cleared on the way out
    assert "restart requested" in err and "restarting worker and server" in err
    assert proc.returncode == 143


# -- scripts/deploy.sh is only a wrapper around the same code ------------------------------------------------
@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="bash or git missing")
def test_deploy_sh_checks_for_a_clean_tree_and_runs_the_update_command(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "deploy.sh", repo / "scripts" / "deploy.sh")
    py = repo / ".venv" / "bin" / "python"
    py.write_text('#!/bin/sh\necho "$*" >> "$FAKE_ROOT/calls.log"\n')
    py.chmod(0o755)
    git(repo, "init", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-m", "x")
    env = {**os.environ, "FAKE_ROOT": str(repo)}
    done = subprocess.run(["bash", str(repo / "scripts" / "deploy.sh")], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (repo / "calls.log").read_text().splitlines() == ["-m storyagent update run"]
    assert "restart" in done.stdout
    (repo / "scratch.txt").write_text("dirty")
    (repo / "calls.log").unlink()
    refused = subprocess.run(["bash", str(repo / "scripts" / "deploy.sh")], env=env, capture_output=True, text=True)
    assert refused.returncode == 1 and "clean checkout" in refused.stderr and not (repo / "calls.log").exists()
