"""Project records: the one place that creates and reads ``projects`` rows (CLI and API share it)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from storyagent.compose.pacing import PacingError, parse_target
from storyagent.config import ConfigError, Settings
from storyagent.db import Database
from storyagent.domain import slugify

_PATCHABLE = ("name", "description", "genre", "content_language", "delivery_profile", "aspect_ratio")
_SETTINGS_KEYS = ("compliance", "models", "allow_restricted_licenses", "target_sec", "allow_remote")


class ProjectNotFound(LookupError):
    pass


class ProjectStateError(RuntimeError):
    """The requested status change does not fit the project's current state."""


@dataclass(frozen=True)
class Project:
    id: int
    slug: str
    name: str
    description: str
    genre: str
    content_language: str
    delivery_profile: str
    aspect_ratio: str
    status: str
    settings: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, r: Any) -> Project:
        return cls(
            id=r["id"], slug=r["slug"], name=r["name"], description=r["description"], genre=r["genre"],
            content_language=r["content_language"], delivery_profile=r["delivery_profile"],
            aspect_ratio=r["aspect_ratio"], status=r["status"], settings=json.loads(r["settings"] or "{}"),
            created_at=r["created_at"], updated_at=r["updated_at"],
        )


class ProjectStore:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db, self.settings = db, settings

    def create(self, name: str, *, genre: str = "", description: str = "", content_language: str = "") -> Project:
        base = slugify(name)
        with self.db.transaction() as conn:
            slug, n = base, 2
            while conn.execute("SELECT 1 FROM projects WHERE slug=?", (slug,)).fetchone():
                slug, n = f"{base}-{n}", n + 1
            cur = conn.execute(
                "INSERT INTO projects(slug, name, genre, description, content_language) VALUES (?,?,?,?,?)",
                (slug, name, genre, description, content_language or self.settings.locale),
            )
            assert cur.lastrowid is not None
            return self.get(cur.lastrowid)

    def get(self, project_id: int) -> Project:
        row = self.db.conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ProjectNotFound(str(project_id))
        return Project.from_row(row)

    def list(self, *, trashed: bool = False) -> list[Project]:
        """Live projects, or (``trashed=True``) the ones in the recycle bin."""
        rows = self.db.conn.execute(
            "SELECT * FROM projects WHERE (status = 'trashed') = ? ORDER BY id", (1 if trashed else 0,))
        return [Project.from_row(r) for r in rows]

    def trash(self, project_id: int) -> Project:
        """Soft delete: hidden from the list, nothing is removed (media stays on disk), reversible with ``restore``."""
        return self._set_status(project_id, "trashed", from_status=None)

    def restore(self, project_id: int) -> Project:
        return self._set_status(project_id, "draft", from_status="trashed")

    def _set_status(self, project_id: int, status: str, *, from_status: str | None) -> Project:
        with self.db.transaction() as conn:
            current = self.get(project_id)
            if from_status is not None and current.status != from_status:
                raise ProjectStateError(f"project {project_id} is not {from_status}")
            if status == "trashed":
                busy = conn.execute("SELECT 1 FROM workflow_runs WHERE project_id=? AND status IN ('queued','running')",
                                    (project_id,)).fetchone()
                if busy is not None:
                    raise ProjectStateError(f"project {project_id} has a run in progress; cancel it first")
            conn.execute("UPDATE projects SET status=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                         (status, project_id))
        return self.get(project_id)

    def update(self, project_id: int, **changes: Any) -> Project:
        unknown = set(changes) - set(_PATCHABLE)
        if unknown:
            raise ConfigError(f"cannot update {sorted(unknown)}")
        if changes:
            sets = ", ".join(f"{k}=?" for k in changes)
            with self.db.transaction() as conn:
                self.get(project_id)
                conn.execute(
                    f"UPDATE projects SET {sets}, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                    (*changes.values(), project_id),
                )
        return self.get(project_id)

    def set_settings(self, project_id: int, patch: Mapping[str, Any]) -> Project:
        """Merge ``patch`` into ``projects.settings`` (compliance / models.<ref> overrides). ``None`` removes a key."""
        unknown = set(patch) - set(_SETTINGS_KEYS)
        if unknown:
            raise ConfigError(f"unknown project setting groups {sorted(unknown)}; allowed: {list(_SETTINGS_KEYS)}")
        with self.db.transaction() as conn:
            current = dict(self.get(project_id).settings)
            for key, value in patch.items():
                if value is None:
                    current.pop(key, None)
                else:
                    current[key] = value
            self.settings.compliance_for(current)  # validates the compliance group
            try:
                parse_target(current.get("target_sec"))
            except PacingError as exc:
                raise ConfigError(str(exc)) from exc
            for ref in current.get("models", {}):
                self.settings.model(ref)  # unknown model ref -> ConfigError
            conn.execute(
                "UPDATE projects SET settings=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (json.dumps(current, ensure_ascii=False), project_id),
            )
        return self.get(project_id)
