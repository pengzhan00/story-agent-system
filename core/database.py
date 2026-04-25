"""
Story Agent System — SQLite Database Layer
Single-connection with thread-safety for reliable local operation.
"""
import sqlite3
import json
import os
import threading
from typing import Optional, List, Type, TypeVar
from datetime import datetime, timezone

from .models import (
    Project, Script, Character, SceneAsset,
    MusicTheme, SoundEffect, PromptTemplate, GenerationLog
)

T = TypeVar("T")
_lock = threading.Lock()

DB_PATH = os.path.expanduser(
    "~/myworkspace/projects/story-agent-system/story_agents.db"
)

_table_map = {
    Project: "projects",
    Script: "scripts",
    Character: "characters",
    SceneAsset: "scenes",
    MusicTheme: "music_themes",
    SoundEffect: "sound_effects",
    PromptTemplate: "prompt_templates",
    GenerationLog: "generation_logs",
}

# JSON fields per table (auto-serialized/deserialized)
_json_fields = {
    "scripts": ["acts"],
    "characters": ["relationships", "ip_ref_images"],
    "scenes": ["ref_images"],
    "prompt_templates": ["variables"],
}

# ───────── Single connection (thread-safe) ─────────

_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    """Get or create the singleton connection. Thread-safe via lock."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


def _execute(sql: str, params: tuple = ()):
    """Execute with automatic commit and lock."""
    with _lock:
        c = _get_conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur


def _fetchone(sql: str, params: tuple = ()):
    with _lock:
        c = _get_conn()
        return c.execute(sql, params).fetchone()


def _fetchall(sql: str, params: tuple = ()):
    with _lock:
        c = _get_conn()
        return c.execute(sql, params).fetchall()


# ───────── Table init ─────────

def init_db():
    """Create tables if they don't exist."""
    with _lock:
        c = _get_conn()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                genre TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL DEFAULT '',
                synopsis TEXT NOT NULL DEFAULT '',
                acts TEXT NOT NULL DEFAULT '[]',
                total_scenes INTEGER NOT NULL DEFAULT 0,
                word_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                role TEXT NOT NULL DEFAULT '主角',
                age TEXT NOT NULL DEFAULT '',
                gender TEXT NOT NULL DEFAULT '',
                appearance TEXT NOT NULL DEFAULT '',
                personality TEXT NOT NULL DEFAULT '',
                background TEXT NOT NULL DEFAULT '',
                voice_profile TEXT NOT NULL DEFAULT '',
                relationships TEXT NOT NULL DEFAULT '[]',
                lora_ref TEXT NOT NULL DEFAULT '',
                ip_ref_images TEXT NOT NULL DEFAULT '[]',
                prompt_template TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                description TEXT NOT NULL DEFAULT '',
                lighting TEXT NOT NULL DEFAULT '',
                color_palette TEXT NOT NULL DEFAULT '',
                atmosphere TEXT NOT NULL DEFAULT '',
                ref_images TEXT NOT NULL DEFAULT '[]',
                lora_ref TEXT NOT NULL DEFAULT '',
                prompt_template TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS music_themes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL DEFAULT '',
                type TEXT NOT NULL DEFAULT 'bgm',
                mood TEXT NOT NULL DEFAULT '',
                tempo TEXT NOT NULL DEFAULT '中速',
                instruments TEXT NOT NULL DEFAULT '',
                key_signature TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                prompt_for_gen TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sound_effects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '环境',
                description TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prompt_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                agent_type TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '通用',
                content TEXT NOT NULL DEFAULT '',
                variables TEXT NOT NULL DEFAULT '[]',
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS generation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL DEFAULT 0,
                agent_type TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL DEFAULT '',
                response TEXT NOT NULL DEFAULT '',
                tokens_in INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_scripts_project ON scripts(project_id);
            CREATE INDEX IF NOT EXISTS idx_chars_project ON characters(project_id);
            CREATE INDEX IF NOT EXISTS idx_scenes_project ON scenes(project_id);
            CREATE INDEX IF NOT EXISTS idx_music_project ON music_themes(project_id);
            CREATE INDEX IF NOT EXISTS idx_sfx_project ON sound_effects(project_id);
            CREATE INDEX IF NOT EXISTS idx_logs_project ON generation_logs(project_id);
            CREATE INDEX IF NOT EXISTS idx_logs_agent ON generation_logs(agent_type);

            CREATE TABLE IF NOT EXISTS task_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL DEFAULT 0,
                agent_type TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                input_params TEXT NOT NULL DEFAULT '{}',
                output_result TEXT NOT NULL DEFAULT '{}',
                priority INTEGER NOT NULL DEFAULT 5,
                error TEXT NOT NULL DEFAULT '',
                parent_task_id INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS agent_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL DEFAULT 0,
                agent_type TEXT NOT NULL,
                action TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_status ON task_queue(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_project ON task_queue(project_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_agent ON task_queue(agent_type);
        """)
        c.commit()


# ───────── Row ↔ Model conversion ─────────

def _row2model(row: sqlite3.Row, cls: Type[T], table: str) -> T:
    d = dict(row)
    return cls(**d)


def _ensure_json(d: dict, table: str) -> dict:
    """Deep copy and ensure JSON fields are serialized."""
    result = dict(d)
    for field in _json_fields.get(table, []):
        val = result.get(field)
        if val is not None and not isinstance(val, str):
            result[field] = json.dumps(val, ensure_ascii=False)
        elif val is None:
            result.pop(field, None)
    # Remove id for INSERT
    if result.get("id") is None:
        result.pop("id", None)
    return result


# ───────── Generic CRUD ─────────

def _insert(table: str, data: dict) -> int:
    data = _ensure_json(data, table)
    now = datetime.now(timezone.utc).isoformat()
    cols_with_defaults = {
        "created_at": now,
        "updated_at": now,
    }
    for col, default in cols_with_defaults.items():
        if col not in data:
            # Check if column exists in table
            col_info = _get_conn().execute(f"PRAGMA table_info({table})").fetchall()
            col_names = [r[1] for r in col_info]
            if col in col_names:
                data[col] = default
    cols = ", ".join(data.keys())
    vals = ", ".join("?" for _ in data)
    cur = _execute(f"INSERT INTO {table} ({cols}) VALUES ({vals})", list(data.values()))
    return cur.lastrowid


def _update(table: str, rid: int, data: dict):
    data = _ensure_json(data, table)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k}=?" for k in data)
    _execute(f"UPDATE {table} SET {sets} WHERE id=?", list(data.values()) + [rid])


def _delete(table: str, rid: int):
    _execute(f"DELETE FROM {table} WHERE id=?", (rid,))


def _get(table: str, cls, rid: int):
    row = _fetchone(f"SELECT * FROM {table} WHERE id=?", (rid,))
    return _row2model(row, cls, table) if row else None


def _list(table: str, cls, where: str = "", params: tuple = ()):
    q = f"SELECT * FROM {table}"
    if where:
        q += f" WHERE {where}"
    q += " ORDER BY id DESC"
    return [_row2model(r, cls, table) for r in _fetchall(q, params)]


# ───────── Public API ─────────

# --- Projects ---
def create_project(data: dict) -> int:
    return _insert("projects", data)

def update_project(pid: int, data: dict):
    _update("projects", pid, data)

def delete_project(pid: int):
    _delete("projects", pid)

def get_project(pid: int) -> Optional[Project]:
    return _get("projects", Project, pid)

def list_projects() -> List[Project]:
    return _list("projects", Project)

# --- Scripts ---
def create_script(data: dict) -> int:
    return _insert("scripts", data)

def update_script(sid: int, data: dict):
    _update("scripts", sid, data)

def delete_script(sid: int):
    _delete("scripts", sid)

def get_script(sid: int) -> Optional[Script]:
    return _get("scripts", Script, sid)

def list_scripts(project_id: int) -> List[Script]:
    return _list("scripts", Script, "project_id=?", (project_id,))

# --- Characters ---
def create_character(data: dict) -> int:
    return _insert("characters", data)

def update_character(cid: int, data: dict):
    _update("characters", cid, data)

def delete_character(cid: int):
    _delete("characters", cid)

def get_character(cid: int) -> Optional[Character]:
    return _get("characters", Character, cid)

def list_characters(project_id: int) -> List[Character]:
    return _list("characters", Character, "project_id=?", (project_id,))

# --- Scene assets ---
def create_scene_asset(data: dict) -> int:
    return _insert("scenes", data)

def update_scene_asset(sid: int, data: dict):
    _update("scenes", sid, data)

def delete_scene_asset(sid: int):
    _delete("scenes", sid)

def get_scene_asset(sid: int) -> Optional[SceneAsset]:
    return _get("scenes", SceneAsset, sid)

def list_scene_assets(project_id: int) -> List[SceneAsset]:
    return _list("scenes", SceneAsset, "project_id=?", (project_id,))

# --- Music ---
def create_music(data: dict) -> int:
    return _insert("music_themes", data)

def delete_music(mid: int):
    _delete("music_themes", mid)

def get_music(mid: int) -> Optional[MusicTheme]:
    return _get("music_themes", MusicTheme, mid)

def list_music(project_id: int) -> List[MusicTheme]:
    return _list("music_themes", MusicTheme, "project_id=?", (project_id,))

# --- Sound Effects ---
def create_sfx(data: dict) -> int:
    return _insert("sound_effects", data)

def delete_sfx(sid: int):
    _delete("sound_effects", sid)

def list_sfx(project_id: int) -> List[SoundEffect]:
    return _list("sound_effects", SoundEffect, "project_id=?", (project_id,))

# --- Prompt Templates ---
def create_prompt(data: dict) -> int:
    return _insert("prompt_templates", data)

def update_prompt(pid: int, data: dict):
    _update("prompt_templates", pid, data)

def delete_prompt(pid: int):
    _delete("prompt_templates", pid)

def list_prompts(agent_type: str = "") -> List[PromptTemplate]:
    if agent_type:
        return _list("prompt_templates", PromptTemplate, "agent_type=?", (agent_type,))
    return _list("prompt_templates", PromptTemplate)

# --- Generation Logs ---
def log_generation(data: dict):
    _insert("generation_logs", data)

def list_logs(project_id: int = 0, limit: int = 50) -> List[GenerationLog]:
    if project_id:
        return _list("generation_logs", GenerationLog,
                     "project_id=? ORDER BY id DESC LIMIT ?", (project_id, limit))
    return _list("generation_logs", GenerationLog,
                 "1=1 ORDER BY id DESC LIMIT ?", (limit,))


def add_prompt_log(project_id: int, agent_type: str, action_type: str,
                   prompt: str, response: str, model: str = ""):
    """兼容桥接函数 — 记录 agent prompt 日志"""
    import time
    log_generation({
        "project_id": project_id,
        "agent_type": f"{agent_type}/{action_type}",
        "model": model,
        "prompt": prompt,
        "response": str(response)[:5000],
        "tokens_in": 0,
        "tokens_out": 0,
        "duration_ms": 0,
    })


# ══════════════════════════════════════════════
#  Task Queue API
# ══════════════════════════════════════════════

def create_task(data: dict) -> int:
    """Create a new task in the queue. Returns task_id."""
    now = datetime.now(timezone.utc).isoformat()
    task_data = {
        "project_id": data.get("project_id", 0),
        "agent_type": data["agent_type"],
        "action": data["action"],
        "status": "pending",
        "input_params": json.dumps(data.get("input_params", {}), ensure_ascii=False),
        "output_result": "{}",
        "priority": data.get("priority", 5),
        "error": "",
        "parent_task_id": data.get("parent_task_id", 0),
        "created_at": now,
    }
    return _insert("task_queue", task_data)


def claim_next_task(agent_type: str) -> Optional[dict]:
    """Claim the highest-priority pending task for this agent type. Returns task dict or None."""
    with _lock:
        c = _get_conn()
        row = c.execute(
            "SELECT * FROM task_queue WHERE agent_type=? AND status='pending' ORDER BY priority ASC, id ASC LIMIT 1",
            (agent_type,)
        ).fetchone()
        if row:
            now = datetime.now(timezone.utc).isoformat()
            c.execute("UPDATE task_queue SET status='running', started_at=? WHERE id=?", (now, row["id"]))
            c.commit()
            return dict(row)
        return None


def get_task(task_id: int) -> Optional[dict]:
    row = _fetchone("SELECT * FROM task_queue WHERE id=?", (task_id,))
    return dict(row) if row else None


def complete_task(task_id: int, output: dict, error: str = ""):
    now = datetime.now(timezone.utc).isoformat()
    if error:
        _execute("UPDATE task_queue SET status='failed', output_result=?, error=?, completed_at=? WHERE id=?",
                 (json.dumps(output, ensure_ascii=False), error, now, task_id))
    else:
        _execute("UPDATE task_queue SET status='completed', output_result=?, completed_at=? WHERE id=?",
                 (json.dumps(output, ensure_ascii=False), now, task_id))


def list_tasks(project_id: int = 0, agent_type: str = "", status: str = "", limit: int = 50):
    where = []
    params = []
    if project_id:
        where.append("project_id=?")
        params.append(project_id)
    if agent_type:
        where.append("agent_type=?")
        params.append(agent_type)
    if status:
        where.append("status=?")
        params.append(status)
    w = " AND ".join(where) if where else "1=1"
    rows = _fetchall(f"SELECT * FROM task_queue WHERE {w} ORDER BY id DESC LIMIT ?", params + [limit])
    return [dict(r) for r in rows]


def add_agent_log(task_id: int, agent_type: str, action: str, level: str, message: str):
    now = datetime.now(timezone.utc).isoformat()
    _insert("agent_logs", {
        "task_id": task_id,
        "agent_type": agent_type,
        "action": action,
        "level": level,
        "message": message,
        "created_at": now,
    })


def list_agent_logs(task_id: int = 0, limit: int = 50):
    if task_id:
        rows = _fetchall("SELECT * FROM agent_logs WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))
    else:
        rows = _fetchall("SELECT * FROM agent_logs ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]
