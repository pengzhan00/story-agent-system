"""One direction for the whole project: the picture frame is chosen at the start and every size follows it."""
from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.config import ConfigError, Settings, load_settings
from storyagent.db import Database, migrate
from storyagent.domain import MediaKind, QualityTier
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import Ledger
from storyagent.projects import ProjectStateError, ProjectStore

LANDSCAPE = "movie_landscape_720"


def test_a_project_picks_its_frame_at_creation_and_the_aspect_follows(fsettings: Settings, fdb: Database) -> None:
    projects = ProjectStore(fdb, fsettings)
    vertical = projects.create("V")
    wide = projects.create("W", delivery_profile=LANDSCAPE)
    assert vertical.aspect_ratio == "9:16" and wide.aspect_ratio == "16:9" and wide.delivery_profile == LANDSCAPE
    with pytest.raises(ConfigError):
        projects.create("X", delivery_profile="no_such_profile")


def test_picture_sizes_follow_the_frame_of_the_project(tmp_path: Path) -> None:
    settings = load_settings({"STORY_AGENT_HOME": str(tmp_path)})          # the packaged models, sized 9:16
    db = Database(settings.db_path)
    migrate(db)
    projects, ledger = ProjectStore(db, settings), Ledger(db, settings)
    wide = projects.create("W", delivery_profile=LANDSCAPE)
    tall = projects.create("T")

    def sizes(project_id: int, model: str, kind: MediaKind) -> tuple[int, int]:
        rec, _ = ledger.enqueue(MediaJob(kind, {"prompt": "x"}, project_id=project_id, model_ref=model,
                                         tier=QualityTier.DRAFT))
        return rec.params["width"], rec.params["height"]

    for model, kind in (("ltx-2.5-video", MediaKind.VIDEO_GENERATE), ("flux2-klein-base-4b", MediaKind.IMAGE_GENERATE)):
        w, h = sizes(wide.id, model, kind)
        assert w > h and 0.95 < (w / h) / (16 / 9) < 1.05, (model, w, h)
        w, h = sizes(tall.id, model, kind)
        assert h > w and 0.95 < (w / h) / (9 / 16) < 1.05, (model, w, h)
    assert sizes(wide.id, "ltx-2.5-video", MediaKind.VIDEO_GENERATE) == (1024, 576)       # the 1024x576 draft recipe
    assert sizes(tall.id, "ltx-2.5-video", MediaKind.VIDEO_GENERATE) == (576, 1024)


def test_the_direction_is_locked_once_pictures_exist_but_the_same_direction_may_change(
        fsettings: Settings, fdb: Database) -> None:
    projects = ProjectStore(fdb, fsettings)
    p = projects.create("P")
    projects.update(p.id, delivery_profile=LANDSCAPE)                      # nothing made yet: free to choose
    assert projects.get(p.id).aspect_ratio == "16:9"
    fdb.conn.execute("INSERT INTO media_assets(id, project_id, kind, slot, path, sha256, bytes, version, is_current)"
                     " VALUES ('a1', ?, 'image.generate', 'main', 'x.png', 'h', 5, 1, 1)", (p.id,))
    with pytest.raises(ProjectStateError):
        projects.update(p.id, delivery_profile="shortdrama_vertical_720")  # a picture exists: direction is fixed
    assert projects.get(p.id).delivery_profile == LANDSCAPE
