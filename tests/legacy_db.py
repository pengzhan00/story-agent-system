"""Builds a synthetic v1 ``story_agents.db`` (same DDL as legacy core/database.py)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  genre_tags TEXT NOT NULL DEFAULT '[]', tone_tags TEXT NOT NULL DEFAULT '[]',
  emotion_arc TEXT NOT NULL DEFAULT '', episode_count INTEGER NOT NULL DEFAULT 80,
  act_count INTEGER NOT NULL DEFAULT 4, format TEXT NOT NULL DEFAULT 'short_drama',
  aspect_ratio TEXT NOT NULL DEFAULT '9:16');
CREATE TABLE scripts (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '', synopsis TEXT NOT NULL DEFAULT '', acts TEXT NOT NULL DEFAULT '[]',
  total_scenes INTEGER NOT NULL DEFAULT 0, word_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE characters (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '',
  project_id INTEGER NOT NULL, role TEXT NOT NULL DEFAULT '主角', age TEXT NOT NULL DEFAULT '',
  gender TEXT NOT NULL DEFAULT '', appearance TEXT NOT NULL DEFAULT '', personality TEXT NOT NULL DEFAULT '',
  background TEXT NOT NULL DEFAULT '', voice_profile TEXT NOT NULL DEFAULT '',
  relationships TEXT NOT NULL DEFAULT '[]', lora_ref TEXT NOT NULL DEFAULT '',
  ip_ref_images TEXT NOT NULL DEFAULT '[]', prompt_template TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE scenes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '',
  project_id INTEGER NOT NULL, description TEXT NOT NULL DEFAULT '', lighting TEXT NOT NULL DEFAULT '',
  color_palette TEXT NOT NULL DEFAULT '', atmosphere TEXT NOT NULL DEFAULT '',
  ref_images TEXT NOT NULL DEFAULT '[]', lora_ref TEXT NOT NULL DEFAULT '',
  prompt_template TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE music_themes (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  name TEXT NOT NULL DEFAULT '', type TEXT NOT NULL DEFAULT 'bgm', mood TEXT NOT NULL DEFAULT '',
  tempo TEXT NOT NULL DEFAULT '', instruments TEXT NOT NULL DEFAULT '', key_signature TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '', file_path TEXT NOT NULL DEFAULT '',
  prompt_for_gen TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE sound_effects (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  name TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
  file_path TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE episodes (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  number INTEGER NOT NULL DEFAULT 1, title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE shots (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  episode_id INTEGER NOT NULL DEFAULT 0, script_id INTEGER NOT NULL DEFAULT 0,
  act_number INTEGER NOT NULL DEFAULT 1, scene_number INTEGER NOT NULL DEFAULT 1,
  shot_number INTEGER NOT NULL DEFAULT 1, location TEXT NOT NULL DEFAULT '',
  shot_type TEXT NOT NULL DEFAULT '中景', mood TEXT NOT NULL DEFAULT '',
  time_of_day TEXT NOT NULL DEFAULT '白天', weather TEXT NOT NULL DEFAULT '晴',
  characters TEXT NOT NULL DEFAULT '[]', narration TEXT NOT NULL DEFAULT '',
  dialogue TEXT NOT NULL DEFAULT '[]', camera_notes TEXT NOT NULL DEFAULT '',
  visual_prompt TEXT NOT NULL DEFAULT '', render_payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'draft', locked INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '', video_path TEXT NOT NULL DEFAULT '',
  audio_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""

NOW = "2026-05-01T00:00:00+00:00"


def build_legacy_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    q = conn.execute
    q("INSERT INTO projects(name, genre, genre_tags, created_at, updated_at) VALUES (?,?,?,?,?)",
      ("穿越女医生 Revenge!", "古装悬疑", '["悬疑","古装"]', NOW, NOW))
    q("INSERT INTO projects(name, created_at, updated_at) VALUES ('', ?, ?)", (NOW, NOW))
    q("INSERT INTO scripts(project_id, title, acts, total_scenes, created_at, updated_at) "
      "VALUES (1,'第一集','[{\"act\":1}]',3,?,?)", (NOW, NOW))
    for name in ("林晚", "林晚", "陆远"):  # duplicate name on purpose
        q("INSERT INTO characters(name, project_id, voice_profile, ip_ref_images, created_at, updated_at) "
          "VALUES (?,1,'低沉男声','broken-json',?,?)", (name, NOW, NOW))
    for name in ("医馆", "医馆"):
        q("INSERT INTO scenes(name, project_id, created_at, updated_at) VALUES (?,1,?,?)", (name, NOW, NOW))
    q("INSERT INTO music_themes(project_id, name, type, prompt_for_gen, created_at) "
      "VALUES (1,'主题曲','theme','epic erhu',?)", (NOW,))
    q("INSERT INTO sound_effects(project_id, name, created_at) VALUES (1,'雨声',?)", (NOW,))
    q("INSERT INTO episodes(project_id, number, title, created_at, updated_at) VALUES (1,2,'第二集',?,?)",
      (NOW, NOW))
    # two shots share (episode 0 -> ep1, act1/scene1/shot1): legacy allowed duplicates
    for status, vp in (("rendered", "/old/out/a.mp4"), ("qc_failed", "")):
        q("INSERT INTO shots(project_id, episode_id, script_id, status, video_path, dialogue, render_payload, "
          "created_at, updated_at) VALUES (1,0,1,?,?,'[{\"character\":\"林晚\",\"line\":\"你好\"}]',"
          "'{\"schema\":\"shot.v2\"}',?,?)", (status, vp, NOW, NOW))
    q("INSERT INTO shots(project_id, episode_id, script_id, shot_number, created_at, updated_at) "
      "VALUES (1,1,1,1,?,?)", (NOW, NOW))
    conn.commit()
    conn.close()
    return path
