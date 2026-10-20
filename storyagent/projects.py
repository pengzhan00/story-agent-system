"""Project records: the one place that creates and reads ``projects`` rows (CLI and API share it)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from storyagent.config import ConfigError, Settings
from storyagent.db import Database
from storyagent.domain import slugify

_PATCHABLE = ("name", "description", "genre", "content_language", "delivery_profile", "aspect_ratio")
_SETTINGS_KEYS = ("compliance", "models")


class ProjectNotFound(LookupError):
    pass


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

    def list(self) -> list[Project]:
        return [Project.from_row(r) for r in self.db.conn.execute("SELECT * FROM projects ORDER BY id")]

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
            for ref in current.get("models", {}):
                self.settings.model(ref)  # unknown model ref -> ConfigError
            conn.execute(
                "UPDATE projects SET settings=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (json.dumps(current, ensure_ascii=False), project_id),
            )
        return self.get(project_id)
