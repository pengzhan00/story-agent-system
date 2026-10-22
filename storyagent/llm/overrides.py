"""Language-model choices made on the screen (``llm_overrides``): per role, for every project or for one project."""
from __future__ import annotations

from dataclasses import dataclass

from storyagent.db import Database

GLOBAL = "global"


def project_scope(project_id: int) -> str:
    return f"project:{project_id}"


@dataclass(frozen=True)
class Override:
    scope: str
    role: str
    backend: str
    model: str


def get(db: Database, role: str, project_id: int | None = None) -> Override | None:
    """The project's choice, else the global one, else ``None`` (the role is whatever ``llm.toml`` says)."""
    scopes = ([project_scope(project_id)] if project_id is not None else []) + [GLOBAL]
    for scope in scopes:
        row = db.conn.execute(
            "SELECT scope, role, backend, model FROM llm_overrides WHERE scope=? AND role=?", (scope, role)).fetchone()
        if row:
            return Override(row["scope"], row["role"], row["backend"], row["model"])
    return None


def listing(db: Database, scope: str) -> dict[str, Override]:
    rows = db.conn.execute("SELECT scope, role, backend, model FROM llm_overrides WHERE scope=?", (scope,)).fetchall()
    return {r["role"]: Override(r["scope"], r["role"], r["backend"], r["model"]) for r in rows}


def put(db: Database, scope: str, role: str, backend: str, model: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO llm_overrides(scope, role, backend, model) VALUES (?,?,?,?) "
            "ON CONFLICT(scope, role) DO UPDATE SET backend=excluded.backend, model=excluded.model, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')", (scope, role, backend, model))


def remove(db: Database, scope: str, role: str) -> bool:
    with db.transaction() as conn:
        return conn.execute("DELETE FROM llm_overrides WHERE scope=? AND role=?", (scope, role)).rowcount > 0
