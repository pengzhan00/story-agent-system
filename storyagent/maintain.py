"""Maintenance tasks an operator can trigger from the web: prune orphaned shot specs/assets and old history logs."""
from __future__ import annotations

import time
from typing import Any

from storyagent.db import Database
from storyagent.util import iso_at

from .media.specs import SpecStore

# (table, time column): the history tables, newest-first by age.
HISTORY_TABLES: tuple[tuple[str, str], ...] = (
    ("audit_log", "at"),
    ("generation_logs", "created_at"),
    ("job_events", "ts"),
)


def prune_orphans(specs: SpecStore, project_id: int, *, dry_run: bool = False) -> dict[str, int]:
    """Drop shot.keyframe/shot.video specs whose shot no longer exists, and their assets."""
    return specs.prune_orphan_shots(project_id, dry_run=dry_run)


def prune_history(
    db: Database, *, days: int = 30, project_id: int | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """Delete rows older than ``days`` days from the history tables (audit log, LLM logs, job events). With
    ``project_id`` only that project's rows go (job events through the jobs they belong to)."""
    if days < 1:
        raise ValueError("days must be at least 1")
    cutoff = iso_at(time.time() - days * 86400)
    deleted: dict[str, int] = {}
    for table, col in HISTORY_TABLES:
        where: str = f"{col} < ?"
        args: list[Any] = [cutoff]
        if project_id is not None:
            where += (" AND job_id IN (SELECT id FROM media_jobs WHERE project_id=?)" if table == "job_events"
                      else " AND project_id=?")
            args.append(project_id)
        if dry_run:
            n = db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]
        else:
            n = db.conn.execute(f"DELETE FROM {table} WHERE {where}", args).rowcount
        deleted[table] = n
    if not dry_run:
        db.conn.commit()
    return {"days": days, "total": sum(deleted.values()), "deleted": deleted}
