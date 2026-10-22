"""Asset packs: one directory (or zip) with a ``pack.json`` manifest that carries a project's story data, generation
specs and media. The same format backs up a project, moves it between machines and hands material to a teammate.

* ``export_pack``  - write a project to a directory or ``.zip`` (current takes by default, ``include_history`` adds the
                     candidates); every file is listed in the manifest with its sha256.
* ``import_pack``  - read a pack into a project (a new one, or ``into=`` an existing one). Rows are matched by stable
                     keys (characters and scenes by name, episodes by number, shots by episode/act/scene/shot), never by
                     database id, so ids are remapped. ``dry_run`` runs the whole import and rolls it back.
* ``import_folder``- a plain folder without a manifest: file names are mapped to slots by the rules in ``[runtime]
                     loose_rules`` (``shot_003.mp4`` -> the video of shot 3 ...); what no rule fits is reported.

Files that are already in the project (same sha256 in the same slot) are never stored twice. Prompts and local paths
do not travel in per-asset provenance (the same redaction as the delivery package).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storyagent.config import ConfigError, Settings
from storyagent.db import Database
from storyagent.projects import ProjectStore
from storyagent.story import _FIELDS, StoryStore
from storyagent.util import now_iso

from .compliance import redact_for_export
from .ledger import Ledger, NewAsset
from .probe import probe
from .specs import SpecError, SpecStore

SCHEMA = "storyagent.pack.v1"
MANIFEST = "pack.json"
POLICIES = ("skip", "new_version")
SCOPES = ("all", "assets", "story")
# story tables in dependency order, with the stable key used to match rows between databases
_STORY = ("seasons", "episodes", "characters", "scenes", "scripts", "music_themes", "sound_effects",
          "character_looks", "props", "shots")
_SUBJECT_TABLE = {"character": "characters", "scene": "scenes", "shot": "shots", "dialogue": "shots",
                  "music": "music_themes"}
_SLOT_SUBJECT = re.compile(r"^([a-z][a-z_]*\.[a-z][a-z_]*):([^:]+)(.*)$")
_REF = re.compile(r"^asset:([0-9a-f]{32})$")


class PackError(ValueError):
    """A pack that cannot be read or written; ``key`` is an i18n key (``pack.error.<code>``)."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code, self.params = code, params


class _DryRun(Exception):  # noqa: N818 - control flow, not an error
    pass


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


# =============================================================================================== export
def _story_rows(story: StoryStore, project_id: int) -> dict[str, list[dict[str, Any]]]:
    """Story rows without database ids/project ids; cross references are replaced by their stable keys."""
    chars = {c["id"]: c["name"] for c in story.list_characters(project_id)}
    episodes = {e["id"]: e["number"] for e in story.list_episodes(project_id)}
    scripts = {s["id"]: s["episode_no"] for s in story.list_scripts(project_id)}
    raw = {
        "seasons": story.list_seasons(project_id), "episodes": story.list_episodes(project_id),
        "characters": story.list_characters(project_id), "scenes": story.list_locations(project_id),
        "scripts": story.list_scripts(project_id), "music_themes": story.list_music(project_id),
        "sound_effects": story.list_sfx(project_id), "character_looks": story.list_looks(project_id),
        "props": story.list_props(project_id), "shots": story.list_shots(project_id),
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for table, rows in raw.items():
        fields = _FIELDS[table]
        items = []
        for row in rows:
            item = {"_id": row["id"], **{k: row[k] for k in fields if k in row}}
            if table == "character_looks":
                item.pop("character_id", None)
                item["_character"] = chars.get(row["character_id"], "")
            if table == "shots":
                item.pop("script_id", None)
                item["_episode"] = episodes.get(row["episode_id"], 0)
                item["_script"] = scripts.get(row["script_id"]) if row.get("script_id") else None
            items.append(item)
        out[table] = items
    return out


def export_pack(
    settings: Settings, db: Database, project_id: int, out: Path, *, scope: str = "all", include_history: bool = False,
) -> dict[str, Any]:
    """Write ``project_id`` to ``out`` (a directory, or a ``.zip`` file by its name). Returns the manifest."""
    if scope not in SCOPES:
        raise PackError("scope", scope=scope, allowed=", ".join(SCOPES))
    project = ProjectStore(db, settings).get(project_id)
    ledger = Ledger(db, settings)
    specs = SpecStore(db, settings, ledger)
    story = StoryStore(db)
    as_zip = out.suffix.lower() == ".zip"
    if out.exists():
        raise PackError("exists", path=str(out))
    work = Path(tempfile.mkdtemp(prefix="pack-")) if as_zip else out
    try:
        work.mkdir(parents=True, exist_ok=True)
        files: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        def add(rel: str, src: Path | None = None, *, data: Any = None) -> None:
            dest = work / rel
            if data is not None:
                _dump(dest, data)
            else:
                assert src is not None
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            files.append({"path": rel, "sha256": _sha(dest), "bytes": dest.stat().st_size})

        story_rows = _story_rows(story, project_id)
        for table, rows in story_rows.items():
            add(f"story/{table}.json", data=rows)
        counts: dict[str, int] = {t: len(r) for t, r in story_rows.items()}
        if scope != "story":
            spec_rows, locks = _export_specs(specs, db, project_id, include_history)
            add("specs/specs.json", data={"specs": spec_rows, "locks": locks})
            counts["specs"] = len(spec_rows)
            shot_key = {s["_id"]: [s["_episode"], s["act_number"], s["scene_number"], s["shot_number"]]
                        for s in story_rows["shots"]}
            entries = []
            for a in ledger.list_assets(project_id=project_id, current_only=not include_history, limit=100000):
                src = settings.home / a.path
                if not src.is_file():
                    warnings.append({"code": "file_missing", "asset": a.id, "path": a.path})
                    continue
                if a.sha256 and _sha(src) != a.sha256:
                    warnings.append({"code": "file_changed", "asset": a.id, "path": a.path})
                    continue
                rel = f"assets/{a.kind}/{a.shot_id or '_'}/{a.id}{src.suffix.lower()}"
                add(rel, src)
                entries.append({
                    "id": a.id, "file": rel, "kind": a.kind, "slot": a.slot, "sha256": files[-1]["sha256"],
                    "bytes": a.bytes, "version": a.version, "current": a.is_current, "shot": shot_key.get(a.shot_id),
                    "duration_sec": a.duration_sec, "width": a.width, "height": a.height, "fps": a.fps,
                    "has_audio": a.has_audio, "metadata": a.metadata,
                    "provenance": redact_for_export(a.provenance) if a.provenance else {},
                    "spec": ({"id": a.spec_id, "version": a.spec_version} if a.spec_id else None),
                })
            add("assets/assets.json", data=entries)
            counts["assets"] = len(entries)
        manifest = {
            "schema": SCHEMA, "exported_at": now_iso(), "scope": scope, "include_history": include_history,
            "project": {"name": project.name, "slug": project.slug, "genre": project.genre,
                        "description": project.description, "content_language": project.content_language,
                        "delivery_profile": project.delivery_profile, "kind": project.kind,
                        "pipeline": project.pipeline, "brief": project.brief, "settings": project.settings},
            "counts": counts, "files": files, "warnings": warnings,
        }
        _dump(work / MANIFEST, manifest)
        if as_zip:
            out.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in sorted(work.rglob("*")):
                    if p.is_file():
                        zf.write(p, p.relative_to(work).as_posix(),
                                 compress_type=zipfile.ZIP_STORED if p.suffix.lower() in _MEDIA_EXT
                                 else zipfile.ZIP_DEFLATED)
        return manifest
    finally:
        if as_zip:
            shutil.rmtree(work, ignore_errors=True)


_MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".webp", ".mp3", ".m4a", ".flac", ".ogg",
              ".aac", ".safetensors"}


def _export_specs(
    specs: SpecStore, db: Database, project_id: int, include_history: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = db.conn.execute(
        "SELECT * FROM gen_specs WHERE project_id=? ORDER BY subject_type, subject_id, slot, version",
        (project_id,)).fetchall()
    newest: dict[tuple[str, str, str], int] = {}
    for r in rows:
        newest[(r["subject_type"], r["subject_id"], r["slot"])] = r["version"]
    keep = rows if include_history else [
        r for r in rows if r["version"] == newest[(r["subject_type"], r["subject_id"], r["slot"])]]
    out = []
    for r in keep:
        s = specs.get(r["id"])
        out.append({"id": s.id, "subject_type": s.subject_type, "subject_id": s.subject_id, "slot": s.slot,
                    "version": s.version, "parent_version": s.parent_version, "kind": s.kind.value,
                    "model_ref": s.model_ref, "prompt": s.prompt, "negative_prompt": s.negative_prompt,
                    "text_payload": s.text_payload, "params": s.params, "seed": s.seed, "refs": s.refs,
                    "depends_on": s.depends_on, "author": s.author, "note": s.note})
    locks = [dict(r) for r in db.conn.execute(
        "SELECT subject_type, subject_id, slot, note FROM gen_spec_locks WHERE project_id=?", (project_id,))]
    return out, locks


# =============================================================================================== import
@dataclass
class PackReport:
    dry_run: bool
    project_id: int | None = None
    created_project: bool = False
    story: dict[str, dict[str, int]] = field(default_factory=dict)
    specs: dict[str, int] = field(default_factory=dict)
    assets: dict[str, int] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    unassigned: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"dry_run": self.dry_run, "project_id": self.project_id, "created_project": self.created_project,
                "story": self.story, "specs": self.specs, "assets": self.assets, "warnings": self.warnings,
                "unassigned": self.unassigned}

    def warn(self, code: str, **params: Any) -> None:
        self.warnings.append({"code": code, **params})

    def bump(self, bucket: dict[str, int], key: str, n: int = 1) -> None:
        bucket[key] = bucket.get(key, 0) + n


def _safe_extract(zf: zipfile.ZipFile, dest: Path, max_bytes: int) -> None:
    total = 0
    root = dest.resolve()
    for info in zf.infolist():
        name = info.filename
        target = (dest / name).resolve()
        if name.startswith(("/", "\\")) or ".." in Path(name).parts or not target.is_relative_to(root):
            raise PackError("unsafe_path", path=name)
        if info.is_dir():
            continue
        total += info.file_size
        if total > max_bytes:
            raise PackError("too_large", limit=max_bytes)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise PackError("unreadable", path=path.name) from exc


class _Importer:
    def __init__(self, settings: Settings, db: Database, root: Path, manifest: Mapping[str, Any], *,
                 into: int | None, policy: str, only: str, dry_run: bool, name: str) -> None:
        self.settings, self.db, self.root, self.manifest = settings, db, root, manifest
        self.into, self.policy, self.only, self.dry, self.name = into, policy, only, dry_run, name
        self.ledger = Ledger(db, settings)
        self.specs = SpecStore(db, settings, self.ledger)
        self.story = StoryStore(db)
        self.report = PackReport(dry_run=dry_run)
        self.pid = 0
        self.maps: dict[str, dict[int, int]] = {t: {} for t in _STORY}   # table -> pack id -> local id
        self.asset_map: dict[str, str] = {}                                # pack asset id -> local asset id
        self.shot_by_key: dict[tuple[int, int, int, int], int] = {}

    # -- project ----------------------------------------------------------------------------------
    def project(self) -> None:
        store = ProjectStore(self.db, self.settings)
        if self.into is not None:
            store.get(self.into)
            self.pid = self.into
            self.report.project_id = self.pid
            return
        p = self.manifest.get("project", {})
        base = dict(name=p.get("name") or self.name, genre=p.get("genre", ""), description=p.get("description", ""),
                    content_language=p.get("content_language", ""))
        try:
            created = store.create(**base, delivery_profile=p.get("delivery_profile", ""), kind=p.get("kind", ""),
                                   pipeline=p.get("pipeline", ""), brief=p.get("brief") or None)
        except ConfigError as exc:
            self.report.warn("plan_unavailable", detail=str(exc))
            created = store.create(**base)
        self.pid = created.id
        self.report.project_id, self.report.created_project = self.pid, True
        keep = {k: v for k, v in (p.get("settings") or {}).items() if k in ("compliance", "models", "edit")}
        if keep:
            with contextlib.suppress(ConfigError):
                store.set_settings(self.pid, keep)

    # -- story ------------------------------------------------------------------------------------
    def _rows(self, table: str) -> list[dict[str, Any]]:
        path = self.root / "story" / f"{table}.json"
        return _read_json(path) if path.is_file() else []

    def _key(self, table: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
        return {
            "seasons": lambda: (row["number"],), "episodes": lambda: (row["number"],),
            "characters": lambda: (row["name"],), "scenes": lambda: (row["name"],),
            "scripts": lambda: (row["episode_no"],), "music_themes": lambda: (row["name"],),
            "sound_effects": lambda: (row["name"],), "props": lambda: (row["name"],),
            "character_looks": lambda: (row.get("_character"), row["label"], row["from_episode"],
                                        row["from_act"], row["from_scene"]),
            "shots": lambda: (row["_episode"], row["act_number"], row["scene_number"], row["shot_number"]),
        }[table]()

    def _local(self, table: str) -> dict[tuple[Any, ...], dict[str, Any]]:
        s, p = self.story, self.pid
        rows = {
            "seasons": s.list_seasons, "episodes": s.list_episodes, "characters": s.list_characters,
            "scenes": s.list_locations, "scripts": s.list_scripts, "music_themes": s.list_music,
            "sound_effects": s.list_sfx, "props": s.list_props, "character_looks": s.list_looks,
            "shots": s.list_shots,
        }[table](p)
        chars = {c["id"]: c["name"] for c in s.list_characters(p)} if table == "character_looks" else {}
        eps = {e["id"]: e["number"] for e in s.list_episodes(p)} if table == "shots" else {}
        out = {}
        for r in rows:
            if table == "character_looks":
                r = {**r, "_character": chars.get(r["character_id"])}
            if table == "shots":
                r = {**r, "_episode": eps.get(r["episode_id"])}
            out[self._key(table, r)] = r
        return out

    def story_import(self, create: bool) -> None:
        for table in _STORY:
            rows = self._rows(table)
            if not rows:
                continue
            bucket = self.report.story.setdefault(table, {})
            local = self._local(table)
            for row in rows:
                key = self._key(table, row)
                data = {k: v for k, v in row.items() if k in _FIELDS[table]}
                extra: dict[str, Any] = {}
                if table == "character_looks":
                    cid = next((c["id"] for c in self.story.list_characters(self.pid)
                                if c["name"] == row["_character"]), None)
                    if cid is None:
                        self.report.warn("look_without_character", character=row.get("_character"))
                        continue
                    data["character_id"] = cid
                if table == "shots":
                    eid = self.maps["episodes"].get(row["_episode"]) or next(
                        (e["id"] for k, e in self._local("episodes").items() if k == (row["_episode"],)), None)
                    if eid is None:
                        self.report.warn("shot_without_episode", episode=row["_episode"])
                        continue
                    extra["episode_id"] = eid
                    sid = next((s["id"] for k, s in self._local("scripts").items() if k == (row["_script"],)), None) \
                        if row.get("_script") is not None else None
                    if sid:
                        data["script_id"] = sid
                found = local.get(key)
                if found is not None:
                    self.maps[table][row["_id"]] = found["id"]
                    self._shot_key(table, found["id"], row)
                    changed = {k: v for k, v in data.items() if found.get(k) != v}
                    if not changed:
                        self.report.bump(bucket, "unchanged")
                    elif self.policy == "new_version" and not found.get("locked") and create:
                        self._update(table, found["id"], changed)
                        self.report.bump(bucket, "updated")
                    else:
                        self.report.bump(bucket, "skipped")
                    continue
                if not create:
                    self.report.bump(bucket, "missing")
                    continue
                new_id = self.story._insert(table, self.pid, data, **extra)
                self.maps[table][row["_id"]] = new_id
                self._shot_key(table, new_id, row)
                self.report.bump(bucket, "created")

    def _update(self, table: str, row_id: int, changed: Mapping[str, Any]) -> None:
        self.story._update(table, row_id, changed)

    def _shot_key(self, table: str, local_id: int, row: Mapping[str, Any]) -> None:
        if table == "shots":
            self.shot_by_key[(row["_episode"], row["act_number"], row["scene_number"], row["shot_number"])] = local_id

    # -- id remapping -----------------------------------------------------------------------------
    def subject(self, subject_type: str, subject_id: str) -> str | None:
        """The local subject id for a pack subject id (None = the row is not in this project)."""
        table = _SUBJECT_TABLE.get(subject_type.split(".", 1)[0])
        if table is None or not subject_id:
            return subject_id
        head, _, idx = subject_id.partition("#")
        if not head.isdigit():
            return subject_id
        new = self.maps[table].get(int(head))
        if new is None:
            return None
        return f"{new}#{idx}" if idx else str(new)

    def slot(self, slot: str) -> str | None:
        m = _SLOT_SUBJECT.match(slot)
        if not m:
            return slot
        new = self.subject(m.group(1), m.group(2))
        return None if new is None else f"{m.group(1)}:{new}{m.group(3)}"

    def refs(self, value: Any) -> Any:
        if isinstance(value, str):
            m = _REF.match(value)
            return f"asset:{self.asset_map.get(m.group(1), m.group(1))}" if m else value
        if isinstance(value, list):
            return [self.refs(v) for v in value]
        if isinstance(value, dict):
            return {k: self.refs(v) for k, v in value.items()}
        return value

    # -- assets -----------------------------------------------------------------------------------
    def assets_import(self) -> None:
        path = self.root / "assets" / "assets.json"
        if not path.is_file():
            return
        bucket = self.report.assets
        slug = self.db.conn.execute("SELECT slug FROM projects WHERE id=?", (self.pid,)).fetchone()["slug"]
        for e in _read_json(path):
            src = self.root / e["file"]
            if not src.is_file():
                self.report.bump(bucket, "missing")
                self.report.warn("file_missing", path=e["file"])
                continue
            if _sha(src) != e["sha256"]:
                self.report.bump(bucket, "corrupt")
                self.report.warn("file_corrupt", path=e["file"])
                continue
            shot_id = None
            if e.get("shot"):
                shot_id = self.shot_by_key.get(tuple(e["shot"]))
                if shot_id is None:
                    self.report.bump(bucket, "unmapped")
                    self.report.warn("shot_not_found", shot="/".join(str(x) for x in e["shot"]))
                    continue
            slot = self.slot(e["slot"])
            if slot is None:
                self.report.bump(bucket, "unmapped")
                self.report.warn("subject_not_found", slot=e["slot"])
                continue
            rec = self._register(slug, src, e["kind"], slot, shot_id, e["sha256"], bucket,
                                 current=bool(e.get("current", True)),
                                 info={k: e.get(k) for k in ("duration_sec", "width", "height", "fps", "has_audio")},
                                 metadata=e.get("metadata") or {},
                                 provenance={"schema": "storyagent.provenance.v1", "origin": "pack",
                                             "pack": {"name": self.name,
                                                      "exported_at": self.manifest.get("exported_at")},
                                             "source": e.get("provenance") or {}})
            if rec:
                self.asset_map[e["id"]] = rec

    def _register(self, slug: str, src: Path, kind: str, slot: str, shot_id: int | None, sha: str,
                  bucket: dict[str, int], *, current: bool, info: Mapping[str, Any], metadata: Mapping[str, Any],
                  provenance: Mapping[str, Any]) -> str | None:
        group = self.db.conn.execute(
            "SELECT id, sha256, is_current FROM media_assets WHERE project_id=? AND COALESCE(shot_id,0)=? AND kind=? "
            "AND slot=? AND trashed_at IS NULL", (self.pid, shot_id or 0, kind, slot)).fetchall()
        same = next((r for r in group if r["sha256"] == sha), None)
        if same is not None:
            if current and self.policy == "new_version" and not same["is_current"]:
                self.ledger.adopt(same["id"])
                self.report.bump(bucket, "adopted")
            else:
                self.report.bump(bucket, "duplicate")
            return same["id"]
        has_current = any(r["is_current"] for r in group)
        adopt = current and (self.policy == "new_version" or not has_current)
        dest = self.settings.project_media_dir(slug, kind, shot_id) / f"{sha[:16]}-{len(group) + 1}{src.suffix.lower()}"
        if not self.dry:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        meta = dict(info)
        unmeasured = any(meta.get(k) is None for k in ("duration_sec", "width", "height", "fps"))
        if not self.dry and unmeasured and src.suffix.lower() in _MEDIA_EXT:
            measured = probe(self.settings, dest)
            if measured is not None:
                meta = {"duration_sec": measured.duration_sec, "width": measured.width, "height": measured.height,
                        "fps": measured.fps, "has_audio": measured.has_audio}
        asset = self.ledger.register_asset(
            NewAsset(kind=kind, slot=slot, path=str(dest.relative_to(self.settings.home)), sha256=sha,
                     bytes=src.stat().st_size, duration_sec=meta.get("duration_sec"), width=meta.get("width"),
                     height=meta.get("height"), fps=meta.get("fps"), has_audio=meta.get("has_audio"),
                     metadata=dict(metadata), provenance=dict(provenance)),
            project_id=self.pid, shot_id=shot_id, adopt=adopt)
        self.report.bump(bucket, "imported" if adopt else "candidate")
        return asset.id

    # -- specs ------------------------------------------------------------------------------------
    def specs_import(self) -> None:
        path = self.root / "specs" / "specs.json"
        if not path.is_file():
            return
        data = _read_json(path)
        bucket = self.report.specs
        for s in data["specs"]:
            sid = self.subject(s["subject_type"], s["subject_id"])
            slot = self.slot(s["slot"]) if s["slot"] != "main" else "main"
            if sid is None or slot is None:
                self.report.bump(bucket, "unmapped")
                self.report.warn("subject_not_found", slot=f'{s["subject_type"]}/{s["subject_id"]}')
                continue
            if s["model_ref"]:
                try:
                    self.settings.model(s["model_ref"])
                except ConfigError:
                    self.report.warn("model_unknown", model=s["model_ref"])
            deps = []
            for d in s["depends_on"]:
                dsid = self.subject(d["subject_type"], str(d["subject_id"]))
                if dsid is not None:
                    deps.append({**d, "subject_id": dsid})
            fields = {"model_ref": s["model_ref"], "prompt": s["prompt"], "negative_prompt": s["negative_prompt"],
                      "text_payload": s["text_payload"], "params": self.refs(s["params"]), "seed": s["seed"],
                      "refs": self.refs(s["refs"]), "depends_on": deps}
            cur = self.specs.current(self.pid, s["subject_type"], sid, slot)
            kind = _kind(s["kind"])
            if cur is None:
                self.specs._insert(self.pid, s["subject_type"], sid, slot, s["version"], s["parent_version"], kind,
                                   fields["model_ref"], fields["prompt"], fields["negative_prompt"],
                                   fields["text_payload"], fields["params"], fields["seed"], fields["refs"],
                                   fields["depends_on"], s["author"], s["note"] or f"pack {self.name}")
                self.report.bump(bucket, "created")
            elif all(getattr(cur, k) == v for k, v in fields.items()):
                self.report.bump(bucket, "unchanged")
            elif self.policy == "new_version" and s["version"] >= cur.version:
                try:
                    self.specs.edit(self.pid, s["subject_type"], sid, slot, note=f"pack {self.name}", **fields)
                    self.report.bump(bucket, "updated")
                except SpecError:
                    self.report.bump(bucket, "skipped")
                    self.report.warn("spec_locked", spec=f'{s["subject_type"]}/{sid}')
            else:
                self.report.bump(bucket, "skipped")
        for lk in data.get("locks", []):
            sid = self.subject(lk["subject_type"], lk["subject_id"])
            if sid is not None and not self.specs.is_locked(self.pid, lk["subject_type"], sid, lk["slot"]):
                self.specs.lock(self.pid, lk["subject_type"], sid, lk["slot"], lk.get("note", ""))
        self._link_assets(data["specs"])

    def _link_assets(self, specs: Sequence[Mapping[str, Any]]) -> None:
        """Imported assets keep the spec version they were made from, so a later spec edit marks them out of date."""
        path = self.root / "assets" / "assets.json"
        if not path.is_file():
            return
        by_id = {s["id"]: s for s in specs}
        for e in _read_json(path):
            local = self.asset_map.get(e["id"])
            link = e.get("spec")
            s = by_id.get(link["id"]) if link else None
            sid = self.subject(s["subject_type"], s["subject_id"]) if s else None
            if not (local and s and sid is not None):
                continue
            slot = self.slot(s["slot"]) if s["slot"] != "main" else "main"
            cur = self.specs.versions(self.pid, s["subject_type"], sid, slot or "main")
            match = next((v for v in cur if v.version == link["version"]), None)
            if match is not None:
                self.db.conn.execute("UPDATE media_assets SET spec_id=?, spec_version=? WHERE id=?",
                                     (match.id, match.version, local))


def _kind(value: str) -> Any:
    from storyagent.domain import MediaKind

    return MediaKind(value)


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / MANIFEST
    if not path.is_file():
        raise PackError("no_manifest")
    manifest = _read_json(path)
    if manifest.get("schema") != SCHEMA:
        raise PackError("schema", found=str(manifest.get("schema")))
    return manifest


def import_pack(
    settings: Settings, db: Database, src: Path, *, into: int | None = None, policy: str = "skip",
    only: str = "all", dry_run: bool = False,
) -> PackReport:
    """Read the pack ``src`` (directory or .zip) into project ``into`` (default: a new project from the manifest)."""
    if policy not in POLICIES:
        raise PackError("policy", policy=policy, allowed=", ".join(POLICIES))
    if only not in SCOPES:
        raise PackError("scope", scope=only, allowed=", ".join(SCOPES))
    if not src.exists():
        raise PackError("not_found", path=str(src))
    tmp: Path | None = None
    try:
        if src.is_file():
            if src.suffix.lower() != ".zip":
                raise PackError("not_a_pack", path=str(src))
            tmp = Path(tempfile.mkdtemp(prefix="pack-in-"))
            limit = int(float(settings.runtime.get("pack_max_gib", 50)) * (1 << 30))
            try:
                with zipfile.ZipFile(src) as zf:
                    _safe_extract(zf, tmp, limit)
            except zipfile.BadZipFile as exc:
                raise PackError("not_a_pack", path=str(src)) from exc
            root = tmp
        else:
            root = src
        manifest = _load_manifest(root)
        imp = _Importer(settings, db, root, manifest, into=into, policy=policy, only=only, dry_run=dry_run,
                        name=src.stem)
        listed = {f["path"]: f for f in manifest.get("files", [])}
        for rel, f in listed.items():
            p = root / rel
            if p.is_file() and rel.startswith(("story/", "specs/")) and _sha(p) != f["sha256"]:
                raise PackError("corrupt", path=rel)
        try:
            with db.transaction():
                imp.project()
                imp.story_import(create=only in ("all", "story"))
                if only in ("all", "assets"):
                    imp.assets_import()
                    imp.specs_import()
                if dry_run:
                    raise _DryRun
        except _DryRun:
            imp.report.project_id = None if into is None else into
        return imp.report
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================================== loose folders
def import_folder(
    settings: Settings, db: Database, folder: Path, project_id: int, *, policy: str = "skip", dry_run: bool = False,
) -> PackReport:
    """Import plain files by name: the first ``loose_rules`` entry whose ``pattern`` matches decides kind and slot."""
    if policy not in POLICIES:
        raise PackError("policy", policy=policy, allowed=", ".join(POLICIES))
    if not folder.is_dir():
        raise PackError("not_found", path=str(folder))
    rules = [(re.compile(r["pattern"], re.IGNORECASE), r) for r in settings.runtime.get("loose_rules", [])]
    imp = _Importer(settings, db, folder, {}, into=project_id, policy=policy,
                    only="assets", dry_run=dry_run, name=folder.name)
    imp.pid = project_id
    imp.report.project_id = project_id
    slug = db.conn.execute("SELECT slug FROM projects WHERE id=?", (project_id,)).fetchone()["slug"]
    shots = imp.story.list_shots(project_id)
    chars = {c["name"].lower(): c["id"] for c in imp.story.list_characters(project_id)}
    music = imp.story.list_music(project_id)
    try:
        with db.transaction():
            for path in sorted(p for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".")):
                target = _match_loose(path.name, rules, shots, chars, music)
                if target is None:
                    imp.report.unassigned.append(path.relative_to(folder).as_posix())
                    continue
                kind, slot, shot_id = target
                imp._register(slug, path, kind, slot, shot_id, _sha(path), imp.report.assets, current=True,
                              info={}, metadata={"original_name": path.name},
                              provenance={"schema": "storyagent.provenance.v1", "origin": "import",
                                          "original_name": path.name})
            if dry_run:
                raise _DryRun
    except _DryRun:
        pass
    return imp.report


def _match_loose(
    name: str, rules: Sequence[tuple[re.Pattern[str], Mapping[str, Any]]], shots: Sequence[Mapping[str, Any]],
    chars: Mapping[str, int], music: Sequence[Mapping[str, Any]],
) -> tuple[str, str, int | None] | None:
    for pattern, rule in rules:
        m = pattern.match(name)
        if not m:
            continue
        target, slot = rule.get("target", "none"), str(rule.get("slot", "main"))
        if target == "shot":
            n = int(m.group("n"))
            if not 1 <= n <= len(shots):
                return None
            return rule["kind"], slot, int(shots[n - 1]["id"])
        if target == "character":
            cid = chars.get(m.group("name").strip().lower())
            return None if cid is None else (rule["kind"], slot.format(id=cid), None)
        if target == "music":
            return None if not music else (rule["kind"], slot.format(id=music[0]["id"]), None)
        return rule["kind"], slot, None
    return None
