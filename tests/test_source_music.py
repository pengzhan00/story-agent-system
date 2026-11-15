"""Original soundtrack policy at persistence and workflow boundaries; no neural models."""
from pathlib import Path

import pytest

from storyagent.config import ConfigError
from storyagent.media.ledger import NewAsset
from storyagent.workflow.stages import Ctx, music

from .story_kit import make_engine, new_project


def test_source_must_be_project_audio_and_music_stage_preserves_it(tmp_path: Path) -> None:
    engine, client, settings = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, None, {"visual_prompts": False})
    other = new_project(engine, "Another film")
    path = settings.home / "original.wav"
    path.write_bytes(b"test fixture")
    with engine.db.transaction() as conn:
        aid = engine.spec_store.ledger._insert_asset(  # noqa: SLF001
            conn, NewAsset(kind="audio.music", slot="main", path="original.wav", bytes=12,
                           duration_sec=241.478667), project_id=pid, shot_id=None, job_id=None,
            spec_id=None, spec_version=None, auto_adopt=False)
    with pytest.raises(ConfigError):
        engine.projects.set_settings(other, {"source_music": f"asset:{aid}"})
    project = engine.projects.set_settings(pid, {"source_music": f"asset:{aid}"})
    ctx = Ctx(settings, engine.db, engine.store, engine.state, engine.agents,
              engine.projects, project, {}, spec_store=engine.spec_store)
    before = list(client.calls)
    assert music(ctx) == {"created": 0, "preserved_source_music": f"asset:{aid}"}
    assert client.calls == before
    audio = [s.id for s in engine.spec_store.list_current(pid) if s.kind.value.startswith("audio.")]
    assert audio
    out = engine.run_stage(pid, "produce", {"only": audio, "force": True})
    assert out["made"] == 0
    assert {s["reason"] for s in out["skipped_detail"]} == {"source_music"}
    path.unlink()
    with pytest.raises(ConfigError):
        engine.projects.set_settings(pid, {"source_music": f"asset:{aid}"})
    assert "source_music" not in engine.projects.set_settings(pid, {"source_music": None}).settings
