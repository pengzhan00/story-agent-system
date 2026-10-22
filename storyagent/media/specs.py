"""Generation specs: the editable, versioned description of everything that can be generated.

A *spec* is what the creator iterates on (portrait prompt, dialogue line, theme-song lyrics, shot
look ...). A *job* is one execution of one spec version, frozen at enqueue time. Specs are append-only:
an edit creates a new version, so history, diff and revert are free, and in-flight jobs are never
affected by later edits.
"""
from __future__ import annotations

import random
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from storyagent.config import ConfigError, Settings
from storyagent.db import Database
from storyagent.domain import MediaKind, QualityTier
from storyagent.util import dumps, loads, new_id

from .contracts import AssetRecord, JobRecord, MediaJob
from .ledger import Ledger
from .prompts import render_prompt

_SUBJECT = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")
_EDITABLE = ("model_ref", "prompt", "negative_prompt", "text_payload", "params", "seed", "refs", "depends_on")


class SpecError(ValueError):
    pass


class SpecLocked(SpecError):
    pass


@dataclass(frozen=True)
class SpecRecord:
    id: str
    project_id: int
    subject_type: str
    subject_id: str
    slot: str
    version: int
    parent_version: int | None
    kind: MediaKind
    model_ref: str
    prompt: str
    negative_prompt: str
    text_payload: str
    params: dict[str, Any]
    seed: int | None
    refs: list[Any]
    depends_on: list[dict[str, Any]]
    author: str
    note: str
    created_at: str

    @classmethod
    def from_row(cls, r: Any) -> SpecRecord:
        return cls(
            id=r["id"], project_id=r["project_id"], subject_type=r["subject_type"], subject_id=r["subject_id"],
            slot=r["slot"], version=r["version"], parent_version=r["parent_version"], kind=MediaKind(r["kind"]),
            model_ref=r["model_ref"], prompt=r["prompt"], negative_prompt=r["negative_prompt"],
            text_payload=r["text_payload"], params=loads(r["params"], {}), seed=r["seed"],
            refs=loads(r["refs"], []), depends_on=loads(r["depends_on"], []), author=r["author"],
            note=r["note"], created_at=r["created_at"],
        )

    @property
    def key(self) -> tuple[int, str, str, str]:
        return (self.project_id, self.subject_type, self.subject_id, self.slot)


@dataclass(frozen=True)
class ReviewItem:
    spec: SpecRecord
    changed_dependencies: list[dict[str, Any]] = field(default_factory=list)  # {subject.., recorded, current}


class SpecStore:
    def __init__(
        self, db: Database, settings: Settings, ledger: Ledger | None = None, rng: random.Random | None = None
    ) -> None:
        self.db, self.settings = db, settings
        self.ledger = ledger or Ledger(db, settings)
        self._rng = rng or random.SystemRandom()

    # -- create / read ------------------------------------------------------------------------
    def create(
        self,
        project_id: int,
        subject_type: str,
        subject_id: str,
        kind: MediaKind,
        *,
        slot: str = "main",
        model_ref: str = "",
        prompt: str = "",
        negative_prompt: str = "",
        text_payload: str = "",
        params: Mapping[str, Any] | None = None,
        seed: int | None = None,
        refs: list[Any] | None = None,
        depends_on: list[dict[str, Any]] | None = None,
        author: str = "user",
        note: str = "",
    ) -> SpecRecord:
        if not _SUBJECT.match(subject_type):
            raise SpecError(f"subject_type must look like '<noun>.<aspect>', got {subject_type!r}")
        if model_ref:
            m = self.settings.model(model_ref)
            if m.kind != kind.value:
                raise ConfigError(f"model {model_ref!r} is {m.kind!r}, not {kind.value!r}")
        with self.db.transaction():
            if self.current(project_id, subject_type, subject_id, slot) is not None:
                raise SpecError(f"{subject_type}/{subject_id}/{slot} already exists; use edit()")
            return self._insert(
                project_id, subject_type, subject_id, slot, 1, None, kind, model_ref, prompt, negative_prompt,
                text_payload, dict(params or {}), seed, refs or [], depends_on or [], author, note,
            )

    def _insert(self, project_id, subject_type, subject_id, slot, version, parent, kind, model_ref, prompt,  # noqa: ANN001, ANN202, PLR0913
                negative_prompt, text_payload, params, seed, refs, depends_on, author, note):
        sid = new_id()
        self.db.conn.execute(
            """INSERT INTO gen_specs(id, project_id, subject_type, subject_id, slot, version, parent_version,
                   kind, model_ref, prompt, negative_prompt, text_payload, params, seed, refs, depends_on,
                   author, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, project_id, subject_type, subject_id, slot, version, parent, kind.value, model_ref, prompt,
             negative_prompt, text_payload, dumps(params), seed, dumps(refs), dumps(depends_on), author, note),
        )
        return self.get(sid)

    def get(self, spec_id: str) -> SpecRecord:
        row = self.db.conn.execute("SELECT * FROM gen_specs WHERE id=?", (spec_id,)).fetchone()
        if row is None:
            raise SpecError(f"unknown spec {spec_id}")
        return SpecRecord.from_row(row)

    def current(self, project_id: int, subject_type: str, subject_id: str, slot: str = "main") -> SpecRecord | None:
        row = self.db.conn.execute(
            "SELECT * FROM gen_specs WHERE project_id=? AND subject_type=? AND subject_id=? AND slot=? "
            "ORDER BY version DESC LIMIT 1",
            (project_id, subject_type, subject_id, slot),
        ).fetchone()
        return SpecRecord.from_row(row) if row else None

    def versions(self, project_id: int, subject_type: str, subject_id: str, slot: str = "main") -> list[SpecRecord]:
        rows = self.db.conn.execute(
            "SELECT * FROM gen_specs WHERE project_id=? AND subject_type=? AND subject_id=? AND slot=? "
            "ORDER BY version",
            (project_id, subject_type, subject_id, slot),
        ).fetchall()
        return [SpecRecord.from_row(r) for r in rows]

    def list_current(self, project_id: int, subject_type: str | None = None) -> list[SpecRecord]:
        sql = "SELECT * FROM gen_specs_current WHERE project_id=?"
        args: list[Any] = [project_id]
        if subject_type:
            sql += " AND subject_type=?"
            args.append(subject_type)
        rows = self.db.conn.execute(sql + " ORDER BY subject_type, subject_id, slot", args).fetchall()
        return [SpecRecord.from_row(r) for r in rows]

    # -- edit / revert / lock -----------------------------------------------------------------
    def _lock_row(self, project_id: int, subject_type: str, subject_id: str, slot: str) -> Any:
        return self.db.conn.execute(
            "SELECT * FROM gen_spec_locks WHERE project_id=? AND subject_type=? AND subject_id=? AND slot=?",
            (project_id, subject_type, subject_id, slot),
        ).fetchone()

    def is_locked(self, project_id: int, subject_type: str, subject_id: str, slot: str = "main") -> bool:
        return self._lock_row(project_id, subject_type, subject_id, slot) is not None

    def lock(self, project_id: int, subject_type: str, subject_id: str, slot: str = "main", note: str = "") -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO gen_spec_locks(project_id, subject_type, subject_id, slot, note) "
            "VALUES (?,?,?,?,?)",
            (project_id, subject_type, subject_id, slot, note),
        )

    def unlock(self, project_id: int, subject_type: str, subject_id: str, slot: str = "main") -> None:
        self.db.conn.execute(
            "DELETE FROM gen_spec_locks WHERE project_id=? AND subject_type=? AND subject_id=? AND slot=?",
            (project_id, subject_type, subject_id, slot),
        )

    def edit(
        self, project_id: int, subject_type: str, subject_id: str, slot: str = "main", *,
        author: str = "user", note: str = "", params_patch: Mapping[str, Any] | None = None, **changes: Any,
    ) -> SpecRecord:
        """New version with ``changes`` applied. Unchanged content returns the current version (no churn)."""
        unknown = set(changes) - set(_EDITABLE)
        if unknown:
            raise SpecError(f"cannot edit {sorted(unknown)}; editable: {list(_EDITABLE)}")
        with self.db.transaction():
            cur = self.current(project_id, subject_type, subject_id, slot)
            if cur is None:
                raise SpecError(f"no spec {subject_type}/{subject_id}/{slot}")
            if self.is_locked(project_id, subject_type, subject_id, slot):
                raise SpecLocked(f"{subject_type}/{subject_id}/{slot} is locked")
            new = {f: getattr(cur, f) for f in _EDITABLE}
            new.update(changes)
            if params_patch:
                new["params"] = {**new["params"], **params_patch}
            if new["model_ref"]:
                m = self.settings.model(new["model_ref"])
                if m.kind != cur.kind.value:
                    raise ConfigError(f"model {new['model_ref']!r} is {m.kind!r}, not {cur.kind.value!r}")
            if new == {f: getattr(cur, f) for f in _EDITABLE}:
                return cur
            return self._insert(
                project_id, subject_type, subject_id, slot, cur.version + 1, cur.version, cur.kind,
                new["model_ref"], new["prompt"], new["negative_prompt"], new["text_payload"], new["params"],
                new["seed"], new["refs"], new["depends_on"], author, note,
            )

    def revert_to(
        self, project_id: int, subject_type: str, subject_id: str, version: int, slot: str = "main",
        *, author: str = "user",
    ) -> SpecRecord:
        old = next((v for v in self.versions(project_id, subject_type, subject_id, slot) if v.version == version), None)
        if old is None:
            raise SpecError(f"no version {version}")
        return self.edit(
            project_id, subject_type, subject_id, slot, author=author, note=f"revert to v{version}",
            **{f: getattr(old, f) for f in _EDITABLE},
        )

    @staticmethod
    def diff(a: SpecRecord, b: SpecRecord) -> dict[str, tuple[Any, Any]]:
        return {f: (getattr(a, f), getattr(b, f)) for f in _EDITABLE if getattr(a, f) != getattr(b, f)}

    # -- staleness ----------------------------------------------------------------------------
    def stale_assets(self, project_id: int) -> list[tuple[AssetRecord, int]]:
        """Current assets generated from an older spec version than the spec's latest. (asset, latest_version)"""
        rows = self.db.conn.execute(
            """SELECT a.*, c.version AS latest FROM media_assets a
               JOIN gen_specs s ON s.id = a.spec_id
               JOIN gen_specs_current c ON c.project_id=s.project_id AND c.subject_type=s.subject_type
                    AND c.subject_id=s.subject_id AND c.slot=s.slot
               WHERE a.project_id=? AND a.is_current=1 AND a.trashed_at IS NULL AND s.version < c.version""",
            (project_id,),
        ).fetchall()
        return [(AssetRecord.from_row(r), r["latest"]) for r in rows]

    def needs_review(self, project_id: int) -> list[ReviewItem]:
        """Specs whose recorded dependencies have since moved on (e.g. a shot whose character was re-edited)."""
        out: list[ReviewItem] = []
        for spec in self.list_current(project_id):
            changed = []
            for dep in spec.depends_on:
                latest = self.current(project_id, dep["subject_type"], dep["subject_id"], dep.get("slot", "main"))
                if latest is not None and latest.version > int(dep["version"]):
                    changed.append({**dep, "recorded": int(dep["version"]), "current": latest.version})
            if changed:
                out.append(ReviewItem(spec, changed))
        return out

    def acknowledge_dependencies(self, spec: SpecRecord, *, author: str = "user") -> SpecRecord:
        """Record that the creator reviewed the change: bump ``depends_on`` to the dependencies' latest versions."""
        refreshed = []
        for dep in spec.depends_on:
            latest = self.current(spec.project_id, dep["subject_type"], dep["subject_id"], dep.get("slot", "main"))
            refreshed.append({**dep, "version": latest.version if latest else dep["version"]})
        return self.edit(
            spec.project_id, spec.subject_type, spec.subject_id, spec.slot, author=author,
            note="dependencies reviewed", depends_on=refreshed,
        )

    # -- spec -> job --------------------------------------------------------------------------
    @staticmethod
    def job_slot(spec: SpecRecord) -> str:
        """The ledger keeps ONE current asset per (project, shot, kind, slot). A shot's keyframe or video is the only
        one of its kind in that shot, so it keeps the spec's own slot; everything else (two characters' portraits, the
        lines of one shot, two music cues ...) would collide, so its slot names the subject."""
        if spec.subject_type in ("shot.keyframe", "shot.video"):
            return spec.slot
        return f"{spec.subject_type}:{spec.subject_id}" + ("" if spec.slot == "main" else f":{spec.slot}")

    def build_job(
        self, spec: SpecRecord, *, tier: QualityTier = QualityTier.FINAL, seed: int | None = None,
        shot_id: int | None = None, priority: int = 5, extra_inputs: Mapping[str, Any] | None = None,
    ) -> MediaJob:
        """Freeze the spec into a job request: dialect-rendered prompt, params, refs and provenance link."""
        cfg = self.ledger.settings_for(spec.project_id)
        model_ref = spec.model_ref or cfg.default_model(spec.kind.value).ref
        model = cfg.model(model_ref)
        params = dict(spec.params)
        structured = params.pop("structured", None)
        voice_ref = params.pop("voice_ref", None)   # TTS / voice conversion: who to sound like (job input, not a knob)
        inputs: dict[str, Any] = {
            "prompt": render_prompt(model.prompt_profile, spec.prompt, structured),
            "negative_prompt": spec.negative_prompt,
            "text": spec.text_payload,
            "refs": spec.refs,
            "subject": {"type": spec.subject_type, "id": spec.subject_id, "slot": spec.slot},
            **({"voice_ref": voice_ref} if voice_ref else {}),
            **(extra_inputs or {}),
        }
        return MediaJob(
            kind=spec.kind, inputs=inputs, project_id=spec.project_id, shot_id=shot_id, model_ref=model_ref,
            tier=tier, slot=self.job_slot(spec), params=params, seed=seed if seed is not None else spec.seed,
            priority=priority, spec_id=spec.id, spec_version=spec.version,
        )

    def regenerate(
        self, project_id: int, subject_type: str, subject_id: str, slot: str = "main", *,
        n_candidates: int = 1, vary_seed: bool = True, tier: QualityTier = QualityTier.FINAL,
        shot_id: int | None = None, priority: int = 5, project_settings: Mapping[str, Any] | None = None,
    ) -> list[JobRecord]:
        """Queue ``n_candidates`` jobs from the *current* spec version. Pick the winner with ``Ledger.adopt``."""
        spec = self.current(project_id, subject_type, subject_id, slot)
        if spec is None:
            raise SpecError(f"no spec {subject_type}/{subject_id}/{slot}")
        if n_candidates < 1:
            raise SpecError("n_candidates must be >= 1")
        if not vary_seed and n_candidates > 1:
            raise SpecError("identical seeds would produce identical candidates; use vary_seed=True")
        seeds: list[int | None]
        if vary_seed:
            seeds = []
            while len(seeds) < n_candidates:
                candidate = self._rng.randrange(2**31)
                if candidate not in seeds:
                    seeds.append(candidate)
        else:
            seeds = [spec.seed]
        jobs = []
        for seed in seeds:
            rec, _ = self.ledger.enqueue(
                self.build_job(spec, tier=tier, seed=seed, shot_id=shot_id, priority=priority), project_settings
            )
            jobs.append(rec)
        return jobs
