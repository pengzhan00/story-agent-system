"""An edit model can ask for the scene before the person (`ref_order`) and wrap the shot in its own wording
(`edit_template`): Krea 2 Identity Edit takes image 1 = the scene, image 2 = the person, and an instruction."""
from __future__ import annotations

from pathlib import Path

from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.ledger import Ledger
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker

from .story_kit import make_engine, new_project
from .test_workflow_media import FAKE_MODELS, SERVICES, serving

SCENE_FIRST = FAKE_MODELS.replace(
    '[models."f-edit"]\nkind = "image.edit"\nprovider = "fake"\n',
    '[models."f-edit"]\nkind = "image.edit"\nprovider = "fake"\nref_order = "scene_first"\n'
    'edit_template = "Place the person in the scene at true scale. {prompt} Keep the exact facial identity."\n')


def _produced(tmp_path: Path, models: str) -> tuple[FakeAdapter, Ledger, object, dict[str, object]]:
    engine, _, settings = make_engine(tmp_path, toml=models, services=SERVICES)
    pid = new_project(engine)
    engine.run(pid, None, {"visual_prompts": False})
    fake = FakeAdapter()
    reg = AdapterRegistry()
    reg.register(fake)
    ledger = engine.spec_store.ledger
    worker = Worker(settings, engine.db, reg, ledger=ledger, probes=[], workflow=engine)
    with serving(worker):
        engine.run_stage(1, "produce", {})
    jobs = {j.spec_id: j for j in ledger.list_jobs(project_id=1, limit=500)}
    shot = engine.store.list_shots(1)[0]
    key = engine.spec_store.current(1, "shot.keyframe", str(shot["id"]))
    return fake, ledger, key, {"job": jobs[key.id], "home": tmp_path / "home"}


def test_an_edit_model_with_scene_first_gets_the_scene_before_the_person_and_its_own_wording(tmp_path: Path) -> None:
    fake, ledger, key, ctx = _produced(tmp_path, SCENE_FIRST)
    job = ctx["job"]
    seen = fake.seen[job.id][0]            # type: ignore[attr-defined]
    made = {str(ctx["home"] / a.path): a for a in ledger.list_assets(project_id=1, limit=500)}   # type: ignore[operator]
    order = [made[r].spec_id for r in seen["refs"]]
    kinds = [next(s for s in ("character.portrait", "scene.keyframe") if _subject_of(ledger, spec_id) == s)
             for spec_id in order]
    assert kinds[0] == "scene.keyframe" and set(kinds[1:]) == {"character.portrait"}
    assert seen["prompt"].startswith("Place the person in the scene at true scale.")
    assert seen["prompt"].endswith("Keep the exact facial identity.")


def test_without_those_settings_the_order_and_the_prompt_stay_as_they_were(tmp_path: Path) -> None:
    fake, _, key, ctx = _produced(tmp_path, FAKE_MODELS)
    seen = fake.seen[ctx["job"].id][0]     # type: ignore[attr-defined]
    assert "Place the person in the scene" not in seen["prompt"]


def _subject_of(ledger: Ledger, spec_id: str | None) -> str:
    row = ledger.db.conn.execute("SELECT subject_type FROM gen_specs WHERE id=?", (spec_id,)).fetchone()
    return str(row["subject_type"])
