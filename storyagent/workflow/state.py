"""Workflow bookkeeping in SQLite: stage states (resume / stale), small shared results, and the run queue."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from storyagent.db import Database
from storyagent.util import iso_at, loads, new_id, now_iso

STAGES = ("analyze", "cast", "script", "scenes", "music", "shotplan", "specs", "produce", "compose")
# What a plain "resume" runs: the story and its editable specs. Producing media and assembling the episode spend
# GPU hours, so they only run when asked for.
DEFAULT_STAGES = STAGES[:7]
# Stages that belong to the whole series (kept once); every other stage is kept per episode.
SERIES_STAGES = ("analyze", "cast")
# What becomes stale when a stage is (re)run.
DOWNSTREAM: dict[str, tuple[str, ...]] = {
    "analyze": ("cast", "script", "scenes", "music", "shotplan", "specs", "produce", "compose"),
    "cast": ("script", "scenes", "shotplan", "specs", "produce", "compose"),
    "script": ("scenes", "music", "shotplan", "specs", "produce", "compose"),
    "scenes": ("shotplan", "specs", "produce", "compose"),
    "music": ("specs", "produce", "compose"),
    "shotplan": ("specs", "produce", "compose"),
    "specs": ("produce", "compose"),
    "produce": ("compose",),
    "compose": (),
}


class WorkflowStateError(ValueError):
    pass


MAX_EPISODES_PER_RUN = 500


def parse_episodes(value: Any) -> list[int]:
    """``[1, 2, 5]``, ``"1-10"``, ``"1,3,5-7"`` or ``3`` -> sorted episode numbers (each >= 1)."""
    if isinstance(value, int):
        value = [value]
    parts: list[Any] = value if isinstance(value, list) else str(value).replace(" ", "").split(",")
    out: set[int] = set()
    try:
        for part in parts:
            text = str(part)
            lo, _, hi = text.partition("-")
            out.update(range(int(lo), int(hi or lo) + 1))
    except ValueError:
        raise WorkflowStateError(f"bad episode list {value!r}") from None
    if not out or min(out) < 1 or len(out) > MAX_EPISODES_PER_RUN:
        raise WorkflowStateError(f"episodes must be 1..{MAX_EPISODES_PER_RUN} numbers, each >= 1")
    return sorted(out)


def _ts(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


class WorkflowState:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- stages -------------------------------------------------------------------------------
    def scope(self, project_id: int, stage: str, episode: int = 1) -> tuple[int, int]:
        """(season, episode) a stage's state is kept under: (0, 0) for the whole series, else the episode's."""
        if stage in SERIES_STAGES:
            return 0, 0
        row = self.db.conn.execute(
            "SELECT season FROM episodes WHERE project_id=? AND number=?", (project_id, episode)).fetchone()
        return (int(row["season"]) if row else 1), episode

    def stages(self, project_id: int, episode: int = 1) -> list[dict[str, Any]]:
        """Every stage with its state as seen from ``episode`` (series stages are shared by all episodes)."""
        rows = {(r["stage"], r["season"], r["episode"]): r for r in self.db.conn.execute(
            "SELECT * FROM workflow_stages WHERE project_id=?", (project_id,)).fetchall()}
        out = []
        for name in STAGES:
            r = rows.get((name, *self.scope(project_id, name, episode)))
            out.append({
                "stage": name, "status": r["status"] if r else "pending",
                "started_at": r["started_at"] if r else None, "finished_at": r["finished_at"] if r else None,
                "error_code": r["error_code"] if r else None, "error_message": r["error_message"] if r else None,
                "summary": loads(r["summary"], {}) if r else {}, "attempts": r["attempts"] if r else 0,
            })
        return out

    def set_stage(
        self, project_id: int, stage: str, status: str, *, summary: dict[str, Any] | None = None,
        error_code: str | None = None, error_message: str | None = None, episode: int = 1,
    ) -> None:
        if stage not in STAGES:
            raise WorkflowStateError(f"unknown stage {stage!r}")
        now = now_iso()
        season, ep = self.scope(project_id, stage, episode)
        key = (project_id, stage, season, ep)
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO workflow_stages(project_id, stage, season, episode) VALUES (?,?,?,?)", key)
            if status == "running":
                conn.execute(
                    "UPDATE workflow_stages SET status='running', started_at=?, finished_at=NULL, error_code=NULL,"
                    " error_message=NULL, attempts=attempts+1"
                    " WHERE project_id=? AND stage=? AND season=? AND episode=?",
                    (now, *key))
            else:
                conn.execute(
                    "UPDATE workflow_stages SET status=?, finished_at=?, summary=COALESCE(?, summary), error_code=?,"
                    " error_message=? WHERE project_id=? AND stage=? AND season=? AND episode=?",
                    (status, now, json.dumps(summary, ensure_ascii=False) if summary is not None else None,
                     error_code, error_message, *key))
            if status == "completed":
                # a series stage outdates the later stages of every episode, an episode stage only its own
                where, extra = ("", ()) if stage in SERIES_STAGES else (" AND episode=?", (ep,))
                for later in DOWNSTREAM[stage]:
                    conn.execute(
                        "UPDATE workflow_stages SET status='stale'"
                        " WHERE project_id=? AND stage=? AND status='completed'" + where, (project_id, later, *extra))

    def episodes_for(self, project_id: int, params: dict[str, Any] | None) -> list[int]:
        """The episodes a run covers: ``episodes`` ("1-10", "1,3" or a list), else ``episode``; with only a ``season``
        it is that season's episodes (a season that has none yet gets ``planned_episodes`` new ones after the last)."""
        params = params or {}
        if params.get("episodes") not in (None, "", []):
            return parse_episodes(params["episodes"])
        if params.get("season") and not params.get("episode"):
            season = int(params["season"])
            have = [int(r["number"]) for r in self.db.conn.execute(
                "SELECT number FROM episodes WHERE project_id=? AND season=? ORDER BY number", (project_id, season))]
            if have:
                return have
            row = self.db.conn.execute(
                "SELECT planned_episodes FROM seasons WHERE project_id=? AND number=?", (project_id, season)).fetchone()
            planned = int(row["planned_episodes"]) if row else 0
            last = self.db.conn.execute(
                "SELECT COALESCE(MAX(number), 0) n FROM episodes WHERE project_id=?", (project_id,)).fetchone()["n"]
            if planned > 0:
                return list(range(last + 1, last + 1 + planned))
            raise WorkflowStateError(f"season {season} has no episodes and no planned_episodes")
        return [int(params.get("episode") or 1)]

    def plan(
        self, project_id: int, requested: list[str] | None, params: dict[str, Any] | None = None,
    ) -> list[tuple[int, str]]:
        """What a run does, in order: (episode, stage) pairs. Series stages (analyze, cast) appear once, with episode 0.
        Without ``requested`` (or with ``params.resume``) each episode resumes where it stopped."""
        params = params or {}
        resume = requested is None or bool(params.get("resume"))
        out: list[tuple[int, str]] = []
        for ep in self.episodes_for(project_id, params):
            for stage in self.pending_stages(project_id, None if resume else requested, ep):
                if stage in SERIES_STAGES:
                    if (0, stage) not in out:
                        out.append((0, stage))
                else:
                    out.append((ep, stage))
        return out

    def pending_stages(self, project_id: int, requested: list[str] | None = None, episode: int = 1) -> list[str]:
        """``requested`` in pipeline order; ``None`` = resume: every story stage of ``episode`` that is not completed
        (stale included). Producing media and composing only run when requested."""
        if requested:
            return [s for s in STAGES if s in requested]
        done = {s["stage"] for s in self.stages(project_id, episode) if s["status"] == "completed"}
        return [s for s in DEFAULT_STAGES if s not in done]

    # -- data ---------------------------------------------------------------------------------
    def get_data(self, project_id: int, key: str, default: Any = None) -> Any:
        row = self.db.conn.execute(
            "SELECT value FROM workflow_data WHERE project_id=? AND key=?", (project_id, key)).fetchone()
        return json.loads(row["value"]) if row else default

    def put_data(self, project_id: int, key: str, value: Any) -> None:
        self.db.conn.execute(
            "INSERT INTO workflow_data(project_id, key, value, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(project_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (project_id, key, json.dumps(value, ensure_ascii=False), now_iso()))

    # -- runs (the queue) ---------------------------------------------------------------------
    @staticmethod
    def _run(row: Any) -> dict[str, Any]:
        d = dict(row)
        d["stages"] = loads(d["stages"], [])
        d["params"], d["progress"] = loads(d["params"], {}), loads(d["progress"], {})
        d["cancel_requested"] = bool(d["cancel_requested"])
        return d

    def enqueue(self, project_id: int, stages: list[str], params: dict[str, Any] | None = None) -> dict[str, Any]:
        bad = [s for s in stages if s not in STAGES]
        if bad or not stages:
            raise WorkflowStateError(f"unknown or empty stage list: {stages}")
        with self.db.transaction() as conn:
            active = conn.execute(
                "SELECT * FROM workflow_runs WHERE project_id=? AND status IN ('queued','running') ORDER BY created_at",
                (project_id,)).fetchone()
            if active is not None:
                raise WorkflowStateError(f"project {project_id} already has an active run ({active['id']})")
            rid = new_id()
            conn.execute(
                "INSERT INTO workflow_runs(id, project_id, stages, params) VALUES (?,?,?,?)",
                (rid, project_id, json.dumps(stages), json.dumps(params or {}, ensure_ascii=False)))
        return self.get_run(rid)

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.db.conn.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise WorkflowStateError(f"unknown run {run_id}")
        return self._run(row)

    def list_runs(self, project_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            "SELECT * FROM workflow_runs WHERE project_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (project_id, limit)).fetchall()
        return [self._run(r) for r in rows]

    def recent_active(self, project_id: int | None = None, finished_within_sec: float = 60.0) -> list[dict[str, Any]]:
        """Queued and running runs plus those that ended recently (the stream's last frame), oldest first."""
        cutoff = iso_at(_ts(now_iso()) - finished_within_sec)
        q = "SELECT * FROM workflow_runs WHERE (status IN ('queued','running') OR finished_at >= ?)"
        args: list[Any] = [cutoff]
        if project_id is not None:
            q += " AND project_id=?"
            args.append(project_id)
        rows = self.db.conn.execute(q + " ORDER BY created_at, rowid", args).fetchall()
        return [self._run(r) for r in rows]

    def claim_next(self, owner: str, lease_seconds: float) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM workflow_runs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE workflow_runs SET status='running', lease_owner=?, lease_expires_at=?, started_at=? WHERE id=?",
                (owner, iso_at(_ts(now_iso()) + lease_seconds), now_iso(), row["id"]))
        return self.get_run(row["id"])

    def heartbeat(
        self, run_id: str, owner: str, lease_seconds: float, progress: dict[str, Any] | None = None
    ) -> tuple[bool, bool]:
        """(still owner, cancel requested)."""
        cur = self.db.conn.execute(
            "UPDATE workflow_runs SET lease_expires_at=?, progress=COALESCE(?, progress) "
            "WHERE id=? AND lease_owner=? AND status='running'",
            (iso_at(_ts(now_iso()) + lease_seconds), json.dumps(progress, ensure_ascii=False) if progress else None,
             run_id, owner))
        if cur.rowcount == 0:
            return False, False
        row = self.db.conn.execute("SELECT cancel_requested FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
        return True, bool(row["cancel_requested"])

    def finish(
        self, run_id: str, status: str, *, error_code: str | None = None, error_message: str | None = None,
        progress: dict[str, Any] | None = None,
    ) -> None:
        self.db.conn.execute(
            "UPDATE workflow_runs SET status=?, error_code=?, error_message=?, finished_at=?, lease_owner=NULL,"
            " lease_expires_at=NULL, progress=COALESCE(?, progress) WHERE id=?",
            (status, error_code, error_message, now_iso(),
             json.dumps(progress, ensure_ascii=False) if progress else None, run_id))

    def request_cancel(self, run_id: str) -> str:
        with self.db.transaction() as conn:
            run = self.get_run(run_id)
            if run["status"] == "queued":
                conn.execute(
                    "UPDATE workflow_runs SET status='cancelled', finished_at=? WHERE id=?", (now_iso(), run_id))
            elif run["status"] == "running":
                conn.execute("UPDATE workflow_runs SET cancel_requested=1 WHERE id=?", (run_id,))
        return str(self.get_run(run_id)["status"])

    def requeue_expired(self) -> list[str]:
        """A worker that died mid-run: its run goes back to the queue (stages are idempotent, work resumes)."""
        now = now_iso()
        rows = self.db.conn.execute(
            "SELECT id FROM workflow_runs WHERE status='running' AND lease_expires_at < ?", (now,)).fetchall()
        for r in rows:
            self.db.conn.execute(
                "UPDATE workflow_runs SET status='queued', lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
                (r["id"],))
        return [r["id"] for r in rows]
