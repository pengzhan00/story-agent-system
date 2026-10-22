"""The worker: turns queued ledger jobs into stored, verified assets.

One process can run many jobs (a heavy one plus a few light ones, per ``broker.can_start``); several
workers may share one database because every state change is an atomic ledger transaction.

Durability: a running job holds a time-limited lease that a heartbeat thread renews. If the process
dies, the lease expires, ``Ledger.requeue_expired`` hands the job to the next worker, which re-attaches
to the external work (e.g. a Phosphene queue entry) when the adapter supports it, or resubmits.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import socket
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from storyagent import content
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.domain.errors import ErrorCode
from storyagent.util import new_id

from . import compliance
from .assetrefs import RefError, Resolver
from .broker import CaffeinateGuard, select_next, system_available_gib
from .contracts import (
    AdapterError,
    ArtifactSpec,
    JobContext,
    JobRecord,
)
from .ledger import Ledger, NewAsset
from .llm_resident import ResidentLLMProbe, build_probes
from .params import validate_params
from .probe import probe
from .registry import AdapterRegistry

if TYPE_CHECKING:
    from storyagent.workflow import WorkflowEngine

log = logging.getLogger(__name__)

_MEDIA_EXT = {
    ".mp4", ".mov", ".mkv", ".webm", ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".png", ".jpg", ".jpeg", ".webp",
}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_TRANSIENT_POLL_FAILURES = 3


class Worker:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        registry: AdapterRegistry,
        *,
        ledger: Ledger | None = None,
        probes: Sequence[ResidentLLMProbe] | None = None,
        owner: str | None = None,
        max_threads: int | None = None,
        workflow: WorkflowEngine | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.registry = registry
        self.ledger = ledger or Ledger(db, settings)
        self.probes = list(probes) if probes is not None else build_probes(settings)
        self.resolver = Resolver(settings, self.ledger)
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}:{new_id()[:6]}"
        self._max_threads = max_threads or int(settings.runtime.get("max_light_jobs", 2)) + 2
        self._pool = ThreadPoolExecutor(max_workers=self._max_threads, thread_name_prefix="media-job")
        self._futures: dict[str, Future[None]] = {}
        self._workflow = workflow
        self._wf_max = int(settings.runtime.get("max_workflow_runs", 1))
        self._wf_pool = ThreadPoolExecutor(max_workers=max(self._wf_max, 1), thread_name_prefix="workflow-run")
        self._wf_futures: dict[str, Future[None]] = {}
        self._stop = threading.Event()
        self._lease = float(settings.runtime.get("lease_seconds", 60))
        self._beat = float(settings.runtime.get("heartbeat_seconds", 15))

    # -- scheduling ---------------------------------------------------------------------------
    def tick(self) -> int:
        """One scheduling pass. Returns the number of jobs started."""
        led = self.ledger
        led.requeue_expired()
        self._futures = {j: f for j, f in self._futures.items() if not f.done()}
        started = 0
        while len(self._futures) < self._max_threads:
            queued = led.queued()
            if not queued:
                break
            policy = self.settings.llm_unload_policy
            resident = self._resident() if policy == "ask" and any(j.heavy for j in queued) else []
            available = system_available_gib() if self.settings.runtime.get("check_system_memory", True) else None
            job, waiting = select_next(queued, led.running(), self.settings, resident_llms=resident,
                                       available_gib=available)
            for jid, reason in waiting.items():
                led.note_waiting(jid, reason.value)
            if job is None:
                break
            claimed = led.claim(job.id, self.owner, self._lease)
            if claimed is None:
                continue
            self._futures[claimed.id] = self._pool.submit(self._run, claimed)
            started += 1
        return started + self._tick_workflow()

    def _engine(self) -> WorkflowEngine:
        if self._workflow is None:
            from storyagent.workflow import WorkflowEngine  # deferred: the LLM layer is only built when a run exists

            self._workflow = WorkflowEngine(self.settings, self.db)
        return self._workflow

    def _tick_workflow(self) -> int:
        """Story-workflow runs (LLM stages) share the worker but not the media job slots."""
        from storyagent.workflow import WorkflowState

        state = WorkflowState(self.db)
        for run_id in state.requeue_expired():
            log.warning("workflow run %s lost its worker; requeued", run_id)
        self._wf_futures = {r: f for r, f in self._wf_futures.items() if not f.done()}
        started = 0
        while len(self._wf_futures) < self._wf_max:
            run = state.claim_next(self.owner, self._lease)
            if run is None:
                break
            self._wf_futures[run["id"]] = self._wf_pool.submit(
                self._engine().execute_run, run, self.owner, self._lease, self._beat)
            started += 1
        return started

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or self._stop
        interval = float(self.settings.runtime.get("poll_interval_seconds", 2))
        with CaffeinateGuard(bool(self.settings.runtime.get("caffeinate", True))):
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("scheduler pass failed")
                stop.wait(interval)
        self.shutdown()

    def shutdown(self) -> None:
        self._stop.set()
        self._pool.shutdown(wait=True)
        self._wf_pool.shutdown(wait=True)

    def drain(self, timeout: float = 30.0) -> bool:
        """Test/CLI helper: run until nothing is queued or running. False on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.tick()
            if not self.ledger.queued() and not self.ledger.running() and not self._futures \
                    and not self._wf_futures and not self._workflow_pending():
                return True
            time.sleep(0.01)
        return False

    def _workflow_pending(self) -> bool:
        row = self.db.conn.execute(
            "SELECT 1 FROM workflow_runs WHERE status IN ('queued','running') LIMIT 1").fetchone()
        return row is not None

    def _resident(self) -> list[str]:
        out: list[str] = []
        for p in self.probes:
            out.extend(p.resident())
        return out

    # -- one job ------------------------------------------------------------------------------
    def _project(self, project_id: int | None) -> tuple[str, dict[str, Any], str]:
        default = self.settings.content.default_rating
        if project_id is None:
            return "_unassigned", {}, default
        row = self.db.conn.execute("SELECT slug, settings, rating FROM projects WHERE id=?",
                                   (project_id,)).fetchone()
        if row is None:
            return "_unassigned", {}, default
        return row["slug"], json.loads(row["settings"] or "{}"), row["rating"]

    def _run(self, job: JobRecord) -> None:
        led, stop_hb = self.ledger, threading.Event()
        cancel_flag, lost = threading.Event(), threading.Event()
        beat = threading.Thread(
            target=self._heartbeat, args=(job.id, stop_hb, cancel_flag, lost), daemon=True, name=f"hb-{job.id[:6]}"
        )
        beat.start()
        work_dir = self.settings.home / "work" / job.id
        failed = False
        try:
            self._execute(job, work_dir, cancel_flag, lost)
        except AdapterError as e:
            failed = True
            if not lost.is_set():
                led.fail(job.id, e.code, e.message, **e.params)
        except Exception as e:
            failed = True
            log.exception("job %s crashed", job.id)
            led.add_event(job.id, "crash", "error", trace=traceback.format_exc(limit=8))
            if not lost.is_set():
                led.fail(job.id, ErrorCode.INTERNAL, str(e), detail=str(e))
        finally:
            stop_hb.set()
            beat.join(timeout=5)
            if failed:
                self._keep_tool_log(job.id, work_dir)
            shutil.rmtree(work_dir, ignore_errors=True)

    def _keep_tool_log(self, job_id: str, work_dir: Path) -> None:
        """The scratch directory is always removed; a failed job's tool log is the one thing worth keeping."""
        src = work_dir / "tool.log"
        if not src.is_file():
            return
        try:
            dest = self.settings.logs_dir / "jobs"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / f"{job_id}.log")
        except OSError:
            log.warning("could not keep the tool log of job %s", job_id)

    def _heartbeat(self, job_id: str, stop: threading.Event, cancel: threading.Event, lost: threading.Event) -> None:
        while not stop.wait(self._beat):
            owned, cancel_req = self.ledger.heartbeat(job_id, self.owner, self._lease)
            if not owned:
                lost.set()
                return
            if cancel_req:
                cancel.set()

    def _execute(self, job: JobRecord, work_dir: Path, cancel_flag: threading.Event, lost: threading.Event) -> None:
        led, s = self.ledger, self.settings
        adapter = self.registry.get(job.provider)
        if adapter is None:
            raise AdapterError(ErrorCode.MISSING_TOOL, f"no adapter for provider {job.provider}", tool=job.provider)
        slug, project_settings, project_rating = self._project(job.project_id)
        rating = content.job_rating(s, project_rating, job.model_ref)
        work_dir.mkdir(parents=True, exist_ok=True)

        def log_event(event: str, level: str = "info", **data: Any) -> None:
            led.add_event(job.id, event, level, **data)

        try:  # asset:/library: references become plain paths; adapters never see them
            inputs, params, resolved = self.resolver.resolve(job.inputs, job.params)
        except RefError as exc:
            raise AdapterError(ErrorCode.INVALID_INPUT, str(exc), detail=str(exc)) from exc
        if resolved:
            job = replace(job, inputs=inputs, params=params)
            log_event("refs_resolved", refs=resolved.assets, sha256=resolved.sha256)
        ctx = JobContext(s, job, work_dir, log_event, project_settings)
        caps = adapter.capabilities()

        # 1) gates: compliance, parameters, adapter-specific validation
        issues = compliance.check_job(s, job, project_settings) + compliance.check_remote(s, job, project_settings)
        notice = compliance.license_notice(s, job, project_settings)
        if notice:
            log_event("license_notice", "warning", **notice)
        issues += content.gate_issues(s, self.db, job, job.project_id, rating)
        p_issues, warnings = validate_params(job.params, caps.param_schema, strict=s.strict_params)
        issues += p_issues + adapter.validate(job, ctx)
        for w in warnings:
            log_event("param_warning", "warning", message=w)
        if issues:
            first = issues[0]
            raise AdapterError(first.code, first.message, **dict(first.params))

        # 2) optionally free memory held by resident LLMs
        if job.heavy and s.llm_unload_policy == "auto":
            for p in self.probes:
                gone = p.unload(p.resident())
                if gone:
                    log_event("llm_unloaded", backend=p.name, models=gone)

        # 3) start or re-attach
        ext = job.external_id
        if ext and caps.supports_reattach and adapter.reattach(ext, job, ctx):
            log_event("reattached", external_id=ext)
        else:
            if ext:
                log_event("resubmitting", "warning", previous=ext)
            ext = adapter.submit(job, ctx)
            led.set_external_id(job.id, self.owner, ext)
            log_event("submitted", external_id=ext)

        # 4) poll
        model = s.models.get(job.model_ref)
        timeout = float(model.timeout_sec) if model else 3600.0
        t0, failures = time.monotonic(), 0
        while True:
            if lost.is_set():
                return
            if self._stop.is_set():
                led.release(job.id, self.owner)
                return
            if cancel_flag.is_set():
                adapter.cancel(ext, job, ctx)
                raise AdapterError(ErrorCode.CANCELLED)
            if time.monotonic() - t0 > timeout:
                adapter.cancel(ext, job, ctx)
                raise AdapterError(ErrorCode.TIMEOUT, f"timeout after {timeout:.0f}s", seconds=int(timeout))
            try:
                prog = adapter.poll(ext, job, ctx)
                failures = 0
            except AdapterError as e:
                failures += 1
                if not e.retryable or failures >= _TRANSIENT_POLL_FAILURES:
                    raise
                log_event("poll_retry", "warning", code=e.code.value, attempt=failures)
                self._stop.wait(caps.poll_interval_sec)
                continue
            if prog.state == "failed":
                raise prog.error or AdapterError(ErrorCode.WORKFLOW_FAILED, "adapter reported failure")
            owned, cancel_req = led.heartbeat(job.id, self.owner, self._lease, prog.fraction, prog.message)
            if not owned:
                lost.set()
                return
            if cancel_req:
                cancel_flag.set()
            if prog.state == "succeeded":
                break
            self._stop.wait(caps.poll_interval_sec)

        # 5) fetch, verify, store, record
        artifacts = adapter.fetch(ext, job, ctx)
        assets = self._store(job, artifacts, ctx, slug, project_settings, rating)
        led.complete(job.id, self.owner, assets)

    # -- storing artifacts --------------------------------------------------------------------
    def _store(
        self, job: JobRecord, artifacts: Sequence[ArtifactSpec], ctx: JobContext, slug: str,
        project_settings: dict[str, Any], rating: str,
    ) -> list[NewAsset]:
        s = self.settings
        if not artifacts:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "adapter returned no artifacts")
        provenance = compliance.build_provenance(s, job, project_settings)
        out: list[NewAsset] = []
        warned_probe = False
        for art in artifacts:
            src = Path(art.path)
            if not src.is_file():
                raise AdapterError(ErrorCode.OUTPUT_MISSING, str(src), path=str(src))
            if src.stat().st_size == 0:
                raise AdapterError(ErrorCode.OUTPUT_INVALID, "empty file", path=str(src))
            kind = (art.kind or job.kind).value
            slot = art.slot or job.slot
            dest_dir = s.project_media_dir(slug, kind, job.shot_id, rating)
            dest_dir.mkdir(parents=True, exist_ok=True)
            name = f"{job.id}{'' if slot == job.slot else '.' + slot}{src.suffix.lower()}"
            dest = dest_dir / name
            if ctx.work_dir in src.parents:
                shutil.move(str(src), dest)
            else:
                shutil.copy2(src, dest)
            sha = hashlib.sha256()
            with dest.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    sha.update(chunk)
            meta: dict[str, Any] = dict(art.metadata)
            info = None
            if dest.suffix.lower() in _MEDIA_EXT:
                info = probe(s, dest)
                if info is None:
                    from .probe import find_tool

                    if find_tool(s, "ffprobe") is None:
                        if not warned_probe:
                            ctx.log("ffprobe_missing", "warning")
                            warned_probe = True
                    else:
                        dest.unlink(missing_ok=True)
                        raise AdapterError(ErrorCode.OUTPUT_INVALID, "unreadable media", path=str(dest))
                elif dest.suffix.lower() in _IMAGE_EXT and not (info.width and info.height):
                    # ffprobe names a file with a good signature and no picture (a truncated or wrong reply) a 0x0 image
                    dest.unlink(missing_ok=True)
                    raise AdapterError(ErrorCode.OUTPUT_INVALID, "image has no pixels", path=str(dest))
            out.append(
                NewAsset(
                    kind=kind, slot=slot, path=str(dest.relative_to(s.home)), sha256=sha.hexdigest(),
                    bytes=dest.stat().st_size,
                    duration_sec=info.duration_sec if info else None, width=info.width if info else None,
                    height=info.height if info else None, fps=info.fps if info else None,
                    has_audio=info.has_audio if info else None, metadata=meta, provenance=provenance,
                    rating=rating,
                )
            )
        return out


def run_once(worker: Worker, timeout: float = 600.0) -> bool:
    """Process everything currently queued, then return (``storyagent worker --once``)."""
    ok = worker.drain(timeout)
    worker.shutdown()
    return ok


def spawn(worker: Worker) -> tuple[threading.Thread, Callable[[], None]]:
    """Run the worker loop in a background thread (tests, embedded use). Returns (thread, stop)."""
    stop = threading.Event()
    t = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True, name="media-worker")
    t.start()
    return t, stop.set
