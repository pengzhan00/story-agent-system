"""The review loop: a reviewer looks at a take, leaves a note, the note drives a redo (edit the spec, queue new
candidates), the reviewer compares before and after and adopts one, which resolves the note.

A note is about a *spec* (the editable thing a take was made from) and optionally names the take. A redo is exactly
what a person would do by hand: a new spec version (author ``user``, note quoting the review) and ``regenerate`` -
nothing is generated outside the ledger, so the candidates, the diff and the provenance are the ordinary ones.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from storyagent.db import Database
from storyagent.domain import QualityTier
from storyagent.media.contracts import AssetRecord, JobRecord
from storyagent.media.ledger import Ledger
from storyagent.media.specs import SpecStore
from storyagent.story import StoryStore
from storyagent.util import now_iso

STATUSES = ("open", "redoing", "resolved", "dismissed")


class ReviewError(ValueError):
    code = "invalid"
    status = 422


class NoteNotFound(ReviewError, LookupError):
    code, status = "not_found", 404


class NoteState(ReviewError):
    code, status = "conflict", 409


@dataclass(frozen=True)
class Note:
    id: int
    project_id: int
    subject_type: str
    subject_id: str
    slot: str
    shot_id: int | None
    asset_id: str | None
    body: str
    status: str
    author: str
    resolution: str
    resolved_by: str
    resolved_at: str | None
    redo_spec_version: int | None
    redo_job_ids: list[str]
    created_at: str
    updated_at: str


def _note(row: Any) -> Note:
    d = dict(row)
    d["redo_job_ids"] = json.loads(d["redo_job_ids"] or "[]")
    return Note(**d)


class ReviewStore:
    def __init__(self, db: Database, ledger: Ledger, specs: SpecStore, story: StoryStore) -> None:
        self.db, self.ledger, self.specs, self.story = db, ledger, specs, story

    # -- notes --------------------------------------------------------------------------------------------
    def get(self, note_id: int, project_id: int | None = None) -> Note:
        row = self.db.conn.execute("SELECT * FROM review_notes WHERE id=?", (note_id,)).fetchone()
        if row is None or (project_id is not None and row["project_id"] != project_id):
            raise NoteNotFound(f"unknown note {note_id}")
        return _note(row)

    def add(self, project_id: int, subject_type: str, subject_id: str, body: str, author: str, *,
            slot: str = "main", shot_id: int | None = None, asset_id: str | None = None) -> Note:
        body = body.strip()
        if not body:
            raise ReviewError("a note needs text")
        if self.specs.current(project_id, subject_type, subject_id, slot) is None:
            raise NoteNotFound(f"no spec {subject_type}/{subject_id}/{slot}")
        if asset_id:
            asset = self.ledger.get_asset(asset_id)
            if asset.project_id != project_id:
                raise NoteNotFound(f"unknown asset {asset_id}")
            shot_id = shot_id if shot_id is not None else asset.shot_id
        cur = self.db.conn.execute(
            "INSERT INTO review_notes(project_id, subject_type, subject_id, slot, shot_id, asset_id, body, author) "
            "VALUES (?,?,?,?,?,?,?,?)", (project_id, subject_type, subject_id, slot, shot_id, asset_id, body, author))
        assert cur.lastrowid is not None
        return self.get(cur.lastrowid)

    def notes(self, project_id: int, *, status: str = "", subject_type: str = "", subject_id: str = "",
             shot_id: int | None = None, limit: int = 500) -> list[Note]:
        where, args = ["project_id=?"], [project_id]
        for col, val in (("status", status), ("subject_type", subject_type), ("subject_id", subject_id),
                         ("shot_id", shot_id)):
            if val not in ("", None):
                where.append(f"{col}=?")
                args.append(val)  # type: ignore[arg-type]
        rows = self.db.conn.execute(
            f"SELECT * FROM review_notes WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", (*args, limit)
        ).fetchall()
        return [_note(r) for r in rows]

    def set_status(self, note_id: int, status: str, actor: str, resolution: str = "",
                   project_id: int | None = None) -> Note:
        if status not in STATUSES:
            raise ReviewError(f"status must be one of {list(STATUSES)}")
        note = self.get(note_id, project_id)
        resolved = status in ("resolved", "dismissed")
        self.db.conn.execute(
            "UPDATE review_notes SET status=?, resolution=?, resolved_by=?, resolved_at=?, updated_at=? WHERE id=?",
            (status, resolution if resolved else "", actor if resolved else "", now_iso() if resolved else None,
             now_iso(), note.id))
        return self.get(note.id)

    # -- redo ---------------------------------------------------------------------------------------------
    def redo(self, note_id: int, actor: str, *, changes: Mapping[str, Any] | None = None, n_candidates: int = 1,
             tier: QualityTier = QualityTier.FINAL, project_settings: Mapping[str, Any] | None = None,
             project_id: int | None = None) -> tuple[Note, list[JobRecord]]:
        """Apply the reviewer's changes to the spec (a new version quoting the note; nothing changes if ``changes`` is
        empty - the same spec with a new seed is a legitimate redo) and queue ``n_candidates`` new takes."""
        note = self.get(note_id, project_id)
        if note.status in ("resolved", "dismissed"):
            raise NoteState(f"note {note_id} is {note.status}; reopen it first")
        edits = {k: v for k, v in (changes or {}).items() if v is not None}
        if edits:
            self.specs.edit(note.project_id, note.subject_type, note.subject_id, note.slot, author="user",
                            note=f"review #{note.id} ({actor}): {note.body[:120]}", **edits)
        spec = self.specs.current(note.project_id, note.subject_type, note.subject_id, note.slot)
        assert spec is not None
        jobs = self.specs.regenerate(
            note.project_id, note.subject_type, note.subject_id, note.slot, n_candidates=n_candidates,
            vary_seed=True, tier=tier, shot_id=note.shot_id, project_settings=project_settings)
        ids = [*note.redo_job_ids, *(j.id for j in jobs)]
        self.db.conn.execute(
            "UPDATE review_notes SET status='redoing', redo_spec_version=?, redo_job_ids=?, resolution='', "
            "resolved_by='', resolved_at=NULL, updated_at=? WHERE id=?",
            (spec.version, json.dumps(ids), now_iso(), note.id))
        return self.get(note.id), jobs

    def adopted(self, asset: AssetRecord, actor: str) -> list[Note]:
        """A take made by a redo was adopted: the notes waiting on it are resolved. (Adopting any other take leaves
        the notes alone - the reviewer decides.)"""
        if not asset.job_id or asset.project_id is None:
            return []
        out = []
        for note in self.notes(asset.project_id, status="redoing"):
            if asset.job_id in note.redo_job_ids:
                out.append(self.set_status(note.id, "resolved", actor, f"adopted:{asset.id}"))
        return out

    def compare(
        self, note_id: int, project_id: int | None = None
    ) -> tuple[AssetRecord | None, list[AssetRecord], list[JobRecord]]:
        """What the note was about, the takes the redo(s) made, and the redo jobs (to show the ones still running)."""
        note = self.get(note_id, project_id)
        before = self.ledger.get_asset(note.asset_id) if note.asset_id else None
        jobs = [self.ledger.get_job(j) for j in note.redo_job_ids]
        after = [a for j in jobs for a in self.ledger.list_assets(job_id=j.id, include_trashed=False)]
        return before, after, jobs

    # -- the board ----------------------------------------------------------------------------------------
    def board(self, project_id: int) -> list[dict[str, Any]]:
        """Every shot with its takes in use and how many notes wait on it - the reviewer's list."""
        counts: dict[int | None, dict[str, int]] = {}
        for n in self.notes(project_id, limit=5000):
            c = counts.setdefault(n.shot_id, {"open": 0, "redoing": 0, "resolved": 0})
            if n.status in c:
                c[n.status] += 1
        assets = [a for a in self.ledger.list_assets(project_id=project_id, current_only=True, limit=1000)
                  if a.shot_id is not None]
        by_shot: dict[int, list[AssetRecord]] = {}
        for a in assets:
            by_shot.setdefault(int(a.shot_id or 0), []).append(a)
        episodes = {e["id"]: e["number"] for e in self.story.list_episodes(project_id)}
        out = []
        for shot in self.story.list_shots(project_id):
            c = counts.get(shot["id"], {})
            out.append({
                "shot_id": shot["id"], "episode": episodes.get(shot.get("episode_id"), 0),
                "act": shot["act_number"], "scene": shot["scene_number"], "shot": shot["shot_number"],
                "location": shot["location"], "assets": by_shot.get(shot["id"], []),
                "open_notes": c.get("open", 0), "redoing_notes": c.get("redoing", 0),
                "resolved_notes": c.get("resolved", 0),
            })
        return out
