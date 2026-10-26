"""Project records: the one place that creates and reads ``projects`` rows (CLI and API share it)."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from storyagent.brief import resolve_brief
from storyagent.compose.pacing import PacingError, parse_target
from storyagent.config import ConfigError, Settings
from storyagent.content import ContentError
from storyagent.db import Database
from storyagent.domain import slugify
from storyagent.pipelines import check_choices

_PATCHABLE = ("name", "description", "genre", "content_language", "delivery_profile", "aspect_ratio", "kind",
              "pipeline", "rating")
_SETTINGS_KEYS = ("compliance", "models", "allow_restricted_licenses", "target_sec", "allow_remote", "default_voice",
                  "video_share", "video_budget_hours", "edit", "pipeline_models", "video_with_sound")


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
    kind: str = ""
    pipeline: str = ""
    brief: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    rating: str = "general"

    @classmethod
    def from_row(cls, r: Any) -> Project:
        return cls(
            id=r["id"], slug=r["slug"], name=r["name"], description=r["description"], genre=r["genre"],
            content_language=r["content_language"], delivery_profile=r["delivery_profile"],
            aspect_ratio=r["aspect_ratio"], status=r["status"], settings=json.loads(r["settings"] or "{}"),
            kind=r["kind"], pipeline=r["pipeline"], brief=json.loads(r["brief"] or "{}"),
            created_at=r["created_at"], updated_at=r["updated_at"], rating=r["rating"],
        )


EDIT_FLAGS = ("title_card", "recap_card", "next_card")
EDIT_SECONDS = ("scene_fade_sec", "dissolve_sec", "j_cut_sec", "l_cut_sec", "title_sec", "recap_sec", "next_sec")


def _check_edit(edit: Any) -> None:
    """``settings.edit``: switches for the title / recap / next cards and the lengths and fade in seconds."""
    if not isinstance(edit, dict):
        raise ConfigError("edit must be an object")
    unknown = set(edit) - {*EDIT_FLAGS, *EDIT_SECONDS}
    if unknown:
        raise ConfigError(f"unknown edit options {sorted(unknown)}; allowed: {[*EDIT_FLAGS, *EDIT_SECONDS]}")
    for key in EDIT_FLAGS:
        if key in edit and not isinstance(edit[key], bool):
            raise ConfigError(f"edit.{key} must be true or false")
    for key in EDIT_SECONDS:
        if key in edit and not (isinstance(edit[key], int | float) and not isinstance(edit[key], bool)
                                and 0 <= edit[key] <= 30):
            raise ConfigError(f"edit.{key} must be a number of seconds between 0 and 30")


class ProjectStore:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db, self.settings = db, settings

    def _frame(self, profile: str) -> tuple[int, int]:
        p = self.settings.delivery_profile(profile or None)
        return int(p["width"]), int(p["height"])

    @staticmethod
    def _orientation(frame: tuple[int, int]) -> str:
        w, h = frame
        return "square" if w == h else "landscape" if w > h else "vertical"

    @staticmethod
    def _ratio(frame: tuple[int, int]) -> str:
        g = math.gcd(*frame)
        return f"{frame[0] // g}:{frame[1] // g}"

    def plan_of(self, pipeline: str, kind: str = "", profile: str = "") -> str:
        """The plan a new project runs on: the one asked for, else the one its kind recommends, else the default - or,
        when only a picture frame was named and the default points the other way, the first plan with that direction."""
        name = pipeline or (self.settings.kind(kind).pipeline if kind else "") or self.settings.default_pipeline
        plan = self.settings.pipeline(name)
        if profile and not pipeline and not kind:
            want = self._orientation(self._frame(profile))
            if self._orientation(self._frame(plan.delivery_profile)) != want:
                name = next((p.name for p in self.settings.pipelines.values()
                             if self._orientation(self._frame(p.delivery_profile)) == want), name)
        return name

    def create(
        self, name: str, *, genre: str = "", description: str = "", content_language: str = "",
        delivery_profile: str = "", kind: str = "", pipeline: str = "", brief: Mapping[str, Any] | None = None,
        rating: str = "", viewer_age: int | None = None,
    ) -> Project:
        """A project is made with its plan: ``kind`` (drama series, advert ...) recommends a pipeline plan - one
        complete set of models and the picture frame - and ``brief`` gives the structure (seasons, episodes, episode
        length). The plan and the frame are decided here: every picture, video and the film follow them. An explicit
        ``delivery_profile`` may refine the frame but must keep the plan's direction."""
        if kind:
            self.settings.kind(kind)
        rating = rating or self.settings.content.default_rating
        if not self.settings.content.known(rating):
            raise ConfigError(f"unknown rating {rating!r}; known: {sorted(self.settings.content.ratings)}")
        plan_name = self.plan_of(pipeline, kind, delivery_profile)
        plan = self.settings.pipeline(plan_name)
        rating = self.settings.content.higher(rating, plan.rating or None)   # a plan's rating cannot be lowered
        if viewer_age is not None and self.settings.content.policy(rating).min_age > viewer_age:
            raise ContentError("age_restricted", rating=rating)     # nobody makes what they may not see
        profile = delivery_profile or plan.delivery_profile
        frame = self._frame(profile)                                 # also rejects an unknown profile
        if self._orientation(frame) != self._orientation(self._frame(plan.delivery_profile)):
            raise ConfigError(f"pipeline {plan_name!r} is {self._orientation(self._frame(plan.delivery_profile))}; "
                              f"profile {profile!r} is {self._orientation(frame)}")
        resolved = resolve_brief(self.settings, kind, brief) if (kind or brief) else {}
        settings: dict[str, Any] = {"target_sec": resolved["episode_sec"]} if resolved else {}
        base = slugify(name)
        with self.db.transaction() as conn:
            slug, n = base, 2
            while conn.execute("SELECT 1 FROM projects WHERE slug=?", (slug,)).fetchone():
                slug, n = f"{base}-{n}", n + 1
            cur = conn.execute(
                "INSERT INTO projects(slug, name, genre, description, content_language, delivery_profile, aspect_ratio,"
                " kind, pipeline, brief, settings, rating) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (slug, name, genre, description, content_language or self.settings.locale, profile,
                 self._ratio(frame), kind, plan_name, json.dumps(resolved, ensure_ascii=False),
                 json.dumps(settings), rating),
            )
            assert cur.lastrowid is not None
            project_id = cur.lastrowid
        if resolved:
            self._plan_seasons(project_id, resolved)
        return self.get(project_id)

    def _plan_seasons(self, project_id: int, brief: Mapping[str, Any]) -> None:
        """One ``seasons`` row per planned season with its planned episode count (never removes or shrinks anything)."""
        from storyagent.story import StoryStore

        store = StoryStore(self.db)
        for number in range(1, int(brief["seasons"]) + 1):
            row = store.ensure_season(project_id, number)
            if int(row.get("planned_episodes") or 0) < int(brief["episodes_per_season"]):
                store.update_season(project_id, number, {"planned_episodes": int(brief["episodes_per_season"])})

    def set_brief(self, project_id: int, answers: Mapping[str, Any]) -> Project:
        """Change the plan made up front (more seasons/episodes, another length); seasons are only ever added to."""
        project = self.get(project_id)
        resolved = resolve_brief(self.settings, project.kind, {**project.brief, **answers})
        with self.db.transaction() as conn:
            conn.execute("UPDATE projects SET brief=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                         (json.dumps(resolved, ensure_ascii=False), project_id))
        self._plan_seasons(project_id, resolved)
        self.set_settings(project_id, {"target_sec": resolved["episode_sec"]})
        return self.get(project_id)

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
        if "kind" in changes and changes["kind"]:
            self.settings.kind(str(changes["kind"]))
        if "pipeline" in changes:
            # Changing the plan changes the whole set of models and the frame: the direction may not change once there
            # are pictures, and the frame becomes the new plan's unless one of the same direction is named too.
            plan = self.settings.pipeline(str(changes["pipeline"]))
            changes["pipeline"] = plan.name
            wanted = str(changes.get("delivery_profile") or plan.delivery_profile)
            if self._orientation(self._frame(wanted)) != self._orientation(self._frame(plan.delivery_profile)):
                raise ConfigError(
                    f"pipeline {plan.name!r} fixes the direction of the frame; profile {wanted!r} differs")
            changes["delivery_profile"] = wanted
            current = self.get(project_id).rating
            raised = self.settings.content.higher(changes.get("rating", current), plan.rating or None)
            if raised != current:
                changes["rating"] = raised   # checked below like any rating change (not once files exist)
        if "rating" in changes:
            if not self.settings.content.known(str(changes["rating"])):
                raise ConfigError(f"unknown rating {changes['rating']!r}; "
                                  f"known: {sorted(self.settings.content.ratings)}")
            if changes["rating"] != self.get(project_id).rating and self.db.conn.execute(
                    "SELECT 1 FROM media_assets WHERE project_id=? LIMIT 1", (project_id,)).fetchone():
                raise ProjectStateError(
                    f"project {project_id} already has files stored by its rating; the rating is decided at the start")
        if "delivery_profile" in changes:
            before = self.get(project_id)
            old, new = self._frame(before.delivery_profile), self._frame(str(changes["delivery_profile"]))
            if self._orientation(old) != self._orientation(new) and self.db.conn.execute(
                    "SELECT 1 FROM media_assets WHERE project_id=? AND trashed_at IS NULL LIMIT 1",
                    (project_id,)).fetchone():
                raise ProjectStateError(
                    f"project {project_id} already has pictures in a {self._orientation(old)} frame; "
                    "the direction is decided at the start (a frame of the same direction can still change)")
            changes["aspect_ratio"] = self._ratio(new)
        if changes:
            sets = ", ".join(f"{k}=?" for k in changes)
            with self.db.transaction() as conn:
                self.get(project_id)
                conn.execute(
                    f"UPDATE projects SET {sets}, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                    (*changes.values(), project_id),
                )
                if "pipeline" in changes:          # the model choices belong to the plan they were made on
                    kept = dict(self.get(project_id).settings)
                    if kept.pop("pipeline_models", None) is not None:
                        conn.execute("UPDATE projects SET settings=? WHERE id=?",
                                     (json.dumps(kept, ensure_ascii=False), project_id))
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
            for key in ("video_share", "video_budget_hours"):
                if key in current and not (isinstance(current[key], int | float) and current[key] >= 0):
                    raise ConfigError(f"{key} must be a number >= 0")
            if current.get("video_share", 0) > 1:
                raise ConfigError("video_share is a fraction between 0 and 1")
            edit = current.get("edit")
            if edit is not None:
                _check_edit(edit)
            if current.get("pipeline_models") is not None:
                current["pipeline_models"] = check_choices(self.settings, self.get(project_id).pipeline,
                                                           current["pipeline_models"]) or None
                if current["pipeline_models"] is None:
                    del current["pipeline_models"]
            voice = current.get("default_voice")
            if voice is not None and not (isinstance(voice, str) and voice.startswith(("library:voice/", "asset:"))):
                raise ConfigError("default_voice must be a library:voice/<name> or asset:<id> reference")
            for ref in current.get("models", {}):
                self.settings.model(ref)  # unknown model ref -> ConfigError
            conn.execute(
                "UPDATE projects SET settings=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (json.dumps(current, ensure_ascii=False), project_id),
            )
        return self.get(project_id)
