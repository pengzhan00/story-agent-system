"""Which settings a project runs with: its pipeline plan's, or the plain global ones for a project made before plans."""
from __future__ import annotations

from typing import Any

from storyagent.config import Settings


def settings_for(settings: Settings, pipeline: str) -> Settings:
    """``pipeline`` empty = a project that predates plans: it keeps the global configuration unchanged."""
    return settings.for_pipeline(pipeline) if pipeline else settings


def project_settings_for(settings: Settings, db: Any, project_id: int | None) -> Settings:
    """The effective settings of a project (by id); no project (the workshop) = the global configuration."""
    if not project_id:
        return settings
    row = db.conn.execute("SELECT pipeline FROM projects WHERE id=?", (project_id,)).fetchone()
    return settings_for(settings, row["pipeline"] if row else "")
