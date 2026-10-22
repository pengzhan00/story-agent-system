"""The job + asset ledger: the single source of truth for what ran and what it produced.

All writes go through ``Database.transaction`` (BEGIN IMMEDIATE), so claims, completions and
candidate adoption are atomic across the API, the CLI and any number of worker processes.
Disk is only a cache that is verified against this ledger, never the other way round.
"""
from __future__ import annotations

import random
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from storyagent.config import ConfigError, Settings
from storyagent.db import Database
from storyagent.domain import JobStatus
from storyagent.domain.errors import ErrorCode
from storyagent.pipelines import project_settings_for
from storyagent.util import digest, dumps, iso_at, new_id

from .contracts import AssetRecord, JobRecord, MediaJob
from .params import resolve_params


class LedgerError(RuntimeError):
    pass


@dataclass(frozen=True)
class NewAsset:
    """An artifact that has already been stored under the data home (path is relative)."""

    kind: str
    slot: str
    path: str
    sha256: str = ""
    bytes: int = 0
    duration_sec: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)


def _parse_iso(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class Ledger:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock
        self._rng = rng or random.SystemRandom()

    # -- helpers ------------------------------------------------------------------------------
    def _now(self) -> str:
        return iso_at(self.clock())

    def _later(self, seconds: float) -> str:
        return iso_at(self.clock() + seconds)

    def add_event(self, job_id: str, event: str, level: str = "info", **data: Any) -> None:
        self.db.conn.execute(
            "INSERT INTO job_events(job_id, ts, level, event, data) VALUES (?,?,?,?,?)",
            (job_id, self._now(), level, event, dumps(data)),
        )

    def events(self, job_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        import json

        rows = self.db.conn.execute(
            "SELECT id, ts, level, event, data FROM job_events WHERE job_id=? AND id>? ORDER BY id",
            (job_id, after_id),
        ).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def last_event_id(self) -> int:
        return int(self.db.conn.execute("SELECT COALESCE(MAX(id),0) FROM job_events").fetchone()[0])

    def touched_jobs(self, after_event_id: int, *, project_id: int | None = None) -> tuple[int, list[JobRecord]]:
        """Jobs that logged an event after ``after_event_id`` (the SSE feed). Returns (new high-water mark, jobs)."""
        high = self.last_event_id()
        if high <= after_event_id:
            return after_event_id, []
        sql = (
            "SELECT j.* FROM media_jobs j WHERE j.id IN "
            "(SELECT job_id FROM job_events WHERE id>? AND id<=?)"
        )
        args: list[Any] = [after_event_id, high]
        if project_id is not None:
            sql += " AND j.project_id=?"
            args.append(project_id)
        rows = self.db.conn.execute(sql + " ORDER BY j.created_at, j.rowid", args).fetchall()
        return high, [JobRecord.from_row(r) for r in rows]

    # -- cost / ETA ---------------------------------------------------------------------------
    def estimate_seconds(self, model_ref: str, tier: str) -> int:
        """Median wall time of the last completed jobs for this model+tier (0 = no history)."""
        rows = self.db.conn.execute(
            """SELECT actual_seconds FROM media_jobs
               WHERE status='completed' AND model_ref=? AND tier=? AND actual_seconds IS NOT NULL
               ORDER BY finished_at DESC LIMIT 10""",
            (model_ref, tier),
        ).fetchall()
        return int(statistics.median(r[0] for r in rows)) if rows else 0

    def settings_for(self, project_id: int | None) -> Settings:
        """The settings the project runs with: its pipeline plan's models and options (global ones for a project that
        predates plans, and for jobs outside any project)."""
        return project_settings_for(self.settings, self.db, project_id)

    def frame_of(self, project_id: int | None) -> tuple[int, int] | None:
        """(width, height) of the project's delivery profile: the one direction every picture of it follows."""
        row = self.db.conn.execute("SELECT delivery_profile FROM projects WHERE id=?", (project_id,)).fetchone() \
            if project_id else None
        profile = self.settings_for(project_id).delivery_profile((row["delivery_profile"] if row else "") or None)
        w, h = int(profile.get("width", 0)), int(profile.get("height", 0))
        return (w, h) if w and h else None

    # -- enqueue ------------------------------------------------------------------------------
    def enqueue(
        self, job: MediaJob, project_settings: Mapping[str, Any] | None = None
    ) -> tuple[JobRecord, bool]:
        """Create a queued job. Returns (record, created); an identical active request returns the
        existing job with ``created=False`` (idempotent double-click / retry-safe)."""
        cfg = self.settings_for(job.project_id)
        model_ref = job.model_ref or cfg.default_model(job.kind.value).ref
        model = cfg.model(model_ref)
        if model.kind != job.kind.value:
            raise ConfigError(f"model {model_ref!r} is {model.kind!r}, cannot run {job.kind.value!r}")
        params = resolve_params(
            cfg, model_ref, job.tier,
            project_settings=project_settings, job_params=job.params, frame=self.frame_of(job.project_id),
        )
        key = job.idempotency_key or digest({
            "p": job.project_id, "s": job.shot_id, "spec": [job.spec_id, job.spec_version],
            "k": job.kind.value, "slot": job.slot, "m": model_ref, "t": job.tier.value,
            "in": job.inputs, "pa": params, "seed": job.seed, "dep": job.depends_on, "pl": job.pipeline_id,
        })
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM media_jobs WHERE idempotency_key=? AND status IN ('queued','running')",
                (key,),
            ).fetchone()
            if existing:
                return JobRecord.from_row(existing), False
            jid = new_id()
            seed = job.seed if job.seed is not None else self._rng.randrange(2**31)
            conn.execute(
                """INSERT INTO media_jobs(id, project_id, shot_id, spec_id, spec_version, kind, provider,
                       model_ref, tier, slot, status, priority, inputs, params, seed, idempotency_key,
                       heavy, est_memory_gib, est_seconds, license_tag, commercial_ok, max_attempts,
                       created_at, depends_on, pipeline_id, pipeline_step)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    jid, job.project_id, job.shot_id, job.spec_id, job.spec_version, job.kind.value,
                    model.provider, model_ref, job.tier.value, job.slot, job.priority,
                    dumps(dict(job.inputs)), dumps(params), seed, key, int(model.heavy),
                    model.est_memory_gib, self.estimate_seconds(model_ref, job.tier.value),
                    model.license_tag, int(model.commercial_ok),
                    job.max_attempts or int(self.settings.runtime.get("max_attempts", 2)),
                    self._now(), job.depends_on, job.pipeline_id, job.pipeline_step,
                ),
            )
            self.add_event(jid, "enqueued", model=model_ref, tier=job.tier.value)
            return self.get_job(jid), True

    # -- reads --------------------------------------------------------------------------------
    def get_job(self, job_id: str) -> JobRecord:
        row = self.db.conn.execute("SELECT * FROM media_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown job {job_id}")
        return JobRecord.from_row(row)

    def list_jobs(
        self,
        *,
        project_id: int | None = None,
        shot_id: int | None = None,
        spec_id: str | None = None,
        status: Sequence[JobStatus | str] | None = None,
        kinds: Sequence[str] | None = None,
        standalone: bool = False,
        pipeline_id: str | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        where: list[str] = ["1=1"]
        args: list[Any] = []
        if standalone:
            where.append("project_id IS NULL")
        if pipeline_id is not None:
            where.append("pipeline_id=?")
            args.append(pipeline_id)
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args.extend(kinds)
        if spec_id is not None:
            where.append("spec_id=?")
            args.append(spec_id)
        if project_id is not None:
            where.append("project_id=?")
            args.append(project_id)
        if shot_id is not None:
            where.append("shot_id=?")
            args.append(shot_id)
        if status:
            where.append(f"status IN ({','.join('?' * len(status))})")
            args.extend(JobStatus(st).value for st in status)
        rows = self.db.conn.execute(
            f"SELECT * FROM media_jobs WHERE {' AND '.join(where)} ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [JobRecord.from_row(r) for r in rows]

    def queued(self, limit: int = 50) -> list[JobRecord]:
        rows = self.db.conn.execute(
            "SELECT * FROM media_jobs WHERE status='queued' AND (depends_on IS NULL OR depends_on IN "
            "(SELECT id FROM media_jobs WHERE status='completed')) ORDER BY priority, created_at, rowid LIMIT ?",
            (limit,),
        ).fetchall()
        return [JobRecord.from_row(r) for r in rows]

    def running(self) -> list[JobRecord]:
        rows = self.db.conn.execute("SELECT * FROM media_jobs WHERE status='running'").fetchall()
        return [JobRecord.from_row(r) for r in rows]

    # -- execution lifecycle ------------------------------------------------------------------
    def claim(self, job_id: str, owner: str, lease_seconds: float) -> JobRecord | None:
        """Atomically move queued -> running for ``owner``. ``None`` if someone else got it."""
        with self.db.transaction() as conn:
            cur = conn.execute(
                """UPDATE media_jobs SET status='running', lease_owner=?, lease_expires_at=?,
                       attempt=attempt+1, started_at=?, error_code=NULL, error_message=NULL
                   WHERE id=? AND status='queued' AND cancel_requested=0""",
                (owner, self._later(lease_seconds), self._now(), job_id),
            )
            if cur.rowcount != 1:
                return None
            self.add_event(job_id, "claimed", owner=owner)
            return self.get_job(job_id)

    def heartbeat(
        self, job_id: str, owner: str, lease_seconds: float,
        progress: float | None = None, message: str | None = None,
    ) -> tuple[bool, bool]:
        """Extend the lease. Returns (still_owner, cancel_requested)."""
        sets: list[str] = ["lease_expires_at=?"]
        args: list[Any] = [self._later(lease_seconds)]
        if progress is not None:
            sets.append("progress=?")
            args.append(min(max(progress, 0.0), 1.0))
        if message is not None:
            sets.append("progress_msg=?")
            args.append(message)
        cur = self.db.conn.execute(
            f"UPDATE media_jobs SET {','.join(sets)} WHERE id=? AND status='running' AND lease_owner=?",
            (*args, job_id, owner),
        )
        if cur.rowcount != 1:
            return False, False
        row = self.db.conn.execute("SELECT cancel_requested FROM media_jobs WHERE id=?", (job_id,)).fetchone()
        return True, bool(row["cancel_requested"])

    def set_external_id(self, job_id: str, owner: str, external_id: str | None) -> None:
        self.db.conn.execute(
            "UPDATE media_jobs SET external_id=? WHERE id=? AND lease_owner=?", (external_id, job_id, owner)
        )

    def release(self, job_id: str, owner: str) -> bool:
        """Graceful shutdown: give a running job back without spending an attempt (keeps external_id)."""
        with self.db.transaction() as conn:
            cur = conn.execute(
                """UPDATE media_jobs SET status='queued', lease_owner=NULL, lease_expires_at=NULL,
                       attempt=MAX(attempt-1,0), progress_msg='' WHERE id=? AND status='running' AND lease_owner=?""",
                (job_id, owner),
            )
            if cur.rowcount:
                self.add_event(job_id, "released", owner=owner)
            return bool(cur.rowcount)

    def note_waiting(self, job_id: str, reason: str | None) -> None:
        """Record why a queued job is not starting (i18n key ``wait.<reason>``); only writes on change."""
        msg = f"wait.{reason}" if reason else ""
        self.db.conn.execute(
            "UPDATE media_jobs SET progress_msg=? WHERE id=? AND status='queued' AND progress_msg<>?",
            (msg, job_id, msg),
        )

    def requeue_expired(self) -> list[tuple[str, str]]:
        """Reclaim jobs whose worker stopped heartbeating. Returns [(job_id, outcome)] where outcome is
        ``requeued`` | ``failed`` | ``cancelled``. ``external_id`` is kept so the next worker can re-attach."""
        out: list[tuple[str, str]] = []
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT id, attempt, max_attempts, cancel_requested FROM media_jobs "
                "WHERE status='running' AND lease_expires_at < ?",
                (self._now(),),
            ).fetchall()
            for r in rows:
                if r["cancel_requested"]:
                    self._finish(conn, r["id"], ErrorCode.CANCELLED, "")
                    out.append((r["id"], "cancelled"))
                elif r["attempt"] < r["max_attempts"]:
                    conn.execute(
                        "UPDATE media_jobs SET status='queued', lease_owner=NULL, lease_expires_at=NULL, "
                        "progress_msg='' WHERE id=?",
                        (r["id"],),
                    )
                    self.add_event(r["id"], "lease_expired_requeued", "warning", attempt=r["attempt"])
                    out.append((r["id"], "requeued"))
                else:
                    self._finish(conn, r["id"], ErrorCode.WORKER_LOST, "")
                    out.append((r["id"], "failed"))
        return out

    def _finish(self, conn: Any, job_id: str, code: ErrorCode, message: str, **params: Any) -> None:
        now = self._now()
        row = conn.execute("SELECT started_at FROM media_jobs WHERE id=?", (job_id,)).fetchone()
        actual = _parse_iso(now) - _parse_iso(row["started_at"]) if row and row["started_at"] else None
        conn.execute(
            """UPDATE media_jobs SET status=?, error_code=?, error_message=?, finished_at=?,
                   actual_seconds=COALESCE(?, actual_seconds), lease_owner=NULL, lease_expires_at=NULL
               WHERE id=?""",
            (code.status.value, code.value, message, now, actual, job_id),
        )
        self.add_event(job_id, "failed", "error", code=code.value, message=message, params=params)
        self._cascade(conn, job_id)

    def _cascade(self, conn: Any, job_id: str) -> None:
        """A step of a pipeline that did not complete takes the steps waiting for it down with it."""
        for r in conn.execute("SELECT id FROM media_jobs WHERE depends_on=? AND status='queued'", (job_id,)).fetchall():
            self._finish(conn, r["id"], ErrorCode.DEPENDENCY_FAILED, "", upstream=job_id)

    def fail(self, job_id: str, code: ErrorCode, message: str = "", **params: Any) -> JobRecord:
        with self.db.transaction() as conn:
            cur = self.get_job(job_id)
            if cur.status.is_terminal:
                return cur  # first terminal state wins
            self._finish(conn, job_id, code, message, **params)
        return self.get_job(job_id)

    def request_cancel(self, job_id: str) -> JobStatus:
        """queued -> cancelled at once; running -> flagged (the worker stops it and records cancelled)."""
        with self.db.transaction() as conn:
            job = self.get_job(job_id)
            if job.status is JobStatus.QUEUED:
                self._finish(conn, job_id, ErrorCode.CANCELLED, "")
            elif job.status is JobStatus.RUNNING:
                conn.execute("UPDATE media_jobs SET cancel_requested=1 WHERE id=?", (job_id,))
                self.add_event(job_id, "cancel_requested")
        return self.get_job(job_id).status

    def _insert_asset(
        self, conn: Any, a: NewAsset, *, project_id: int | None, shot_id: int | None, job_id: str | None,
        spec_id: str | None = None, spec_version: int | None = None, auto_adopt: bool = True,
    ) -> str:
        """Register ``a`` as the next version of its (project, shot, kind, slot) group (caller owns the transaction)."""
        group = (project_id, shot_id or 0, a.kind, a.slot)
        where = "project_id IS ? AND COALESCE(shot_id,0)=? AND kind=? AND slot=?"
        version = conn.execute(
            f"SELECT COALESCE(MAX(version),0)+1 FROM media_assets WHERE {where}", group
        ).fetchone()[0]
        has_current = conn.execute(
            f"SELECT 1 FROM media_assets WHERE {where} AND is_current=1 AND trashed_at IS NULL", group
        ).fetchone()
        adopt = auto_adopt or not has_current
        if adopt and has_current:
            conn.execute(f"UPDATE media_assets SET is_current=0 WHERE {where} AND is_current=1", group)
        aid = new_id()
        conn.execute(
            """INSERT INTO media_assets(id, job_id, project_id, shot_id, spec_id, spec_version, kind,
                   slot, path, sha256, bytes, duration_sec, width, height, fps, has_audio, metadata,
                   provenance, version, is_current, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                aid, job_id, project_id, shot_id, spec_id, spec_version, a.kind, a.slot,
                a.path, a.sha256, a.bytes, a.duration_sec, a.width, a.height, a.fps,
                None if a.has_audio is None else int(a.has_audio), dumps(dict(a.metadata)),
                dumps(dict(a.provenance)), version, int(adopt), self._now(),
            ),
        )
        return aid

    def register_asset(
        self, a: NewAsset, *, project_id: int | None, shot_id: int | None = None, adopt: bool = True
    ) -> AssetRecord:
        """Register a file that did not come from a job (an upload, a library copy)."""
        with self.db.transaction() as conn:
            aid = self._insert_asset(conn, a, project_id=project_id, shot_id=shot_id, job_id=None, auto_adopt=adopt)
        return self.get_asset(aid)

    def complete(
        self,
        job_id: str,
        owner: str,
        assets: Sequence[NewAsset],
        *,
        peak_memory_gib: float | None = None,
    ) -> list[AssetRecord]:
        """Mark completed and register the artifacts as new versions (new current unless auto_adopt is off
        and a current asset already exists). 'Completed' is only ever recorded together with its assets."""
        if not assets:
            raise LedgerError("a job cannot complete without assets")
        auto_adopt = bool(self.settings.runtime.get("auto_adopt", True))
        out: list[str] = []
        with self.db.transaction() as conn:
            job = self.get_job(job_id)
            if job.status is not JobStatus.RUNNING or job.lease_owner != owner:
                raise LedgerError(f"job {job_id} is not running under {owner}")
            seen: set[tuple[str, str]] = set()
            for a in assets:
                if (a.kind, a.slot) in seen:
                    raise LedgerError(f"duplicate asset kind/slot in one job: {a.kind}/{a.slot}")
                seen.add((a.kind, a.slot))
                out.append(self._insert_asset(
                    conn, a, project_id=job.project_id, shot_id=job.shot_id, job_id=job_id, spec_id=job.spec_id,
                    spec_version=job.spec_version, auto_adopt=auto_adopt,
                ))
            now = self._now()
            actual = _parse_iso(now) - _parse_iso(job.started_at) if job.started_at else None
            conn.execute(
                """UPDATE media_jobs SET status='completed', progress=1, finished_at=?, actual_seconds=?,
                       peak_memory_gib=?, lease_owner=NULL, lease_expires_at=NULL, error_code=NULL,
                       error_message=NULL WHERE id=?""",
                (now, actual, peak_memory_gib, job_id),
            )
            self.add_event(job_id, "completed", assets=len(out))
        return [self.get_asset(i) for i in out]

    def retry(self, job_id: str, *, new_seed: bool = False) -> JobRecord:
        """New queued job with the same frozen snapshot, linked via ``parent_job_id``."""
        with self.db.transaction() as conn:
            old = self.get_job(job_id)
            if not old.status.is_terminal:
                raise LedgerError(f"job {job_id} is still {old.status.value}")
            jid = new_id()
            seed = self._rng.randrange(2**31) if new_seed else old.seed
            conn.execute(
                """INSERT INTO media_jobs(id, project_id, shot_id, spec_id, spec_version, kind, provider,
                       model_ref, tier, slot, status, priority, inputs, params, seed, idempotency_key, heavy,
                       est_memory_gib, est_seconds, license_tag, commercial_ok, max_attempts, parent_job_id,
                       created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    jid, old.project_id, old.shot_id, old.spec_id, old.spec_version, old.kind.value,
                    old.provider, old.model_ref, old.tier.value, old.slot, old.priority, dumps(old.inputs),
                    dumps(old.params), seed, "" if new_seed else old.idempotency_key, int(old.heavy),
                    old.est_memory_gib, self.estimate_seconds(old.model_ref, old.tier.value), old.license_tag,
                    int(old.commercial_ok), old.max_attempts, old.id, self._now(),
                ),
            )
            self.add_event(jid, "enqueued", retry_of=old.id, new_seed=new_seed)
            return self.get_job(jid)

    # -- assets -------------------------------------------------------------------------------
    def get_asset(self, asset_id: str) -> AssetRecord:
        row = self.db.conn.execute("SELECT * FROM media_assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown asset {asset_id}")
        return AssetRecord.from_row(row)

    def list_assets(
        self,
        *,
        project_id: int | None = None,
        shot_id: int | None = None,
        kind: str | None = None,
        slot: str | None = None,
        spec_id: str | None = None,
        job_id: str | None = None,
        current_only: bool = False,
        include_trashed: bool = False,
        limit: int = 200,
        kinds: Sequence[str] | None = None,
        standalone: bool = False,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[AssetRecord]:
        where: list[str] = ["1=1"]
        args: list[Any] = []
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args.extend(kinds)
        if standalone:
            where.append("project_id IS NULL")
        for col, val in (("project_id", project_id), ("shot_id", shot_id), ("kind", kind), ("slot", slot),
                         ("spec_id", spec_id), ("job_id", job_id)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        if current_only:
            where.append("is_current=1")
        if not include_trashed:
            where.append("trashed_at IS NULL")
        order = "created_at DESC, rowid DESC" if newest_first else "version DESC, rowid DESC"
        rows = self.db.conn.execute(
            f"SELECT * FROM media_assets WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?",
            (*args, limit, max(offset, 0)),
        ).fetchall()
        return [AssetRecord.from_row(r) for r in rows]

    def adopt(self, asset_id: str) -> AssetRecord:
        """Make a candidate the current asset of its slot (the previous current becomes a candidate)."""
        with self.db.transaction() as conn:
            a = self.get_asset(asset_id)
            if a.trashed_at:
                raise LedgerError(f"asset {asset_id} is in the trash")
            conn.execute(
                "UPDATE media_assets SET is_current=0 WHERE project_id IS ? AND COALESCE(shot_id,0)=? "
                "AND kind=? AND slot=? AND is_current=1",
                (a.project_id, a.shot_id or 0, a.kind, a.slot),
            )
            conn.execute("UPDATE media_assets SET is_current=1 WHERE id=?", (asset_id,))
        return self.get_asset(asset_id)

    def trash_asset(self, asset_id: str) -> AssetRecord:
        """Soft delete (files stay on disk until a purge). A trashed current asset leaves its slot empty."""
        self.db.conn.execute(
            "UPDATE media_assets SET trashed_at=?, is_current=0 WHERE id=?", (self._now(), asset_id)
        )
        return self.get_asset(asset_id)
