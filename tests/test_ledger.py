from __future__ import annotations

import random

import pytest

from storyagent.config import ConfigError, Settings
from storyagent.db import Database
from storyagent.domain import JobStatus, MediaKind, QualityTier
from storyagent.domain.errors import ErrorCode
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import Ledger, LedgerError, NewAsset


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def led(fdb: Database, fsettings: Settings, clock: Clock) -> Ledger:
    return Ledger(fdb, fsettings, clock=clock, rng=random.Random(1))


def job(**kw: object) -> MediaJob:
    base = {"kind": MediaKind.VIDEO_GENERATE, "inputs": {"prompt": "a cat"}, "project_id": 1, "shot_id": 1,
            "model_ref": "fake-video"}
    return MediaJob(**{**base, **kw})  # type: ignore[arg-type]


def asset(kind: str = "video.generate", slot: str = "main", path: str = "p/x.mp4") -> NewAsset:
    return NewAsset(kind=kind, slot=slot, path=path, sha256="h", bytes=3)


def test_enqueue_freezes_resolved_params_seed_and_model_facts(led: Ledger) -> None:
    rec, created = led.enqueue(job(tier=QualityTier.DRAFT, params={"fps": 12}))
    assert created and rec.status is JobStatus.QUEUED
    assert rec.params == {"width": 50, "fps": 12}  # tier + job layers, frozen
    assert rec.provider == "fake" and rec.license_tag == "test" and rec.est_memory_gib == 4
    assert rec.seed is not None and rec.heavy is False
    assert [e["event"] for e in led.events(rec.id)] == ["enqueued"]


def test_default_model_for_kind_and_kind_mismatch(led: Ledger) -> None:
    with pytest.raises(ConfigError):
        led.enqueue(job(model_ref="fake-tts"))  # audio model cannot run video.generate


def test_identical_active_request_is_deduplicated_but_new_seed_is_not(led: Ledger) -> None:
    a, created_a = led.enqueue(job())
    b, created_b = led.enqueue(job())
    assert created_a and not created_b and a.id == b.id
    c, created_c = led.enqueue(job(seed=123))
    d, created_d = led.enqueue(job(seed=124))
    assert created_c and created_d and len({a.id, c.id, d.id}) == 3
    assert c.seed == 123 and d.seed == 124


def test_resubmit_after_terminal_is_allowed(led: Ledger) -> None:
    a, _ = led.enqueue(job())
    led.request_cancel(a.id)
    b, created = led.enqueue(job())
    assert created and b.id != a.id


def test_claim_is_exclusive(led: Ledger) -> None:
    rec, _ = led.enqueue(job())
    first = led.claim(rec.id, "w1", 60)
    second = led.claim(rec.id, "w2", 60)
    assert first is not None and first.status is JobStatus.RUNNING and first.attempt == 1
    assert second is None


def test_queue_order_is_priority_then_age(led: Ledger, clock: Clock) -> None:
    a, _ = led.enqueue(job(seed=1, priority=5))
    clock.t += 1
    b, _ = led.enqueue(job(seed=2, priority=1))
    clock.t += 1
    c, _ = led.enqueue(job(seed=3, priority=5))
    assert [j.id for j in led.queued()] == [b.id, a.id, c.id]


def test_lease_expiry_requeues_keeping_external_id_then_fails_after_max_attempts(
    led: Ledger, clock: Clock
) -> None:
    rec, _ = led.enqueue(job(max_attempts=2))
    led.claim(rec.id, "w1", 10)
    led.set_external_id(rec.id, "w1", "prompt-1")
    assert led.requeue_expired() == []  # lease still valid
    clock.t += 11
    assert led.requeue_expired() == [(rec.id, "requeued")]
    again = led.get_job(rec.id)
    assert again.status is JobStatus.QUEUED and again.external_id == "prompt-1" and again.lease_owner is None
    led.claim(rec.id, "w2", 10)
    clock.t += 11
    assert led.requeue_expired() == [(rec.id, "failed")]
    final = led.get_job(rec.id)
    assert final.status is JobStatus.WORKFLOW_ERROR and final.error_code == ErrorCode.WORKER_LOST.value


def test_expired_job_with_pending_cancel_is_cancelled_not_requeued(led: Ledger, clock: Clock) -> None:
    rec, _ = led.enqueue(job())
    led.claim(rec.id, "w1", 10)
    assert led.request_cancel(rec.id) is JobStatus.RUNNING  # flagged only
    clock.t += 11
    assert led.requeue_expired() == [(rec.id, "cancelled")]
    assert led.get_job(rec.id).status is JobStatus.CANCELLED


def test_heartbeat_extends_lease_and_reports_cancel(led: Ledger, clock: Clock) -> None:
    rec, _ = led.enqueue(job())
    led.claim(rec.id, "w1", 10)
    clock.t += 8
    assert led.heartbeat(rec.id, "w1", 10, 0.4, "half") == (True, False)
    clock.t += 8
    assert led.requeue_expired() == []  # extended, so still alive
    assert led.heartbeat(rec.id, "intruder", 10) == (False, False)
    led.request_cancel(rec.id)
    assert led.heartbeat(rec.id, "w1", 10) == (True, True)
    j = led.get_job(rec.id)
    assert j.progress == pytest.approx(0.4) and j.progress_msg == "half"


def test_cancel_queued_is_immediate_and_terminal_states_are_sticky(led: Ledger) -> None:
    rec, _ = led.enqueue(job())
    assert led.request_cancel(rec.id) is JobStatus.CANCELLED
    assert led.claim(rec.id, "w", 10) is None
    again = led.fail(rec.id, ErrorCode.TIMEOUT, "late")
    assert again.status is JobStatus.CANCELLED  # first terminal state wins


def test_complete_requires_running_owner_and_assets(led: Ledger) -> None:
    rec, _ = led.enqueue(job())
    with pytest.raises(LedgerError):
        led.complete(rec.id, "w1", [asset()])  # not running
    led.claim(rec.id, "w1", 10)
    with pytest.raises(LedgerError):
        led.complete(rec.id, "other", [asset()])
    with pytest.raises(LedgerError):
        led.complete(rec.id, "w1", [])
    with pytest.raises(LedgerError):
        led.complete(rec.id, "w1", [asset(), asset(path="p/y.mp4")])  # same kind+slot twice
    assert led.get_job(rec.id).status is JobStatus.RUNNING  # failed attempts changed nothing


def test_candidates_versioning_and_adoption(led: Ledger) -> None:
    ids = []
    for seed in (1, 2, 3):
        rec, _ = led.enqueue(job(seed=seed))
        led.claim(rec.id, "w", 10)
        ids.append(led.complete(rec.id, "w", [asset(path=f"p/{seed}.mp4")])[0])
    assert [a.version for a in ids] == [1, 2, 3]
    assets = led.list_assets(project_id=1, shot_id=1, kind="video.generate")
    assert [a.is_current for a in assets] == [True, False, False]  # newest is current (auto_adopt)
    chosen = next(a for a in assets if a.version == 1)
    led.adopt(chosen.id)
    cur = led.list_assets(project_id=1, shot_id=1, current_only=True)
    assert [a.id for a in cur] == [chosen.id]
    led.trash_asset(chosen.id)
    assert led.list_assets(project_id=1, shot_id=1, current_only=True) == []  # slot left empty, by design
    with pytest.raises(LedgerError):
        led.adopt(chosen.id)


def test_auto_adopt_off_keeps_existing_current(tmp_path, clock) -> None:  # noqa: ANN001
    from storyagent.config import load_settings
    from storyagent.db import migrate

    home = tmp_path / "h"
    (home / "config").mkdir(parents=True)
    (home / "config" / "models.toml").write_text('[models.m]\nkind="video.generate"\nprovider="fake"\n')
    (home / "config" / "services.toml").write_text("[runtime]\nauto_adopt = false\n")
    s = load_settings({"STORY_AGENT_HOME": str(home)})
    db = Database(s.db_path)
    migrate(db)
    db.conn.execute("INSERT INTO projects(slug) VALUES ('p')")
    led = Ledger(db, s, clock=clock)
    out = []
    for seed in (1, 2):
        rec, _ = led.enqueue(MediaJob(MediaKind.VIDEO_GENERATE, {}, project_id=1, model_ref="m", seed=seed))
        led.claim(rec.id, "w", 10)
        out.append(led.complete(rec.id, "w", [asset(path=f"{seed}.mp4")])[0])
    cur = led.list_assets(project_id=1, current_only=True)
    assert [a.version for a in cur] == [1]  # first became current; the second stayed a candidate
    db.close()


def test_project_level_assets_without_shot_version_independently(led: Ledger) -> None:
    for seed in (1, 2):
        rec, _ = led.enqueue(job(shot_id=None, seed=seed))
        led.claim(rec.id, "w", 10)
        led.complete(rec.id, "w", [asset(path=f"m{seed}.mp4")])
    a = led.list_assets(project_id=1, kind="video.generate")
    assert sorted(x.version for x in a) == [1, 2] and sum(x.is_current for x in a) == 1


def test_slots_are_independent(led: Ledger) -> None:
    rec, _ = led.enqueue(job())
    led.claim(rec.id, "w", 10)
    got = led.complete(rec.id, "w", [asset(), asset(slot="thumb", path="t.png")])
    assert {a.slot for a in got} == {"main", "thumb"} and all(a.is_current for a in got)


def test_retry_links_parent_and_can_reseed(led: Ledger) -> None:
    rec, _ = led.enqueue(job())
    with pytest.raises(LedgerError):
        led.retry(rec.id)  # still queued
    led.claim(rec.id, "w", 10)
    led.fail(rec.id, ErrorCode.SERVICE_UNREACHABLE, "down", service="comfy")
    assert led.get_job(rec.id).status is JobStatus.TRANSPORT_ERROR
    same = led.retry(rec.id)
    assert same.parent_job_id == rec.id and same.seed == rec.seed and same.params == rec.params
    led.request_cancel(same.id)
    reseeded = led.retry(same.id, new_seed=True)
    assert reseeded.seed != rec.seed and reseeded.parent_job_id == same.id


def test_a_retried_job_says_so_and_cannot_be_retried_twice_while_the_retry_waits(led: Ledger) -> None:
    rec, _ = led.enqueue(job())
    led.claim(rec.id, "w", 10)
    led.fail(rec.id, ErrorCode.SERVICE_UNREACHABLE, "down", service="comfy")
    assert led.get_job(rec.id).retried_by is None and led.get_job(rec.id).retries == 0
    first = led.retry(rec.id)
    shown = led.get_job(rec.id)
    assert (shown.retried_by, shown.retried_status, shown.retries) == (first.id, "queued", 1)
    assert [j.retried_by for j in led.list_jobs() if j.id == rec.id] == [first.id]
    with pytest.raises(LedgerError, match="already retried"):
        led.retry(rec.id)                                    # the second click on the same job
    led.claim(first.id, "w", 10)
    led.fail(first.id, ErrorCode.SERVICE_UNREACHABLE, "down", service="comfy")
    again = led.retry(rec.id)                                # the retry failed too: another one is allowed
    assert led.get_job(rec.id).retries == 2 and led.get_job(rec.id).retried_by == again.id


def test_failure_event_carries_params_for_i18n(led: Ledger) -> None:
    rec, _ = led.enqueue(job())
    led.claim(rec.id, "w", 10)
    led.fail(rec.id, ErrorCode.SERVICE_UNREACHABLE, "down", service="comfy")
    ev = [e for e in led.events(rec.id) if e["event"] == "failed"][0]
    assert ev["data"]["code"] == "service_unreachable" and ev["data"]["params"] == {"service": "comfy"}


def test_estimate_uses_median_of_recent_completions(led: Ledger, clock: Clock, fdb: Database) -> None:
    assert led.estimate_seconds("fake-video", "final") == 0
    for seed, secs in enumerate((10, 30, 20), start=100):
        rec, _ = led.enqueue(job(seed=seed))
        led.claim(rec.id, "w", 100)
        clock.t += secs
        led.complete(rec.id, "w", [asset(path=f"{seed}.mp4")])
        clock.t += 1
    assert led.estimate_seconds("fake-video", "final") == 20
    nxt, _ = led.enqueue(job(seed=999))
    assert nxt.est_seconds == 20  # ETA available at enqueue time


def test_list_filters(led: Ledger) -> None:
    a, _ = led.enqueue(job(seed=1))
    b, _ = led.enqueue(job(seed=2, shot_id=None))
    led.request_cancel(b.id)
    assert {j.id for j in led.list_jobs(project_id=1)} == {a.id, b.id}
    assert [j.id for j in led.list_jobs(status=["queued"])] == [a.id]
    assert [j.id for j in led.list_jobs(shot_id=1)] == [a.id]
    assert [j.id for j in led.list_jobs(status=[JobStatus.CANCELLED])] == [b.id]
