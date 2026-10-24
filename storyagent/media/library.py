"""Uploads and the cross-project asset library.

* ``import_file``  — bring an outside file (reference voice, face photo, footage, LoRA) into a project as an asset.
* ``Library``      — publish an asset under ``<role>/<name>`` so any project can use it. The file is copied into
                     ``<home>/library/`` and gets its own asset row; the item keeps the source for traceability.

Jobs refer to both with ``asset:<id>`` / ``library:<role>/<name>`` (see ``assetrefs``).
"""
from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storyagent.config import Settings
from storyagent.db import Database
from storyagent.util import dumps, loads, new_id

from .contracts import AssetRecord
from .ledger import Ledger, LedgerError, NewAsset
from .probe import probe

AUDIO = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO = {".mp4", ".mov", ".mkv", ".webm"}
TEXT = {".txt", ".md", ".markdown"}
MODEL = {".safetensors", ".gguf", ".ckpt", ".pt"}
ROLES = ("voice", "portrait", "lora", "music", "sfx", "reference", "video", "story", "script", "dialogue", "image")
_SAFE = re.compile(r"^[^/\\\x00]{1,80}$")


class LibraryError(ValueError):
    pass


def upload_kind(path: Path) -> str:
    ext = path.suffix.lower()
    for group, name in ((AUDIO, "audio"), (IMAGE, "image"), (VIDEO, "video"), (TEXT, "text"), (MODEL, "model")):
        if ext in group:
            return f"upload.{name}"
    return "upload.file"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def import_file(
    settings: Settings, ledger: Ledger, src: Path, *, project_id: int | None, slot: str = "main",
    shot_id: int | None = None, metadata: Mapping[str, Any] | None = None, adopt: bool = True,
) -> AssetRecord:
    """Copy ``src`` into the project's media tree and register it (kind ``upload.<audio|image|video|text|...>``)."""
    if not src.is_file():
        raise LibraryError(f"file not found: {src}")
    kind = upload_kind(src)
    slug, rating = "_unassigned", settings.content.default_rating
    if project_id is not None:
        row = ledger.db.conn.execute("SELECT slug, rating FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise LibraryError(f"unknown project {project_id}")
        slug, rating = row["slug"], row["rating"]
    dest_dir = settings.project_media_dir(slug, kind, shot_id, rating)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{new_id()}{src.suffix.lower()}"
    shutil.copy2(src, dest)
    info = probe(settings, dest) if kind in ("upload.audio", "upload.image", "upload.video") else None
    meta = {"original_name": src.name, **dict(metadata or {})}
    return ledger.register_asset(
        NewAsset(
            kind=kind, slot=slot, path=str(dest.relative_to(settings.home)), sha256=_sha(dest),
            bytes=dest.stat().st_size, duration_sec=info.duration_sec if info else None,
            width=info.width if info else None, height=info.height if info else None,
            fps=info.fps if info else None, has_audio=info.has_audio if info else None, metadata=meta,
            provenance={"schema": "storyagent.provenance.v1", "origin": "upload", "original_name": src.name},
            rating=rating,
        ),
        project_id=project_id, shot_id=shot_id, adopt=adopt,
    )


@dataclass(frozen=True)
class LibraryItem:
    id: str
    role: str
    name: str
    asset_id: str
    description: str
    tags: list[str]
    meta: dict[str, Any]
    source_asset_id: str | None
    source_project_id: int | None
    created_at: str
    trashed_at: str | None = None
    asset: AssetRecord | None = field(default=None, compare=False)

    @property
    def ref(self) -> str:
        return f"library:{self.role}/{self.name}"


class Library:
    def __init__(self, db: Database, settings: Settings, ledger: Ledger | None = None) -> None:
        self.db, self.settings = db, settings
        self.ledger = ledger or Ledger(db, settings)

    def _item(self, row: Any) -> LibraryItem:
        return LibraryItem(
            id=row["id"], role=row["role"], name=row["name"], asset_id=row["asset_id"],
            description=row["description"], tags=loads(row["tags"], []), meta=loads(row["meta"], {}),
            source_asset_id=row["source_asset_id"], source_project_id=row["source_project_id"],
            created_at=row["created_at"], trashed_at=row["trashed_at"], asset=self.ledger.get_asset(row["asset_id"]),
        )

    # -- reads --------------------------------------------------------------------------------
    def get(self, item_id: str) -> LibraryItem:
        row = self.db.conn.execute("SELECT * FROM library_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise LibraryError(f"unknown library item {item_id}")
        return self._item(row)

    def find(self, role: str, name: str) -> LibraryItem | None:
        row = self.db.conn.execute(
            "SELECT * FROM library_items WHERE role=? AND name=? AND trashed_at IS NULL", (role, name)
        ).fetchone()
        return self._item(row) if row else None

    def list(self, *, role: str | None = None, tag: str | None = None, q: str | None = None) -> list[LibraryItem]:
        where, args = ["trashed_at IS NULL"], []
        if role:
            where.append("role=?")
            args.append(role)
        if q:
            where.append("(name LIKE ? OR description LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        rows = self.db.conn.execute(
            f"SELECT * FROM library_items WHERE {' AND '.join(where)} ORDER BY role, name", args
        ).fetchall()
        items = [self._item(r) for r in rows]
        return [i for i in items if tag in i.tags] if tag else items

    # -- writes -------------------------------------------------------------------------------
    def publish(
        self, asset_id: str, role: str, name: str, *, description: str = "", tags: Sequence[str] = (),
        meta: Mapping[str, Any] | None = None, replace: bool = False,
    ) -> LibraryItem:
        """Copy a project asset into the library under ``role/name``."""
        if role not in ROLES:
            raise LibraryError(f"role must be one of {', '.join(ROLES)} (got {role!r})")
        if not _SAFE.match(name):
            raise LibraryError("name must be 1-80 characters and contain no slash")
        try:
            src = self.ledger.get_asset(asset_id)
        except LedgerError as exc:
            raise LibraryError(str(exc)) from exc
        if src.trashed_at:
            raise LibraryError(f"asset {asset_id} is in the trash")
        src_file = self.settings.home / src.path
        if not src_file.is_file():
            raise LibraryError(f"asset file is missing: {src.path}")
        existing = self.find(role, name)
        if existing and not replace:
            raise LibraryError(f"{role}/{name} already exists (use replace to overwrite)")
        vaulted = self.settings.content.policy(src.rating).vault          # a restricted asset stays in the vault
        dest_dir = (self.settings.vault_dir / "_library" if vaulted else self.settings.home / "library") / role
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{new_id()}{src_file.suffix.lower()}"
        shutil.copy2(src_file, dest)
        item_meta = {**dict(src.metadata.get("library_meta", {})), **dict(meta or {})}
        copy = self.ledger.register_asset(
            NewAsset(
                kind=src.kind, slot=f"{role}/{name}", path=str(dest.relative_to(self.settings.home)),
                sha256=src.sha256 or _sha(dest), bytes=src.bytes, duration_sec=src.duration_sec, width=src.width,
                height=src.height, fps=src.fps, has_audio=src.has_audio, metadata=dict(src.metadata),
                provenance={**src.provenance, "library": {"source_asset": src.id, "source_project": src.project_id}},
                rating=src.rating,
            ),
            project_id=None, adopt=False,
        )
        item_id = new_id()
        with self.db.transaction() as conn:
            if existing:
                conn.execute("UPDATE library_items SET trashed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                             (existing.id,))
            conn.execute(
                """INSERT INTO library_items(id, role, name, asset_id, description, tags, meta, source_asset_id,
                       source_project_id, rating) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (item_id, role, name, copy.id, description, dumps(list(tags)), dumps(item_meta), src.id,
                 src.project_id, src.rating),
            )
        return self.get(item_id)

    def add_text(
        self, text: str, role: str, name: str, *, project_id: int, slot: str, kind: str = "text.script",
        description: str = "", tags: Sequence[str] = (), meta: Mapping[str, Any] | None = None, replace: bool = False,
        origin: Mapping[str, Any] | None = None,
    ) -> LibraryItem:
        """File a text (a script, a story) of a project under ``role/name``: the text becomes an asset of the project
        (``slot``, kept in the vault when the project's rating says so) and its copy is the named item."""
        if not _SAFE.match(name):
            raise LibraryError("name must be 1-80 characters and contain no slash")
        if self.find(role, name) and not replace:
            raise LibraryError(f"{role}/{name} already exists (use replace to overwrite)")
        row = self.db.conn.execute("SELECT slug, rating FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise LibraryError(f"unknown project {project_id}")
        dest_dir = self.settings.project_media_dir(row["slug"], kind, None, row["rating"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{new_id()}.md"
        dest.write_text(text, "utf-8")
        asset = self.ledger.register_asset(
            NewAsset(
                kind=kind, slot=slot, path=str(dest.relative_to(self.settings.home)), sha256=_sha(dest),
                bytes=dest.stat().st_size, metadata={"original_name": f"{slot}.md", **dict(origin or {})},
                provenance={"schema": "storyagent.provenance.v1", "origin": "project_text", **dict(origin or {})},
                rating=row["rating"],
            ),
            project_id=project_id, adopt=True,
        )
        return self.publish(asset.id, role, name, description=description, tags=tags, meta=meta, replace=replace)

    def add_file(
        self, src: Path, role: str, name: str, *, description: str = "", tags: Sequence[str] = (),
        meta: Mapping[str, Any] | None = None, replace: bool = False,
    ) -> LibraryItem:
        """Import an outside file straight into the library (no project involved)."""
        tmp = import_file(self.settings, self.ledger, src, project_id=None, slot=f"import-{new_id()[:8]}")
        try:
            return self.publish(tmp.id, role, name, description=description, tags=tags, meta=meta, replace=replace)
        finally:
            self.ledger.trash_asset(tmp.id)
            (self.settings.home / tmp.path).unlink(missing_ok=True)

    def update(self, item_id: str, *, description: str | None = None, tags: Sequence[str] | None = None,
               meta: Mapping[str, Any] | None = None) -> LibraryItem:
        item = self.get(item_id)
        self.db.conn.execute(
            "UPDATE library_items SET description=?, tags=?, meta=? WHERE id=?",
            (item.description if description is None else description,
             dumps(item.tags if tags is None else list(tags)),
             dumps(item.meta if meta is None else {**item.meta, **dict(meta)}), item_id),
        )
        return self.get(item_id)

    def remove(self, item_id: str) -> LibraryItem:
        """Soft delete: the name becomes free again; files stay until a purge."""
        self.get(item_id)
        self.db.conn.execute(
            "UPDATE library_items SET trashed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (item_id,)
        )
        return self.get(item_id)
