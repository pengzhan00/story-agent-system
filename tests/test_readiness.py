"""What is about to be made is matched to the models that will make it before anything is queued: how many reference
images each model takes (one statement per model, with the models that take more), and the worker's own job gate
asked about the jobs a run would queue."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from storyagent.db import Database, migrate
from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media import gate, refs
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.assetrefs import Resolver
from storyagent.media.contracts import MediaJob, ReferenceRule, ValidationIssue
from storyagent.media.ledger import Ledger
from storyagent.media.registry import AdapterRegistry, default_registry
from storyagent.workflow import preflight
from storyagent.workflow.media import reference_uses, uploaded_reference_uses

from .adapter_kit import make_settings
from .test_full_run import _full, _rig

MODELS = """
[defaults]
"image.generate" = "one"
"image.edit" = "edit"
[models."one"]
kind = "image.generate"
provider = "fake"
max_refs = 1
[models."many"]
kind = "image.generate"
provider = "fake"
max_refs = 8
[models."first"]
kind = "image.generate"
provider = "fake"
used_refs = 1
[models."unsaid"]
kind = "image.generate"
provider = "fake"
[models."gone"]
kind = "image.generate"
provider = "nobody"
max_refs = 16
[models."edit"]
kind = "image.edit"
provider = "fake"
max_refs = 2
"""
# the adapters whose reference images depend on the model entry (a workflow, a connector, a stand-in): they state
# nothing themselves, `max_refs` / `used_refs` in models.toml do
BY_MODEL_ENTRY = {"comfy", "cli", "http", "control-tools", "fake"}


def _registry() -> AdapterRegistry:
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    return reg


def test_every_adapter_that_makes_pictures_states_its_references_or_leaves_them_to_the_model_entry(
        tmp_path: Path) -> None:
    said, left = set(), set()
    reg = default_registry(make_settings(tmp_path))
    for provider in reg.providers():
        adapter = reg.get(provider)
        assert adapter is not None
        kinds = [k for k in adapter.capabilities().kinds if k.value in refs.IMAGE_KINDS]
        if not kinds:
            continue
        rules = [adapter.reference_rule(k) for k in kinds]
        if all(isinstance(r, ReferenceRule) for r in rules):
            said.add(provider)
        else:
            assert rules == [None] * len(rules), provider      # all or nothing: no half-stated adapter
            left.add(provider)
    assert {"drawthings", "h3image", "mflux"} <= said
    assert left <= BY_MODEL_ENTRY, f"say what references these take, or list them: {left - BY_MODEL_ENTRY}"


def test_the_rule_of_a_model_is_its_entry_then_its_adapter_then_unknown(tmp_path: Path) -> None:
    cfg, reg = make_settings(tmp_path, models=MODELS), _registry()
    assert refs.rule_of(cfg, reg, "one", "image.generate") == ReferenceRule(most=1)
    assert refs.rule_of(cfg, reg, "first", "image.generate") == ReferenceRule(used=1)
    assert refs.rule_of(cfg, reg, "unsaid", "image.generate") is None
    assert refs.rule_of(cfg, reg, "no-such-model", "image.generate") is None
    reg.get("fake").reference_rule = lambda kind: ReferenceRule(most=3)     # type: ignore[method-assign,union-attr]
    assert refs.rule_of(cfg, reg, "unsaid", "image.generate") == ReferenceRule(most=3)
    assert refs.rule_of(cfg, reg, "one", "image.generate") == ReferenceRule(most=1)       # the entry wins
    assert ReferenceRule(most=4, used=1).room == 1 and ReferenceRule().room is None
    # the models that take several: stated, of the same kind, their adapter there; a stated number first
    roomy = refs.roomy_models(cfg, reg, "image.generate", 3, exclude="one")
    assert [m.text() for m in roomy] == ["unsaid (3)", "many (8)"]
    assert refs.roomy_models(cfg, reg, "image.generate", 9) == []


def test_too_many_reference_images_are_found_with_the_models_that_take_them(tmp_path: Path) -> None:
    cfg, reg = make_settings(tmp_path, models=MODELS), _registry()
    use = preflight.RefUse
    found, refused = preflight.check_references(cfg, reg, [
        use("a", "character.portrait", "image.generate", "one", 2, "Lin Wan"),
        use("b", "shot.keyframe", "image.generate", "one", 3),
        use("c", "character.portrait", "image.generate", "one", 1, "Zhao Heng"),      # fits
        use("d", "character.portrait", "image.generate", "first", 2, "Lin Wan"),      # read, but only the first
        use("e", "shot.keyframe", "image.edit", "edit", 3),                           # no edit model takes three
        use("f", "character.portrait", "image.generate", "unsaid", 9, "Lin Wan"),     # unknown: never reported
    ])
    assert refused == {"a", "b", "e"}
    assert [(f.code, f.level, f.subject, f.detail, f.values) for f in found] == [
        ("too_many_refs_only", "block", "edit", "", {"limit": "2", "count": "1", "most": "3", "models": ""}),
        ("refs_dropped", "warn", "first", "Lin Wan", {"limit": "1", "count": "1", "most": "2", "models": "many (8)"}),
        ("too_many_refs", "block", "one", "Lin Wan", {"limit": "1", "count": "2", "most": "3", "models": "many (8)"}),
    ]
    # before the story has specs, the pictures uploaded for the people are already known
    people: list[dict[str, Any]] = [{"id": 1, "name": "Lin Wan", "ref_images": ["a.png", "b.png"]},
                                    {"id": 2, "name": "Zhao Heng", "ref_images": []}]
    early = preflight.check_references(cfg, reg, uploaded_reference_uses(cfg, people))[0]
    assert [(f.code, f.subject, f.detail) for f in early] == [("too_many_refs", "one", "Lin Wan")]


def test_the_jobs_a_run_would_queue_are_shown_to_the_workers_gate_without_queueing_them(tmp_path: Path) -> None:
    cfg, reg = make_settings(tmp_path, models=MODELS + '[models."snd"]\nkind = "audio.sfx"\nprovider = "fake"\n'), \
        _registry()
    db = Database(cfg.db_path)
    migrate(db)
    ledger = Ledger(db, cfg)

    class Spec:
        def __init__(self, subject_type: str) -> None:
            self.subject_type = subject_type

    def job(kind: MediaKind, model: str, **inputs: Any) -> MediaJob:
        return MediaJob(kind=kind, inputs={"prompt": "x", **inputs}, model_ref=model)

    bad = {"fake_validate": ErrorCode.INVALID_PARAMS.value}
    jobs: list[tuple[Any, MediaJob]] = [
        (Spec("character.portrait"), job(MediaKind.IMAGE_GENERATE, "one", **bad)),
        (Spec("character.portrait"), job(MediaKind.IMAGE_GENERATE, "one", **bad)),      # same shape: asked once
        (Spec("scene.keyframe"), job(MediaKind.IMAGE_GENERATE, "one")),                 # fine
        (Spec("shot.sfx"), job(MediaKind.AUDIO_SFX, "snd", **bad)),                     # a film stands without it
        (Spec("character.portrait"), job(MediaKind.IMAGE_GENERATE, "gone", **bad)),     # no adapter: said elsewhere
    ]
    found = preflight.check_jobs(cfg, db, ledger, reg, jobs, project_settings={}, project_rating="general")
    assert [(f.code, f.level, f.subject, f.detail, f.values) for f in found] == [
        ("refused", "block", "one", "fake validation",
         {"count": "2", "what": "character.portrait", "error": "invalid_params"}),
        ("refused", "warn", "snd", "fake validation", {"count": "1", "what": "shot.sfx", "error": "invalid_params"}),
    ]
    assert ledger.list_jobs(limit=5) == []                                  # nothing was queued
    assert preflight.check_jobs(cfg, db, ledger, reg, jobs, project_settings={}, project_rating="general",
                                skip_providers={"fake"}) == []
    # the draft is the record the queue would store, and the worker asks the very same gate
    rec = ledger.draft(jobs[0][1])
    assert (rec.provider, rec.model_ref, rec.id) == ("fake", "one", "") and rec.inputs["prompt"] == "x"
    checked = gate.check(cfg, db, ledger, FakeAdapter(), Resolver(cfg, ledger), rec, tmp_path / "never",
                         project_settings={}, project_rating="general")
    assert checked.issues == [ValidationIssue(ErrorCode.INVALID_PARAMS, "fake validation", {"detail": "fake"})]
    assert not (tmp_path / "never").exists()
    db.close()


def test_a_scheduling_worker_leaves_a_sign_the_api_can_read(tmp_path: Path) -> None:
    from storyagent.media.worker import Worker, beat_path, worker_age

    cfg = make_settings(tmp_path, models=MODELS)
    assert worker_age(cfg) is None
    assert [(f.code, f.level, f.detail) for f in preflight.check_worker(None)] == [("no_worker", "warn", "")]
    db = Database(cfg.db_path)
    migrate(db)
    worker = Worker(cfg, db, _registry(), probes=[])
    worker.say_alive()
    age = worker_age(cfg)
    assert age is not None and age < 5 and preflight.check_worker(age) == []
    beat_path(cfg).unlink()
    worker.say_alive()                                   # not more often than every few seconds
    assert worker_age(cfg) is None
    # a sign that is old is a worker that stopped
    assert [f.detail for f in preflight.check_worker(preflight.WORKER_QUIET + 30)] == ["90"]
    worker.shutdown()
    db.close()


def test_a_run_stops_before_the_first_job_when_a_model_cannot_take_the_references(tmp_path: Path) -> None:
    engine, worker, fake = _rig(tmp_path)
    engine.run(1, None, {"visual_prompts": False})                          # the story, up to the specs
    who = engine.store.list_characters(1)[0]
    picture = engine.settings.home / "refs"
    picture.mkdir(parents=True, exist_ok=True)
    for name in ("a.png", "b.png"):
        (picture / name).write_bytes(b"png")
    engine.store.update_character(who["id"], {"ref_images": [str(picture / "a.png"), str(picture / "b.png")]})
    engine.run(1, ["specs"], {"visual_prompts": False, "force": True})
    specs = engine.spec_store.list_current(1)
    uses = reference_uses(engine.settings, specs, {}, {("character.portrait", str(who["id"])): who["name"]})
    assert max(u.count for u in uses) >= 2 and any(u.name == who["name"] for u in uses)
    fake.reference_rule = lambda kind: ReferenceRule(most=1)                # type: ignore[method-assign]
    run = _full(engine, worker)
    assert run["status"] == "failed" and run["error_code"] == "workflow.not_ready"
    assert "too_many_refs" in run["error_message"]
    assert engine.spec_store.ledger.list_jobs(project_id=1, limit=10) == []      # not one job was queued


def test_comfy_template_capacity_reaches_readiness_without_model_max_refs(tmp_path: Path) -> None:
    from storyagent.media.adapters._http import ServiceClient
    from storyagent.media.adapters.comfy import ComfyAdapter

    models = '''
[models."plain"]
kind = "image.generate"
provider = "comfy"
[models."reference"]
kind = "image.generate"
provider = "comfy"
'''
    cfg = make_settings(tmp_path, models=models)
    reg = AdapterRegistry()
    reg.register(ComfyAdapter(ServiceClient(cfg.service("comfy")),
                             {"plain": "stable_audio_3", "reference": "flux2_klein_edit"}))
    assert refs.rule_of(cfg, reg, "plain", "image.generate") == ReferenceRule(most=0)
    assert refs.rule_of(cfg, reg, "reference", "image.generate") == ReferenceRule()
    assert [m.ref for m in refs.roomy_models(cfg, reg, "image.generate", 2)] == ["reference"]
