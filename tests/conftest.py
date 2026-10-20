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
