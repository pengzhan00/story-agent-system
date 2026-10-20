"""One-way import from the v1 ``story_agents.db`` into the v2 schema.

The legacy file is opened strictly read-only (``mode=ro``) and is never written to.
Re-running is idempotent at project granularity: a project whose legacy id is already
present is skipped as a whole.

What is NOT imported: render_jobs / audio_assets / asset_versions / pipeline_runs /
task_queue / logs — the v2 media ledger supersedes them. Each shot's old
``video_path``/``audio_path`` is kept in ``legacy_*_path`` for later ledger back-fill.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storyagent.domain import slugify

from .connection import Database


class LegacyImportError(RuntimeError):
    """``key`` is an i18n key (``error.legacy.*``); ``params`` its placeholders."""

    def __init__(self, key: str, **params: object):
        super().__init__(f"{key} {params}")
        self.key = key
        self.params = params


@dataclass
class ImportReport:
    projects: int = 0
    skipped_projects: int = 0
    scripts: int = 0
    characters: int = 0
    scenes: int = 0
    music_themes: int = 0
    sound_effects: int = 0
    episodes: int = 0
    shots: int = 0
    notes: list[str] = field(default_factory=list)


def open_legacy_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise LegacyImportError("error.legacy.not_found", path=str(path))
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return conn
    except sqlite3.Error as exc:
        raise LegacyImportError("error.legacy.unreadable", path=str(path)) from exc


def _g(row: sqlite3.Row, key: str, default: Any = "") -> Any:
    return row[key] if key in row.keys() and row[key] is not None else default  # noqa: SIM118


def _json_text(value: Any, default: str) -> str:
    """Return valid JSON text; fall back to ``default`` for empty/invalid input."""
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    try:
        json.loads(value)
        return str(value)
    except (TypeError, ValueError):
        return default


def _unique_slug(conn: sqlite3.Connection, name: str, legacy_id: int) -> str:
    base = slugify(name, fallback=f"project-{legacy_id}")
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM projects WHERE slug=?", (slug,)).fetchone():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _tables(legacy: sqlite3.Connection) -> set[str]:
    return {r[0] for r in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def import_legacy(legacy_path: Path, db: Database) -> ImportReport:
    legacy = open_legacy_readonly(legacy_path)
    try:
        have = _tables(legacy)
        if "projects" not in have:
            raise LegacyImportError("error.legacy.unreadable", path=str(legacy_path))
        report = ImportReport()
        for prow in legacy.execute("SELECT * FROM projects ORDER BY id").fetchall():
            with db.transaction() as conn:
                _import_project(legacy, have, conn, prow, report)
        return report
    finally:
        legacy.close()


def _import_project(
    legacy: sqlite3.Connection,
    have: set[str],
    conn: sqlite3.Connection,
    prow: sqlite3.Row,
    rep: ImportReport,
) -> None:
    old_pid = int(prow["id"])
    if conn.execute("SELECT 1 FROM projects WHERE legacy_id=?", (old_pid,)).fetchone():
        rep.skipped_projects += 1
        return

    name = _g(prow, "name")
    cur = conn.execute(
        """INSERT INTO projects(slug, name, description, genre, genre_tags, tone_tags,
               emotion_arc, format, aspect_ratio, episode_count, act_count, status, legacy_id,
               created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                   COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ','now')))""",
        (
            _unique_slug(conn, name, old_pid), name, _g(prow, "description"), _g(prow, "genre"),
            _json_text(_g(prow, "genre_tags", None), "[]"),
            _json_text(_g(prow, "tone_tags", None), "[]"),
            _g(prow, "emotion_arc"), _g(prow, "format", "short_drama"),
            _g(prow, "aspect_ratio", "9:16"), int(_g(prow, "episode_count", 1) or 1),
            int(_g(prow, "act_count", 4) or 4), _g(prow, "status", "draft"), old_pid,
            _g(prow, "created_at", None), _g(prow, "updated_at", None),
        ),
    )
    pid = int(cur.lastrowid or 0)
    rep.projects += 1

    script_map: dict[int, int] = {}
    if "scripts" in have:
        for r in legacy.execute("SELECT * FROM scripts WHERE project_id=? ORDER BY id", (old_pid,)):
            c = conn.execute(
                """INSERT INTO scripts(project_id, episode_no, title, synopsis, acts, total_scenes,
                       word_count, status) VALUES (?,?,?,?,?,?,?,?)""",
                (pid, len(script_map) + 1, _g(r, "title"), _g(r, "synopsis"),
                 _json_text(_g(r, "acts", None), "[]"), int(_g(r, "total_scenes", 0) or 0),
                 int(_g(r, "word_count", 0) or 0), _g(r, "status", "draft")),
            )
            script_map[int(r["id"])] = int(c.lastrowid or 0)
            rep.scripts += 1

    if "characters" in have:
        for r in legacy.execute("SELECT * FROM characters WHERE project_id=? ORDER BY id", (old_pid,)):
            c = conn.execute(
                """INSERT OR IGNORE INTO characters(project_id, name, role, age, gender, appearance,
                       personality, background, relationships, prompt_template, voice_profile,
                       lora_ref, ref_images)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, _g(r, "name"), _g(r, "role"), _g(r, "age"), _g(r, "gender"),
                 _g(r, "appearance"), _g(r, "personality"), _g(r, "background"),
                 _json_text(_g(r, "relationships", None), "[]"), _g(r, "prompt_template"),
                 _g(r, "voice_profile"), _g(r, "lora_ref"),
                 _json_text(_g(r, "ip_ref_images", None), "[]")),
            )
            if c.rowcount:
                rep.characters += 1
            else:
                rep.notes.append(f"project {old_pid}: duplicate character {_g(r, 'name')!r} skipped")

    if "scenes" in have:
        for r in legacy.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY id", (old_pid,)):
            c = conn.execute(
                """INSERT OR IGNORE INTO scenes(project_id, name, description, lighting,
                       color_palette, atmosphere, prompt_template, lora_ref, ref_images)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (pid, _g(r, "name"), _g(r, "description"), _g(r, "lighting"),
                 _g(r, "color_palette"), _g(r, "atmosphere"), _g(r, "prompt_template"),
                 _g(r, "lora_ref"), _json_text(_g(r, "ref_images", None), "[]")),
            )
            if c.rowcount:
                rep.scenes += 1
            else:
                rep.notes.append(f"project {old_pid}: duplicate scene {_g(r, 'name')!r} skipped")

    if "music_themes" in have:
        for r in legacy.execute("SELECT * FROM music_themes WHERE project_id=? ORDER BY id", (old_pid,)):
            conn.execute(
                """INSERT INTO music_themes(project_id, name, kind, mood, tempo, instruments,
                       key_signature, description, gen_prompt) VALUES (?,?,?,?,?,?,?,?,?)""",
                (pid, _g(r, "name"), _g(r, "type", "bgm"), _g(r, "mood"), _g(r, "tempo"),
                 _g(r, "instruments"), _g(r, "key_signature"), _g(r, "description"),
                 _g(r, "prompt_for_gen")),
            )
            rep.music_themes += 1

    if "sound_effects" in have:
        for r in legacy.execute("SELECT * FROM sound_effects WHERE project_id=? ORDER BY id", (old_pid,)):
            conn.execute(
                """INSERT INTO sound_effects(project_id, name, category, description, tags)
                   VALUES (?,?,?,?,?)""",
                (pid, _g(r, "name"), _g(r, "category"), _g(r, "description"), _g(r, "tags")),
            )
            rep.sound_effects += 1

    episode_map: dict[int, int] = {}

    def episode_for(old_episode_id: int) -> int:
        """Map legacy episode ids (0 / dangling => episode 1) onto v2 episode rows."""
        if old_episode_id in episode_map:
            return episode_map[old_episode_id]
        row = None
        if old_episode_id and "episodes" in have:
            row = legacy.execute("SELECT * FROM episodes WHERE id=?", (old_episode_id,)).fetchone()
        number = int(_g(row, "number", 1) or 1) if row is not None else 1
        existing = conn.execute(
            "SELECT id FROM episodes WHERE project_id=? AND number=?", (pid, number)
        ).fetchone()
        if existing:
            eid = int(existing["id"])
        else:
            c = conn.execute(
                "INSERT INTO episodes(project_id, number, title, summary, status) VALUES (?,?,?,?,?)",
                (pid, number, _g(row, "title") if row is not None else "",
                 _g(row, "summary") if row is not None else "",
                 _g(row, "status", "draft") if row is not None else "draft"),
            )
            eid = int(c.lastrowid or 0)
            rep.episodes += 1
        episode_map[old_episode_id] = eid
        return eid

    if "episodes" in have:
        for r in legacy.execute("SELECT id FROM episodes WHERE project_id=? ORDER BY number", (old_pid,)):
            episode_for(int(r["id"]))

    if "shots" in have:
        for r in legacy.execute("SELECT * FROM shots WHERE project_id=? ORDER BY id", (old_pid,)):
            eid = episode_for(int(_g(r, "episode_id", 0) or 0))
            act, scn, shot = (int(_g(r, k, 1) or 1) for k in ("act_number", "scene_number", "shot_number"))
            while conn.execute(
                "SELECT 1 FROM shots WHERE episode_id=? AND act_number=? AND scene_number=? AND shot_number=?",
                (eid, act, scn, shot),
            ).fetchone():
                shot += 1  # legacy data allowed duplicates; keep every shot
                rep.notes.append(f"project {old_pid}: shot renumbered to {act}/{scn}/{shot}")
            conn.execute(
                """INSERT INTO shots(project_id, episode_id, script_id, act_number, scene_number,
                       shot_number, location, shot_type, mood, time_of_day, weather, characters,
                       narration, dialogue, camera_notes, visual_prompt, payload_version,
                       render_payload, status, locked, error, legacy_video_path, legacy_audio_path)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'legacy', ?,?,?,?,?,?)""",
                (pid, eid, script_map.get(int(_g(r, "script_id", 0) or 0)), act, scn, shot,
                 _g(r, "location"), _g(r, "shot_type"), _g(r, "mood"), _g(r, "time_of_day"),
                 _g(r, "weather"), _json_text(_g(r, "characters", None), "[]"),
                 _g(r, "narration"), _json_text(_g(r, "dialogue", None), "[]"),
                 _g(r, "camera_notes"), _g(r, "visual_prompt"),
                 _json_text(_g(r, "render_payload", None), "{}"), _map_shot_status(_g(r, "status")),
                 1 if _g(r, "locked", 0) else 0, _g(r, "error"),
                 _g(r, "video_path"), _g(r, "audio_path")),
            )
            rep.shots += 1


def _map_shot_status(old: str) -> str:
    return {"rendering": "generating", "rendered": "generated", "qc_failed": "failed"}.get(old, old or "draft")
