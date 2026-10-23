"""Changed configuration reaches the running worker between jobs, never in the middle of one."""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from pathlib import Path

from storyagent.config import load_settings
from storyagent.config.reload import fingerprint
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.registry import AdapterRegistry

from .adapter_kit import make_rig, make_settings


def test_the_fingerprint_changes_with_the_files_that_matter_only(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    assert fingerprint(cfg) == ""
    cfg.mkdir()
    (cfg / "models.toml").write_text("a = 1\n")
    first = fingerprint(cfg)
    (cfg / "notes.txt").write_text("not watched")
    assert fingerprint(cfg) == first
    (cfg / "workflows").mkdir()
    (cfg / "workflows" / "w.json").write_text("{}")
    second = fingerprint(cfg)
    assert second != first
    (cfg / "models.toml").write_text("a = 22\n")
    assert fingerprint(cfg) != second


def wait_for(check, seconds: float = 5.0) -> bool:  # noqa: ANN001
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


def test_the_worker_swaps_in_changed_configuration_only_when_nothing_runs(tmp_path: Path) -> None:
    s = make_settings(tmp_path)
    rig = make_rig(s, FakeAdapter())
    worker = rig.worker

    def reread():  # noqa: ANN202
        fresh = load_settings({"STORY_AGENT_HOME": str(s.home), "STORY_AGENT_LOCALE": "en"})
        reg = AdapterRegistry()
        reg.register(FakeAdapter())
        return fresh, reg

    busy: Future[None] = Future()
    worker._futures["running"] = busy                               # a job is in flight
    stop = threading.Event()
    thread = threading.Thread(target=worker.run_forever, args=(stop, reread), daemon=True)
    thread.start()
    time.sleep(0.3)                                                 # the worker has taken its first fingerprint
    try:
        (s.config_dir / "models.toml").write_text('[models."added"]\nkind = "image.generate"\nprovider = "fake"\n')
        time.sleep(0.4)
        assert "added" not in worker.settings.models                   # waits while a job runs
        busy.set_result(None)
        assert wait_for(lambda: "added" in worker.settings.models)
        assert "added" in worker.ledger.settings.models and worker.resolver.settings is worker.settings
        (s.config_dir / "models.toml").write_text("this is [not toml\n")
        time.sleep(0.4)
        assert "added" in worker.settings.models                       # a broken file leaves the old one in place
    finally:
        stop.set()
        thread.join(timeout=5)
