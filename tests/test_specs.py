from __future__ import annotations

import random

import pytest

from storyagent.config import ConfigError, Settings
from storyagent.db import Database
from storyagent.domain import JobStatus, MediaKind, QualityTier
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.prompts import render_prompt
from storyagent.media.registry import AdapterRegistry
from storyagent.media.specs import SpecError, SpecLocked, SpecStore
from storyagent.media.worker import Worker

PORTRAIT = "character.portrait"


@pytest.fixture
def store(fdb: Database, fsettings: Settings) -> SpecStore:
    return SpecStore(fdb, fsettings, rng=random.Random(7))


def make_portrait(store: SpecStore, **kw: object):  # noqa: ANN201
    return store.create(1, PORTRAIT, "7", MediaKind.VIDEO_GENERATE, model_ref="fake-video",
                        prompt="a tall swordswoman", **kw)  # type: ignore[arg-type]


def test_create_edit_creates_versions_and_noop_edits_do_not(store: SpecStore) -> None:
    v1 = make_portrait(store, seed=5)
    assert v1.version == 1 and v1.parent_version is None and v1.author == "user"
    same = store.edit(1, PORTRAIT, "7", prompt="a tall swordswoman")
    assert same.id == v1.id  # nothing changed -> no new version
    v2 = store.edit(1, PORTRAIT, "7", prompt="a tall swordswoman, scar on cheek", note="add scar", author="agent")
    assert (v2.version, v2.parent_version, v2.author, v2.note) == (2, 1, "agent", "add scar")
    assert v2.seed == 5 and v2.model_ref == "fake-video"  # untouched fields carry over
    assert [v.version for v in store.versions(1, PORTRAIT, "7")] == [1, 2]
    assert store.current(1, PORTRAIT, "7").id == v2.id  # type: ignore[union-attr]


def test_params_patch_merges_while_params_replaces(store: SpecStore) -> None:
    make_portrait(store, params={"a": 1, "b": 2})
    assert store.edit(1, PORTRAIT, "7", params_patch={"b": 3, "c": 4}).params == {"a": 1, "b": 3, "c": 4}
    assert store.edit(1, PORTRAIT, "7", params={"z": 0}).params == {"z": 0}


def test_create_twice_and_bad_input(store: SpecStore) -> None:
    make_portrait(store)
    with pytest.raises(SpecError, match="already exists"):
        make_portrait(store)
    with pytest.raises(SpecError, match="subject_type"):
        store.create(1, "Bad Type", "1", MediaKind.VIDEO_GENERATE)
    with pytest.raises(ConfigError):
        store.create(1, "x.y", "1", MediaKind.VIDEO_GENERATE, model_ref="fake-tts")  # wrong kind
    with pytest.raises(SpecError, match="cannot edit"):
        store.edit(1, PORTRAIT, "7", kind="audio.tts")
    with pytest.raises(SpecError):
        store.edit(1, PORTRAIT, "404", prompt="x")


def test_custom_subject_types_are_allowed(store: SpecStore) -> None:
    s = store.create(1, "prop.turnaround", "sword", MediaKind.VIDEO_GENERATE)
    assert s.subject_type == "prop.turnaround"


def test_lock_blocks_edits_until_unlocked(store: SpecStore) -> None:
    make_portrait(store)
    store.lock(1, PORTRAIT, "7", note="approved look")
    assert store.is_locked(1, PORTRAIT, "7")
    with pytest.raises(SpecLocked):
        store.edit(1, PORTRAIT, "7", prompt="changed")
    store.unlock(1, PORTRAIT, "7")
    assert store.edit(1, PORTRAIT, "7", prompt="changed").version == 2


def test_revert_creates_a_new_version_with_old_content_and_history_is_kept(store: SpecStore) -> None:
    make_portrait(store)
    store.edit(1, PORTRAIT, "7", prompt="v2 prompt")
    store.edit(1, PORTRAIT, "7", prompt="v3 prompt")
    back = store.revert_to(1, PORTRAIT, "7", 1)
    assert back.version == 4 and back.prompt == "a tall swordswoman" and back.note == "revert to v1"
    assert [v.prompt for v in store.versions(1, PORTRAIT, "7")][1:3] == ["v2 prompt", "v3 prompt"]
    with pytest.raises(SpecError):
        store.revert_to(1, PORTRAIT, "7", 99)


def test_diff_lists_only_changed_fields(store: SpecStore) -> None:
    a = make_portrait(store)
    b = store.edit(1, PORTRAIT, "7", prompt="new", seed=9)
    assert SpecStore.diff(a, b) == {"prompt": ("a tall swordswoman", "new"), "seed": (None, 9)}


def test_slots_are_separate_subjects(store: SpecStore) -> None:
    make_portrait(store)
    store.create(1, PORTRAIT, "7", MediaKind.VIDEO_GENERATE, slot="alt", prompt="alt look")
    store.edit(1, PORTRAIT, "7", "alt", prompt="alt look 2")
    assert store.current(1, PORTRAIT, "7").version == 1  # type: ignore[union-attr]
    assert store.current(1, PORTRAIT, "7", "alt").version == 2  # type: ignore[union-attr]


# ---- spec -> job --------------------------------------------------------------------------------
def test_job_is_a_frozen_snapshot_of_the_spec_version(store: SpecStore) -> None:
    spec = make_portrait(store, negative_prompt="blurry", params={"steps": 5}, refs=[{"image": "a.png"}])
    [job] = store.regenerate(1, PORTRAIT, "7", shot_id=1)
    assert job.spec_id == spec.id and job.spec_version == 1 and job.slot == "character.portrait:7" and job.shot_id == 1
    assert job.inputs["prompt"] == "a tall swordswoman" and job.inputs["negative_prompt"] == "blurry"
    assert job.inputs["refs"] == [{"image": "a.png"}]
    assert job.inputs["subject"] == {"type": PORTRAIT, "id": "7", "slot": "main"}
    assert job.params["steps"] == 5 and job.params["width"] == 200  # spec params over model/tier layers
    store.edit(1, PORTRAIT, "7", prompt="completely different")
    assert store.ledger.get_job(job.id).inputs["prompt"] == "a tall swordswoman"  # in-flight job unaffected


def test_text_payload_carries_dialogue_or_lyrics(store: SpecStore) -> None:
    s = store.create(1, "music.theme_song", "main", MediaKind.VIDEO_GENERATE, model_ref="fake-video",
                     prompt="epic erhu, 90 bpm", text_payload="[verse]\nla la la")
    job = store.build_job(s)
    assert job.inputs["prompt"] == "epic erhu, 90 bpm" and job.inputs["text"] == "[verse]\nla la la"


def test_regenerate_makes_distinct_candidates_and_only_one_is_current(
    store: SpecStore, fsettings: Settings, fdb: Database
) -> None:
    make_portrait(store)
    jobs = store.regenerate(1, PORTRAIT, "7", n_candidates=3, shot_id=1)
    assert len({j.seed for j in jobs}) == 3 and len({j.id for j in jobs}) == 3
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    w = Worker(fsettings, fdb, reg, ledger=store.ledger, probes=[])
    assert w.drain(15)
    assets = store.ledger.list_assets(project_id=1, shot_id=1)
    assert len(assets) == 3 and sum(a.is_current for a in assets) == 1
    assert {a.spec_version for a in assets} == {1}
    # the creator prefers the first candidate:
    first = min(assets, key=lambda a: a.version)
    store.ledger.adopt(first.id)
    assert [a.id for a in store.ledger.list_assets(project_id=1, shot_id=1, current_only=True)] == [first.id]
    w.shutdown()


def test_regenerate_validation(store: SpecStore) -> None:
    make_portrait(store, seed=11)
    with pytest.raises(SpecError):
        store.regenerate(1, PORTRAIT, "7", n_candidates=2, vary_seed=False)
    with pytest.raises(SpecError):
        store.regenerate(1, PORTRAIT, "7", n_candidates=0)
    with pytest.raises(SpecError):
        store.regenerate(1, PORTRAIT, "nope")
    [j] = store.regenerate(1, PORTRAIT, "7", vary_seed=False)
    assert j.seed == 11  # reproduce exactly with the spec's own seed
    again = store.regenerate(1, PORTRAIT, "7", vary_seed=False)
    assert again[0].id == j.id  # identical active request is deduplicated


def test_tier_is_chosen_at_regeneration_time(store: SpecStore) -> None:
    make_portrait(store)
    [draft] = store.regenerate(1, PORTRAIT, "7", tier=QualityTier.DRAFT)
    [final] = store.regenerate(1, PORTRAIT, "7", tier=QualityTier.FINAL)
    assert draft.params["width"] == 50 and final.params["width"] == 200 and draft.tier is QualityTier.DRAFT


# ---- staleness / dependencies -------------------------------------------------------------------
def test_assets_become_stale_when_their_spec_moves_on(store: SpecStore, fsettings: Settings, fdb: Database) -> None:
    make_portrait(store)
    store.regenerate(1, PORTRAIT, "7", shot_id=1)
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    w = Worker(fsettings, fdb, reg, ledger=store.ledger, probes=[])
    assert w.drain(10)
    assert store.stale_assets(1) == []
    store.edit(1, PORTRAIT, "7", prompt="redesigned")
    [(asset, latest)] = store.stale_assets(1)
    assert asset.spec_version == 1 and latest == 2
    store.regenerate(1, PORTRAIT, "7", shot_id=1)
    assert w.drain(10)
    assert store.stale_assets(1) == []  # the new current asset was made from v2
    w.shutdown()


def test_dependents_need_review_after_a_dependency_changes(store: SpecStore) -> None:
    make_portrait(store)
    shot = store.create(
        1, "shot.video", "1", MediaKind.VIDEO_GENERATE, prompt="she draws her sword",
        depends_on=[{"subject_type": PORTRAIT, "subject_id": "7", "slot": "main", "version": 1}],
    )
    assert store.needs_review(1) == []
    store.edit(1, PORTRAIT, "7", prompt="now with a red cloak")
    [item] = store.needs_review(1)
    assert item.spec.id == shot.id
    assert item.changed_dependencies[0]["recorded"] == 1 and item.changed_dependencies[0]["current"] == 2
    reviewed = store.acknowledge_dependencies(item.spec)
    assert reviewed.version == 2 and reviewed.depends_on[0]["version"] == 2
    assert store.needs_review(1) == []


def test_locked_dependents_still_flag_but_cannot_be_acknowledged_until_unlocked(store: SpecStore) -> None:
    make_portrait(store)
    shot = store.create(1, "shot.video", "1", MediaKind.VIDEO_GENERATE,
                        depends_on=[{"subject_type": PORTRAIT, "subject_id": "7", "version": 1}])
    store.lock(1, "shot.video", "1")
    store.edit(1, PORTRAIT, "7", prompt="changed")
    assert len(store.needs_review(1)) == 1
    with pytest.raises(SpecLocked):
        store.acknowledge_dependencies(shot)


# ---- prompt dialects ----------------------------------------------------------------------------
def test_h3_structured_dialect() -> None:
    out = render_prompt("h3_structured", "A short drama shot.",
                        {"scene": "rainy street", "action": "she turns", "audio": "rain and a distant bell"})
    assert out == "A short drama shot.\nScene: rainy street\nAction: she turns\nAudio: rain and a distant bell"
    assert render_prompt("h3_structured", " plain ", None) == "plain"
    assert render_prompt("plain", "  x ") == "x"
    with pytest.raises(ConfigError):
        render_prompt("nope", "x")


def test_structured_params_use_the_model_dialect(store: SpecStore, fsettings: Settings) -> None:
    s = store.create(1, "shot.video", "2", MediaKind.VIDEO_GENERATE, model_ref="fake-video", prompt="p",
                     params={"structured": {"scene": "s"}, "steps": 3})
    job = store.build_job(s)
    assert "structured" not in job.params and job.params["steps"] == 3  # not leaked to the adapter as a param
    assert job.inputs["prompt"] == "p"  # fake-video uses the plain dialect


def test_job_end_to_end_status(store: SpecStore, fsettings: Settings, fdb: Database) -> None:
    make_portrait(store)
    [rec] = store.regenerate(1, PORTRAIT, "7", shot_id=1)
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    w = Worker(fsettings, fdb, reg, ledger=store.ledger, probes=[])
    assert w.drain(10)
    assert store.ledger.get_job(rec.id).status is JobStatus.COMPLETED
    w.shutdown()


def test_assets_of_different_subjects_never_share_a_current_slot(store: SpecStore) -> None:
    """The ledger keeps one current asset per (project, shot, kind, slot): two portraits must not demote each other."""
    a = store.create(1, "character.portrait", "1", MediaKind.VIDEO_GENERATE, model_ref="fake-video", prompt="a")
    b = store.create(1, "character.portrait", "2", MediaKind.VIDEO_GENERATE, model_ref="fake-video", prompt="b")
    line0 = store.create(1, "dialogue.line", "1#0", MediaKind.AUDIO_TTS, model_ref="fake-tts", text_payload="x")
    line1 = store.create(1, "dialogue.line", "1#1", MediaKind.AUDIO_TTS, model_ref="fake-tts", text_payload="y")
    slots = {store.job_slot(s) for s in (a, b, line0, line1)}
    assert len(slots) == 4
    video = store.create(1, "shot.video", "5", MediaKind.VIDEO_GENERATE, model_ref="fake-video", prompt="v")
    assert store.job_slot(video) == "main"                       # one per shot: keeps the plain slot
    variant = store.create(1, "scene.keyframe", "3", MediaKind.VIDEO_GENERATE, slot="night", model_ref="fake-video")
    assert store.job_slot(variant) == "scene.keyframe:3:night"
