from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.config import Settings, load_settings
from storyagent.db import Database, migrate
from storyagent.i18n import set_locale


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Isolated settings: tmp home, no env leakage from the developer machine."""
    return load_settings({"STORY_AGENT_HOME": str(tmp_path / "home")})


@pytest.fixture
def db(settings: Settings) -> Database:
    database = Database(settings.db_path)
    migrate(database)
    yield database
    database.close()


@pytest.fixture(autouse=True)
def _locale() -> None:
    set_locale("en")


# ---- media fixtures ---------------------------------------------------------------------------
FAKE_MODELS = """
[models."fake-video"]
kind = "video.generate"
provider = "fake"
version = "t1"
est_memory_gib = 4
license_tag = "test"
timeout_sec = 30
[models."fake-video".params]
width = 100
fps = 8
[models."fake-video".tiers.draft]
width = 50
[models."fake-video".tiers.final]
width = 200

[models."fake-heavy"]
kind = "video.generate"
provider = "fake"
heavy = true
est_memory_gib = 40
license_tag = "test"
timeout_sec = 30

[models."fake-restricted"]
kind = "video.generate"
provider = "fake"
commercial_ok = false
license_tag = "restricted"
timeout_sec = 30

[models."fake-tts"]
kind = "audio.tts"
provider = "fake"
timeout_sec = 30

[models."fake-slow"]
kind = "video.generate"
provider = "fake"
timeout_sec = 0.3
"""

FAKE_SERVICES = """
[runtime]
poll_interval_seconds = 0.01
heartbeat_seconds = 0.05
lease_seconds = 5
caffeinate = false
memory_budget_gib = 64
check_system_memory = false
max_concurrent_jobs = 0
"""


@pytest.fixture
def fsettings(tmp_path: Path) -> Settings:
    """Settings with fake models/provider and fast timings."""
    home = tmp_path / "home"
    cfg = home / "config"
    cfg.mkdir(parents=True)
    (cfg / "models.toml").write_text(FAKE_MODELS)
    (cfg / "services.toml").write_text(FAKE_SERVICES)
    return load_settings({"STORY_AGENT_HOME": str(home)})


@pytest.fixture
def fdb(fsettings: Settings) -> Database:
    database = Database(fsettings.db_path)
    migrate(database)
    database.conn.execute("INSERT INTO projects(slug, name) VALUES ('demo', 'Demo')")
    database.conn.execute(
        "INSERT INTO episodes(project_id, number) VALUES (1, 1)"
    )
    database.conn.execute(
        "INSERT INTO shots(project_id, episode_id) VALUES (1, 1)"
    )
    yield database
    database.close()
