"""The story domain store: characters, locations, scripts, episodes, shots, music themes, sound effects.

Records are plain dicts (JSON columns decoded) so the API, the workflow and the CLI share them without an ORM.
Everything user-editable goes through here; media generation never touches these tables (it only reads them, via specs).
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from storyagent.db import Database
from storyagent.domain import ShotStatus
from storyagent.util import now_iso


class StoryError(ValueError):
    pass


class StoryNotFound(StoryError, LookupError):
    pass


class ShotLocked(StoryError):
    pass


_JSON = {
    "characters": ("relationships", "ref_images"),
    "scenes": ("ref_images",),
    "scripts": ("acts",),
    "shots": ("characters", "dialogue", "render_payload"),
    "music_themes": (),
    "sound_effects": (),
    "episodes": (),
    "seasons": (),
}
_FIELDS: dict[str, tuple[str, ...]] = {
    "characters": ("name", "role", "age", "gender", "appearance", "personality", "background", "relationships",
                   "prompt_template", "voice_profile", "voice_ref_audio", "voice_ref_text", "voice_ref_consent",
                   "voice_consent_note", "lora_ref", "ref_images", "introduced_episode", "locked"),
    "scenes": ("name", "description", "lighting", "color_palette", "atmosphere", "prompt_template", "lora_ref",
               "ref_images", "keyframe_mode"),
    "scripts": ("episode_no", "title", "synopsis", "acts", "total_scenes", "word_count", "status"),
    "shots": ("script_id", "act_number", "scene_number", "shot_number", "location", "shot_type", "mood", "time_of_day",
              "weather", "characters", "narration", "dialogue", "camera_notes", "visual_prompt", "payload_version",
              "render_payload", "status", "locked", "error", "treatment"),
    "music_themes": ("name", "kind", "mood", "tempo", "instruments", "key_signature", "description", "gen_prompt",
                     "lyrics", "duration_sec", "scene_ref"),
    "sound_effects": ("name", "category", "description", "tags"),
    "episodes": ("number", "title", "summary", "status", "season", "number_in_season", "hook", "cliffhanger"),
    "seasons": ("number", "title", "summary", "planned_episodes", "target_sec", "status"),
}
_CHOICES = {("shots", "treatment"): ("auto", "video", "still"),
            ("scenes", "keyframe_mode"): ("auto", "compose", "text")}
_PARENT_ORDER = {"shots": "act_number, scene_number, shot_number, id", "scripts": "episode_no, id",
                 "episodes": "number", "seasons": "number", "characters": "id", "scenes": "id", "music_themes": "id",
                 "sound_effects": "id"}
_HAS_UPDATED = {"characters", "scenes", "scripts", "shots", "episodes", "seasons"}


def _decode(table: str, row: Any) -> dict[str, Any]:
    out = dict(row)
    for col in _JSON[table]:
        raw = out.get(col)
        out[col] = json.loads(raw) if raw else ({} if col == "render_payload" else [])
    if table == "characters":
        out["voice_ref_consent"] = bool(out.get("voice_ref_consent"))
        out["locked"] = bool(out.get("locked"))
    if table == "shots":
        out["locked"] = bool(out.get("locked"))
    return out


def _encode(table: str, key: str, value: Any) -> Any:
    if key in _JSON[table]:
        return json.dumps(value, ensure_ascii=False)
    if key in ("locked", "voice_ref_consent"):
        return int(bool(value))
    return value


class StoryStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- generic ------------------------------------------------------------------------------
    def _get(self, table: str, row_id: int, project_id: int | None = None) -> dict[str, Any]:
        q, args = f"SELECT * FROM {table} WHERE id=?", [row_id]
        if project_id is not None:
            q += " AND project_id=?"
            args.append(project_id)
        row = self.db.conn.execute(q, args).fetchone()
        if row is None:
            raise StoryNotFound(f"unknown {table[:-1] if table.endswith('s') else table} {row_id}")
        return _decode(table, row)

    def _list(self, table: str, project_id: int, where: str = "", args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            f"SELECT * FROM {table} WHERE project_id=? {where} ORDER BY {_PARENT_ORDER[table]}", (project_id, *args)
        ).fetchall()
        return [_decode(table, r) for r in rows]

    def _check(self, table: str, data: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(data) - set(_FIELDS[table])
        if unknown:
            raise StoryError(f"unknown {table} fields: {sorted(unknown)}")
        for (t, col), allowed in _CHOICES.items():
            if t == table and col in data and data[col] not in allowed:
                raise StoryError(f"{col} must be one of {list(allowed)}")
        return dict(data)

    def _insert(self, table: str, project_id: int, data: Mapping[str, Any], **extra: Any) -> int:
        d = {**self._check(table, data), **extra}
        cols = ["project_id", *d]
        cur = self.db.conn.execute(
            f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [project_id, *(_encode(table, k, v) for k, v in d.items())],
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def _update(self, table: str, row_id: int, data: Mapping[str, Any]) -> None:
        d = self._check(table, data)
        if not d:
            return
        sets = [f"{k}=?" for k in d] + (["updated_at=?"] if table in _HAS_UPDATED else [])
        args = [_encode(table, k, v) for k, v in d.items()] + ([now_iso()] if table in _HAS_UPDATED else [])
        self.db.conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", [*args, row_id])

    def _delete(self, table: str, row_id: int) -> None:
        self._get(table, row_id)
        self.db.conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))

    # -- characters ---------------------------------------------------------------------------
    def list_characters(self, project_id: int) -> list[dict[str, Any]]:
        return self._list("characters", project_id)

    def get_character(self, character_id: int) -> dict[str, Any]:
        return self._get("characters", character_id)

    def find_character(self, project_id: int, name: str) -> dict[str, Any] | None:
        row = self.db.conn.execute(
            "SELECT * FROM characters WHERE project_id=? AND name=?", (project_id, name.strip())
        ).fetchone()
        return _decode("characters", row) if row else None

    def create_character(self, project_id: int, data: Mapping[str, Any]) -> dict[str, Any]:
        name = str(data.get("name", "")).strip()
        if not name:
            raise StoryError("a character needs a name")
        if self.find_character(project_id, name):
            raise StoryError(f"character {name!r} already exists")
        return self.get_character(self._insert("characters", project_id, {**data, "name": name}))

    def update_character(self, character_id: int, data: Mapping[str, Any]) -> dict[str, Any]:
        with self.db.transaction():
            current = self.get_character(character_id)
            if "name" in data:
                name = str(data["name"]).strip()
                other = self.find_character(current["project_id"], name)
                if not name or (other and other["id"] != character_id):
                    raise StoryError(f"character name {name!r} is empty or taken")
                data = {**data, "name": name}
            self._update("characters", character_id, data)
        return self.get_character(character_id)

    def delete_character(self, character_id: int) -> None:
        self._delete("characters", character_id)

    # -- locations (the ``scenes`` table: reusable places) --------------------------------------------
    def list_locations(self, project_id: int) -> list[dict[str, Any]]:
        return self._list("scenes", project_id)

    def get_location(self, location_id: int) -> dict[str, Any]:
        return self._get("scenes", location_id)

    def find_location(self, project_id: int, name: str) -> dict[str, Any] | None:
        row = self.db.conn.execute(
            "SELECT * FROM scenes WHERE project_id=? AND name=?", (project_id, name.strip())
        ).fetchone()
        return _decode("scenes", row) if row else None

    def create_location(self, project_id: int, data: Mapping[str, Any]) -> dict[str, Any]:
        name = str(data.get("name", "")).strip()
        if not name:
            raise StoryError("a location needs a name")
        if self.find_location(project_id, name):
            raise StoryError(f"location {name!r} already exists")
        return self.get_location(self._insert("scenes", project_id, {**data, "name": name}))

    def update_location(self, location_id: int, data: Mapping[str, Any]) -> dict[str, Any]:
        self._get("scenes", location_id)
        self._update("scenes", location_id, data)
        return self.get_location(location_id)

    def delete_location(self, location_id: int) -> None:
        self._delete("scenes", location_id)

    # -- music themes, sound effects -----------------------------------------------------------------
    def list_music(self, project_id: int) -> list[dict[str, Any]]:
        return self._list("music_themes", project_id)

    def get_music(self, music_id: int) -> dict[str, Any]:
        return self._get("music_themes", music_id)

    def create_music(self, project_id: int, data: Mapping[str, Any]) -> dict[str, Any]:
        return self._get("music_themes", self._insert("music_themes", project_id, data))

    def update_music(self, music_id: int, data: Mapping[str, Any]) -> dict[str, Any]:
        self._get("music_themes", music_id)
        self._update("music_themes", music_id, data)
        return self._get("music_themes", music_id)

    def delete_music(self, music_id: int) -> None:
        self._delete("music_themes", music_id)

    def list_sfx(self, project_id: int) -> list[dict[str, Any]]:
        return self._list("sound_effects", project_id)

    def get_sfx(self, sfx_id: int) -> dict[str, Any]:
        return self._get("sound_effects", sfx_id)

    def create_sfx(self, project_id: int, data: Mapping[str, Any]) -> dict[str, Any]:
        return self._get("sound_effects", self._insert("sound_effects", project_id, data))

    def delete_sfx(self, sfx_id: int) -> None:
        self._delete("sound_effects", sfx_id)

    # -- episodes and scripts -------------------------------------------------------------------------
    def list_episodes(self, project_id: int) -> list[dict[str, Any]]:
        return self._list("episodes", project_id)

    def list_seasons(self, project_id: int) -> list[dict[str, Any]]:
        """Seasons with how many episodes each holds (a project always has season 1)."""
        self.ensure_season(project_id, 1)
        counts = {r["season"]: r["n"] for r in self.db.conn.execute(
            "SELECT season, COUNT(*) n FROM episodes WHERE project_id=? GROUP BY season", (project_id,))}
        return [{**s, "episodes": counts.get(s["number"], 0)} for s in self._list("seasons", project_id)]

    def ensure_season(self, project_id: int, number: int, *, title: str = "", summary: str = "") -> dict[str, Any]:
        row = self.db.conn.execute(
            "SELECT id FROM seasons WHERE project_id=? AND number=?", (project_id, number)).fetchone()
        if row is None:
            return self._get("seasons", self._insert("seasons", project_id, {
                "number": number, "title": title, "summary": summary}))
        if title or summary:
            self._update("seasons", row["id"], {k: v for k, v in (("title", title), ("summary", summary)) if v})
        return self._get("seasons", row["id"])

    def update_season(self, project_id: int, number: int, data: Mapping[str, Any]) -> dict[str, Any]:
        season = self.ensure_season(project_id, number)
        self._update("seasons", season["id"], data)
        return self._get("seasons", season["id"])

    def season_of(self, project_id: int, episode: int) -> int:
        """The season an episode belongs to (season 1 while the episode does not exist yet)."""
        row = self.db.conn.execute(
            "SELECT season FROM episodes WHERE project_id=? AND number=?", (project_id, episode)).fetchone()
        return int(row["season"]) if row else 1

    def ensure_episode(
        self, project_id: int, number: int, *, title: str = "", summary: str = "", season: int = 1,
    ) -> dict[str, Any]:
        row = self.db.conn.execute(
            "SELECT id FROM episodes WHERE project_id=? AND number=?", (project_id, number)
        ).fetchone()
        if row is None:
            self.ensure_season(project_id, season)
            in_season = self.db.conn.execute(
                "SELECT COUNT(*) n FROM episodes WHERE project_id=? AND season=?", (project_id, season)).fetchone()["n"]
            return self._get("episodes", self._insert("episodes", project_id, {
                "number": number, "title": title, "summary": summary, "status": "draft",
                "season": season, "number_in_season": in_season + 1}))
        if title or summary:
            self._update("episodes", row["id"], {k: v for k, v in (("title", title), ("summary", summary)) if v})
        return self._get("episodes", row["id"])

    def update_episode(self, episode_id: int, data: Mapping[str, Any]) -> dict[str, Any]:
        self._get("episodes", episode_id)
        self._update("episodes", episode_id, data)
        return self._get("episodes", episode_id)

    def get_script(self, project_id: int, episode_no: int = 1) -> dict[str, Any] | None:
        rows = self._list("scripts", project_id, "AND episode_no=?", (episode_no,))
        return rows[-1] if rows else None

    def list_scripts(self, project_id: int) -> list[dict[str, Any]]:
        return self._list("scripts", project_id)

    def save_script(
        self, project_id: int, episode_no: int, *, title: str, synopsis: str, acts: list[dict[str, Any]],
        status: str = "draft",
    ) -> dict[str, Any]:
        """One script row per (project, episode): saving again replaces it (earlier text lives in the audit log)."""
        scenes = sum(len(a.get("scenes", [])) for a in acts)
        words = len(json.dumps(acts, ensure_ascii=False))
        data = {"episode_no": episode_no, "title": title, "synopsis": synopsis, "acts": acts,
                "total_scenes": scenes, "word_count": words, "status": status}
        with self.db.transaction():
            existing = self.get_script(project_id, episode_no)
            if existing:
                self._update("scripts", existing["id"], data)
                return self._get("scripts", existing["id"])
            return self._get("scripts", self._insert("scripts", project_id, data))

    def update_script_scene(
        self, project_id: int, episode_no: int, act_number: int, scene_number: int, changes: Mapping[str, Any]
    ) -> dict[str, Any]:
        script = self.get_script(project_id, episode_no)
        if script is None:
            raise StoryNotFound(f"no script for episode {episode_no}")
        for act in script["acts"]:
            if act.get("number") != act_number:
                continue
            for scene in act.get("scenes", []):
                if scene.get("number") == scene_number:
                    scene.update(changes)
                    return self.save_script(project_id, episode_no, title=script["title"],
                                            synopsis=script["synopsis"], acts=script["acts"], status=script["status"])
        raise StoryNotFound(f"unknown scene act {act_number} scene {scene_number}")

    # -- shots ----------------------------------------------------------------------------------------
    def list_shots(self, project_id: int, episode_id: int | None = None) -> list[dict[str, Any]]:
        if episode_id is None:
            return self._list("shots", project_id)
        return self._list("shots", project_id, "AND episode_id=?", (episode_id,))

    def get_shot(self, shot_id: int) -> dict[str, Any]:
        return self._get("shots", shot_id)

    def update_shot(self, shot_id: int, data: Mapping[str, Any], *, force: bool = False) -> dict[str, Any]:
        shot = self.get_shot(shot_id)
        if shot["locked"] and not force and set(data) - {"locked"}:
            raise ShotLocked(f"shot {shot_id} is locked")
        if "status" in data and data["status"] not in {s.value for s in ShotStatus}:
            raise StoryError(f"unknown shot status {data['status']!r}")
        self._update("shots", shot_id, data)
        return self.get_shot(shot_id)

    def set_shot_lock(self, shot_id: int, locked: bool) -> dict[str, Any]:
        self.get_shot(shot_id)
        self._update("shots", shot_id, {"locked": locked})
        return self.get_shot(shot_id)

    def delete_shot(self, shot_id: int, *, force: bool = False) -> None:
        if self.get_shot(shot_id)["locked"] and not force:
            raise ShotLocked(f"shot {shot_id} is locked")
        self.db.conn.execute("DELETE FROM shots WHERE id=?", (shot_id,))

    def replace_episode_shots(
        self, project_id: int, episode_id: int, shots: Iterable[Mapping[str, Any]]
    ) -> tuple[list[int], list[int]]:
        """Re-plan one episode. A planned shot that lands on the position (act, scene, shot) of an existing unlocked
        shot updates that row in place, so its id - and the specs and assets hanging off it - survive; new positions
        are inserted, unlocked shots with no counterpart are removed. Locked shots are kept untouched and their
        position is not reused. Other episodes are never touched. Returns (planned ids, kept locked ids)."""
        def pos(row: Any) -> tuple[Any, Any, Any]:
            return row["act_number"], row["scene_number"], row["shot_number"]

        with self.db.transaction():
            rows = self.db.conn.execute(
                "SELECT id, locked, act_number, scene_number, shot_number FROM shots WHERE episode_id=?",
                (episode_id,)).fetchall()
            locked = [r for r in rows if r["locked"]]
            taken = {pos(r) for r in locked}
            reusable = {pos(r): r["id"] for r in rows if not r["locked"]}
            planned: list[int] = []
            for shot in shots:
                key = (shot.get("act_number", 1), shot.get("scene_number", 1), shot.get("shot_number", 1))
                if key in taken:
                    continue
                if key in reusable:
                    shot_id = reusable.pop(key)
                    self._update("shots", shot_id, shot)
                    planned.append(shot_id)
                else:
                    planned.append(self._insert("shots", project_id, shot, episode_id=episode_id))
            for leftover in reusable.values():
                self.db.conn.execute("DELETE FROM shots WHERE id=?", (leftover,))
            return planned, [r["id"] for r in locked]

    # -- dialogue -------------------------------------------------------------------------------------
    def dialogue_lines(self, project_id: int, episode_id: int | None = None) -> list[dict[str, Any]]:
        """Every spoken line with its shot, position and the character it resolves to (``character_id`` None =
        the name matches no character: the UI offers 'create' or 'reassign')."""
        by_name = {c["name"]: c for c in self.list_characters(project_id)}
        out = []
        for shot in self.list_shots(project_id, episode_id):
            for idx, line in enumerate(shot["dialogue"]):
                who = by_name.get(str(line.get("character", "")).strip())
                out.append({"shot_id": shot["id"], "index": idx, "id": f"{shot['id']}#{idx}",
                            "character": line.get("character", ""), "character_id": who["id"] if who else None,
                            "line": line.get("line", ""), "emotion": line.get("emotion", "")})
        return out

    def reassign_speaker(self, shot_id: int, index: int, character: str) -> dict[str, Any]:
        shot = self.get_shot(shot_id)
        if not 0 <= index < len(shot["dialogue"]):
            raise StoryNotFound(f"shot {shot_id} has no line {index}")
        shot["dialogue"][index]["character"] = character
        return self.update_shot(shot_id, {"dialogue": shot["dialogue"]})
