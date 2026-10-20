from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from storyagent.config import Settings
from storyagent.db import Database
from storyagent.domain import JobStatus, MediaKind, QualityTier
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import Ledger
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker


class StubProbe:
    name = "stub"

    def __init__(self, resident: list[str]) -> None:
        self._resident, self.unloaded = list(resident), []

    def resident(self) -> list[str]:
        return list(self._resident)

    def unload(self, models: list[str]) -> list[str]:
        self.unloaded += models
        self._resident = [m for m in self._resident if m not in models]
        return models


def make(fsettings: Settings, fdb: Database, adapter: FakeAdapter | None = None, **kw):  # noqa: ANN003, ANN201
    reg = AdapterRegistry()
    adapter = adapter or FakeAdapter()
    reg.register(adapter)
    ledger = kw.pop("ledger", None) or Ledger(fdb, fsettings)
    return Worker(fsettings, fdb, reg, ledger=ledger, probes=kw.pop("probes", []), **kw), ledger, adapter


def submit(led: Ledger, model: str = "fake-video", kind: MediaKind = MediaKind.VIDEO_GENERATE, **inputs):  # noqa: ANN003, ANN201
    ps = inputs.pop("_project_settings", None)
    seed = inputs.pop("_seed", None)
    rec, _ = led.enqueue(MediaJob(kind, inputs, project_id=1, shot_id=1, model_ref=model, seed=seed), ps)
    return rec


def test_success_stores_verified_asset_with_provenance(fsettings: Settings, fdb: Database) -> None:
    w, led, _ = make(fsettings, fdb)
    rec = submit(led, prompt="a cat", fake_steps=2)
    assert w.drain(10)
    job = led.get_job(rec.id)
    assert job.status is JobStatus.COMPLETED and job.progress == 1 and job.error_code is None
    [asset] = led.list_assets(project_id=1)
    assert asset.is_current and asset.version == 1 and asset.job_id == rec.id
    path = fsettings.home / asset.path
    assert path.is_file() and path.parent == fsettings.project_media_dir("demo", "video.generate", 1)
    assert asset.bytes == path.stat().st_size > 0 and len(asset.sha256) == 64
    p = asset.provenance
    assert p["aigc"] is True and p["model"]["ref"] == "fake-video" and p["seed"] == rec.seed
    assert p["params"] == rec.params and p["compliance"]["enabled"] is True
    assert not (fsettings.home / "work" / rec.id).exists()  # scratch removed
    events = [e["event"] for e in led.events(rec.id)]
    assert events[:3] == ["enqueued", "claimed", "submitted"] and events[-1] == "completed"
    w.shutdown()


def test_two_candidates_then_adopt_old_one(fsettings: Settings, fdb: Database) -> None:
    w, led, _ = make(fsettings, fdb)
    a, b = submit(led, _seed=1), submit(led, _seed=2)
    assert w.drain(10)
    assets = sorted(led.list_assets(project_id=1), key=lambda x: x.version)
    assert [x.version for x in assets] == [1, 2] and sum(x.is_current for x in assets) == 1
    assert {x.job_id for x in assets} == {a.id, b.id}
    w.shutdown()


@pytest.mark.parametrize(
    ("inputs", "code", "status"),
    [
        ({"fake_fail": "workflow_failed"}, ErrorCode.WORKFLOW_FAILED, JobStatus.WORKFLOW_ERROR),
        ({"fake_fail": "service_unreachable"}, ErrorCode.SERVICE_UNREACHABLE, JobStatus.TRANSPORT_ERROR),
        ({"fake_validate": "missing_model"}, ErrorCode.MISSING_MODEL, JobStatus.FAILED_VALIDATION),
        ({"fake_no_output": True}, ErrorCode.OUTPUT_MISSING, JobStatus.FAILED_VALIDATION),
        ({"fake_empty": True}, ErrorCode.OUTPUT_INVALID, JobStatus.FAILED_VALIDATION),
    ],
)
def test_failures_are_classified_and_never_register_assets(
    fsettings: Settings, fdb: Database, inputs: dict, code: ErrorCode, status: JobStatus
) -> None:
    w, led, _ = make(fsettings, fdb)
    rec = submit(led, **inputs)
    assert w.drain(10)
    job = led.get_job(rec.id)
    assert job.status is status and job.error_code == code.value
    assert led.list_assets(project_id=1) == []  # 'completed' is only ever recorded together with assets
    w.shutdown()


def test_unregistered_provider(fsettings: Settings, fdb: Database) -> None:
    led = Ledger(fdb, fsettings)
    w = Worker(fsettings, fdb, AdapterRegistry(), ledger=led, probes=[])
    rec = submit(led)
    assert w.drain(10)
    assert led.get_job(rec.id).error_code == ErrorCode.MISSING_TOOL.value
    w.shutdown()


def test_media_files_are_verified_with_ffprobe(fsettings: Settings, fdb: Database) -> None:
    import shutil

    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe not installed")
    w, led, _ = make(fsettings, fdb)
    rec = submit(led, fake_ext="mp4")  # garbage bytes pretending to be a video
    assert w.drain(10)
    job = led.get_job(rec.id)
    assert job.error_code == ErrorCode.OUTPUT_INVALID.value
    assert not list(fsettings.project_media_dir("demo", "video.generate", 1).glob("*"))  # bad file removed
    w.shutdown()


def test_extra_artifact_gets_its_own_slot(fsettings: Settings, fdb: Database) -> None:
    w, led, _ = make(fsettings, fdb)
    submit(led, fake_multi=True)
    assert w.drain(10)
    assert {(a.slot, a.is_current) for a in led.list_assets(project_id=1)} == {("main", True), ("thumb", True)}
    w.shutdown()


def test_parameter_validation_and_strict_mode(fsettings: Settings, fdb: Database) -> None:
    schema = {"width": {"type": "int", "min": 64}}
    w, led, _ = make(fsettings, fdb, FakeAdapter(schema=schema))
    ok = submit(led, _seed=1)  # model params: width=100, fps=8 -> 'fps' unknown but allowed (warning)
    assert w.drain(10)
    assert led.get_job(ok.id).status is JobStatus.COMPLETED
    assert any(e["event"] == "param_warning" for e in led.events(ok.id))
    w.shutdown()

    strict = replace(fsettings, runtime={**fsettings.runtime, "strict_params": True})
    w2, led2, _ = make(strict, fdb, FakeAdapter(schema=schema), ledger=Ledger(fdb, strict))
    bad = submit(led2, _seed=2)
    assert w2.drain(10)
    j = led2.get_job(bad.id)
    assert j.status is JobStatus.FAILED_VALIDATION and j.error_code == ErrorCode.INVALID_PARAMS.value
    w2.shutdown()


# ---- cancellation / timeout ---------------------------------------------------------------------
def test_cancel_running_job(fsettings: Settings, fdb: Database) -> None:
    w, led, adapter = make(fsettings, fdb)
    rec = submit(led, fake_hang=True)
    w.tick()
    deadline = time.monotonic() + 5
    while led.get_job(rec.id).status is not JobStatus.RUNNING and time.monotonic() < deadline:
        time.sleep(0.01)
    assert led.request_cancel(rec.id) is JobStatus.RUNNING
    assert w.drain(10)
    assert led.get_job(rec.id).status is JobStatus.CANCELLED
    assert ("cancel", rec.id) in adapter.calls
    assert led.list_assets(project_id=1) == []
    w.shutdown()


def test_timeout_cancels_the_adapter(fsettings: Settings, fdb: Database) -> None:
    w, led, adapter = make(fsettings, fdb)
    rec = submit(led, model="fake-slow", fake_hang=True)  # model timeout_sec = 0.3
    assert w.drain(10)
    job = led.get_job(rec.id)
    assert job.status is JobStatus.TIMEOUT and job.error_code == ErrorCode.TIMEOUT.value
    assert ("cancel", rec.id) in adapter.calls
    w.shutdown()


# ---- crash recovery -----------------------------------------------------------------------------
class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def crashed_job(led: Ledger, clock: Clock, external_id: str) -> str:
    """A job a previous (now dead) worker had started: running, lease about to expire."""
    rec = submit(led, fake_steps=1)
    led.claim(rec.id, "dead-worker", 5)
    led.set_external_id(rec.id, "dead-worker", external_id)
    clock.t += 10
    return rec.id


def test_dead_worker_job_is_reattached_not_resubmitted(fsettings: Settings, fdb: Database) -> None:
    clock = Clock()
    led = Ledger(fdb, fsettings, clock=clock)
    jid = crashed_job(led, clock, "prompt-42")
    w, _, adapter = make(fsettings, fdb, FakeAdapter(reattachable=True), ledger=led)
    assert w.drain(10)
    job = led.get_job(jid)
    assert job.status is JobStatus.COMPLETED and job.attempt == 2
    kinds = [c[0] for c in adapter.calls]
    assert "reattach" in kinds and "submit" not in kinds  # no duplicate external work
    assert "reattached" in [e["event"] for e in led.events(jid)]
    w.shutdown()


def test_dead_worker_job_is_resubmitted_when_reattach_is_impossible(fsettings: Settings, fdb: Database) -> None:
    clock = Clock()
    led = Ledger(fdb, fsettings, clock=clock)
    jid = crashed_job(led, clock, "prompt-42")
    w, _, adapter = make(fsettings, fdb, FakeAdapter(reattachable=False), ledger=led)
    assert w.drain(10)
    assert led.get_job(jid).status is JobStatus.COMPLETED
    assert [c[0] for c in adapter.calls].count("submit") == 1
    assert "resubmitting" in [e["event"] for e in led.events(jid)]
    assert len(led.list_assets(project_id=1)) == 1  # exactly one result, no duplicates
    w.shutdown()


def test_lost_lease_makes_the_old_worker_stand_down(fsettings: Settings, fdb: Database) -> None:
    """If another worker took the job over, the original must not write anything."""
    clock = Clock()
    led = Ledger(fdb, fsettings, clock=clock)
    w, _, _ = make(fsettings, fdb, ledger=led)
    rec = submit(led, fake_hang=True)
    w.tick()
    deadline = time.monotonic() + 5
    while led.get_job(rec.id).status is not JobStatus.RUNNING and time.monotonic() < deadline:
        time.sleep(0.01)
    fdb.conn.execute("UPDATE media_jobs SET lease_owner='thief' WHERE id=?", (rec.id,))
    time.sleep(0.3)  # heartbeat notices and the worker thread returns without touching the job
    job = led.get_job(rec.id)
    assert job.status is JobStatus.RUNNING and job.lease_owner == "thief"
    assert led.list_assets(project_id=1) == []
    w.shutdown()


# ---- scheduling in the real loop --------------------------------------------------------------
def test_heavy_jobs_never_overlap(fsettings: Settings, fdb: Database) -> None:
    active, peak, lock = 0, 0, threading.Lock()

    class Probing(FakeAdapter):
        def submit(self, job, ctx):  # noqa: ANN001, ANN201
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            return super().submit(job, ctx)

        def fetch(self, external_id, job, ctx):  # noqa: ANN001, ANN201
            nonlocal active
            with lock:
                active -= 1
            return super().fetch(external_id, job, ctx)

    w, led, _ = make(fsettings, fdb, Probing(heavy=True))
    for i in range(4):
        submit(led, model="fake-heavy", fake_steps=3, _seed=i)
    assert w.drain(20)
    assert peak == 1
    assert len(led.list_assets(project_id=1)) == 4
    w.shutdown()


def test_light_jobs_run_beside_a_heavy_one(fsettings: Settings, fdb: Database) -> None:
    w, led, _ = make(fsettings, fdb)
    heavy = submit(led, model="fake-heavy", fake_steps=30, _seed=1)
    light = submit(led, model="fake-video", _seed=2)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        w.tick()
        if led.get_job(light.id).status is JobStatus.COMPLETED:
            break
        time.sleep(0.01)
    assert led.get_job(light.id).status is JobStatus.COMPLETED
    assert w.drain(20) and led.get_job(heavy.id).status is JobStatus.COMPLETED
    w.shutdown()


def test_waiting_reason_is_visible_on_the_queued_job(fsettings: Settings, fdb: Database) -> None:
    w, led, _ = make(fsettings, fdb)
    first = submit(led, model="fake-heavy", fake_hang=True, _seed=1)
    second = submit(led, model="fake-heavy", _seed=2)
    w.tick()
    deadline = time.monotonic() + 5
    while led.get_job(first.id).status is not JobStatus.RUNNING and time.monotonic() < deadline:
        time.sleep(0.01)
    w.tick()
    assert led.get_job(second.id).status is JobStatus.QUEUED
    assert led.get_job(second.id).progress_msg == "wait.heavy_busy"
    led.request_cancel(first.id)
    assert w.drain(10)
    assert led.get_job(second.id).status is JobStatus.COMPLETED
    w.shutdown()


def test_graceful_shutdown_releases_running_jobs_without_spending_an_attempt(
    fsettings: Settings, fdb: Database
) -> None:
    w, led, _ = make(fsettings, fdb)
    rec = submit(led, fake_hang=True)
    w.tick()
    deadline = time.monotonic() + 5
    while led.get_job(rec.id).status is not JobStatus.RUNNING and time.monotonic() < deadline:
        time.sleep(0.01)
    w.shutdown()
    job = led.get_job(rec.id)
    assert job.status is JobStatus.QUEUED and job.attempt == 0 and job.lease_owner is None
    assert job.external_id is not None  # kept for re-attach


# ---- LLM residency policy ---------------------------------------------------------------------
def test_ask_policy_holds_heavy_job_until_llm_is_unloaded(fsettings: Settings, fdb: Database) -> None:
    s = replace(fsettings, runtime={**fsettings.runtime, "llm_unload_policy": "ask"})
    probe = StubProbe(["gemma4:31b"])
    w, led, _ = make(s, fdb, probes=[probe], ledger=Ledger(fdb, s))
    rec = submit(led, model="fake-heavy")
    w.tick()
    time.sleep(0.05)
    job = led.get_job(rec.id)
    assert job.status is JobStatus.QUEUED and job.progress_msg == "wait.llm_resident"
    assert probe.unloaded == []  # 'ask' never unloads on its own
    probe.unload(["gemma4:31b"])  # the operator does it (storyagent ops llm-unload)
    assert w.drain(10) and led.get_job(rec.id).status is JobStatus.COMPLETED
    w.shutdown()


def test_auto_policy_unloads_before_a_heavy_job_and_never_for_light(fsettings: Settings, fdb: Database) -> None:
    s = replace(fsettings, runtime={**fsettings.runtime, "llm_unload_policy": "auto"})
    probe = StubProbe(["gemma4:31b"])
    w, led, _ = make(s, fdb, probes=[probe], ledger=Ledger(fdb, s))
    light = submit(led, model="fake-video", _seed=1)
    assert w.drain(10) and probe.unloaded == []
    heavy = submit(led, model="fake-heavy", _seed=2)
    assert w.drain(10)
    assert probe.unloaded == ["gemma4:31b"]
    assert "llm_unloaded" in [e["event"] for e in led.events(heavy.id)]
    assert light and led.get_job(heavy.id).status is JobStatus.COMPLETED
    w.shutdown()


def test_never_policy_leaves_llms_alone(fsettings: Settings, fdb: Database) -> None:
    probe = StubProbe(["gemma4:31b"])
    w, led, _ = make(fsettings, fdb, probes=[probe])
    submit(led, model="fake-heavy")
    assert w.drain(10) and probe.unloaded == []
    w.shutdown()


# ---- compliance -------------------------------------------------------------------------------
def test_restricted_licence_needs_project_ack_when_compliance_is_on(fsettings: Settings, fdb: Database) -> None:
    w, led, _ = make(fsettings, fdb)
    blocked = submit(led, model="fake-restricted", _seed=1)
    assert w.drain(10)
    j = led.get_job(blocked.id)
    assert j.status is JobStatus.FAILED_VALIDATION and j.error_code == ErrorCode.LICENSE_ACK_REQUIRED.value

    fdb.conn.execute("UPDATE projects SET settings=? WHERE id=1", (json.dumps({"allow_restricted_licenses": True}),))
    allowed = submit(led, model="fake-restricted", _seed=2)
    assert w.drain(10) and led.get_job(allowed.id).status is JobStatus.COMPLETED
    w.shutdown()


def test_compliance_can_be_switched_off_globally_by_env(fsettings: Settings, fdb: Database, tmp_path: Path) -> None:
    from storyagent.config import load_settings

    off = load_settings({"STORY_AGENT_HOME": str(fsettings.home), "STORY_AGENT_COMPLIANCE": "off"})
    w, led, _ = make(off, fdb, ledger=Ledger(fdb, off))
    rec = submit(led, model="fake-restricted")
    assert w.drain(10) and led.get_job(rec.id).status is JobStatus.COMPLETED
    [asset] = led.list_assets(project_id=1)
    assert asset.provenance["aigc"] is True  # provenance is recorded regardless of the switch
    assert asset.provenance["compliance"]["enabled"] is False
    w.shutdown()


def test_compliance_can_be_switched_off_per_project(fsettings: Settings, fdb: Database) -> None:
    fdb.conn.execute("UPDATE projects SET settings=? WHERE id=1", (json.dumps({"compliance": {"enabled": False}}),))
    w, led, _ = make(fsettings, fdb)
    rec = submit(led, model="fake-restricted")
    assert w.drain(10) and led.get_job(rec.id).status is JobStatus.COMPLETED
    w.shutdown()


def test_voice_clone_requires_consent(fsettings: Settings, fdb: Database) -> None:
    w, led, _ = make(fsettings, fdb)
    clone = {"audio": "ref.wav", "text": "hi"}
    no = submit(led, model="fake-tts", kind=MediaKind.AUDIO_TTS, voice_ref=clone, _seed=1)
    yes = submit(led, model="fake-tts", kind=MediaKind.AUDIO_TTS, voice_ref={**clone, "consent": True}, _seed=2)
    plain = submit(led, model="fake-tts", kind=MediaKind.AUDIO_TTS, text="no clone", _seed=3)
    assert w.drain(10)
    assert led.get_job(no.id).error_code == ErrorCode.CONSENT_REQUIRED.value
    assert led.get_job(yes.id).status is JobStatus.COMPLETED
    assert led.get_job(plain.id).status is JobStatus.COMPLETED
    w.shutdown()


def test_project_param_override_is_frozen_into_the_job(fsettings: Settings, fdb: Database) -> None:
    ps = {"models": {"fake-video": {"width": 321}}}
    w, led, _ = make(fsettings, fdb)
    rec = submit(led, _project_settings=ps, _seed=1)
    assert rec.params["width"] == 321
    assert led.get_job(rec.id).tier is QualityTier.FINAL
    w.shutdown()
