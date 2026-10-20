-- 001_core: story/content domain. Media ledger arrives in 002.
-- JSON columns hold TEXT validated by CHECK(json_valid(..)).
-- Timestamps are UTC ISO-8601 (strftime default below).

CREATE TABLE projects (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    slug           TEXT    NOT NULL UNIQUE,
    name           TEXT    NOT NULL DEFAULT '',
    description    TEXT    NOT NULL DEFAULT '',
    genre          TEXT    NOT NULL DEFAULT '',
    genre_tags     TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(genre_tags)),
    tone_tags      TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(tone_tags)),
    emotion_arc    TEXT    NOT NULL DEFAULT '',
    format         TEXT    NOT NULL DEFAULT 'short_drama',
    aspect_ratio   TEXT    NOT NULL DEFAULT '9:16',
    episode_count  INTEGER NOT NULL DEFAULT 1,
    act_count      INTEGER NOT NULL DEFAULT 4,
    delivery_profile TEXT  NOT NULL DEFAULT '',
    -- language of script/narration/subtitles/dubbing; independent of the UI locale
    content_language TEXT  NOT NULL DEFAULT 'zh-CN',
    status         TEXT    NOT NULL DEFAULT 'draft',
    legacy_id      INTEGER UNIQUE,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE scripts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    episode_no   INTEGER NOT NULL DEFAULT 1,
    title        TEXT    NOT NULL DEFAULT '',
    synopsis     TEXT    NOT NULL DEFAULT '',
    acts         TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(acts)),
    total_scenes INTEGER NOT NULL DEFAULT 0,
    word_count   INTEGER NOT NULL DEFAULT 0,
    status       TEXT    NOT NULL DEFAULT 'draft',
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_scripts_project ON scripts(project_id, episode_no);

CREATE TABLE characters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL DEFAULT '',
    role            TEXT    NOT NULL DEFAULT '',
    age             TEXT    NOT NULL DEFAULT '',
    gender          TEXT    NOT NULL DEFAULT '',
    appearance      TEXT    NOT NULL DEFAULT '',
    personality     TEXT    NOT NULL DEFAULT '',
    background      TEXT    NOT NULL DEFAULT '',
    relationships   TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(relationships)),
    prompt_template TEXT    NOT NULL DEFAULT '',
    -- voice: description for LLMs + reference clip for cloning TTS
    voice_profile   TEXT    NOT NULL DEFAULT '',
    voice_ref_audio TEXT    NOT NULL DEFAULT '',
    voice_ref_text  TEXT    NOT NULL DEFAULT '',
    -- visual consistency: references resolved via config, never hard-coded in pipelines
    lora_ref        TEXT    NOT NULL DEFAULT '',
    ref_images      TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(ref_images)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (project_id, name)
);

CREATE TABLE scenes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL DEFAULT '',
    description     TEXT    NOT NULL DEFAULT '',
    lighting        TEXT    NOT NULL DEFAULT '',
    color_palette   TEXT    NOT NULL DEFAULT '',
    atmosphere      TEXT    NOT NULL DEFAULT '',
    prompt_template TEXT    NOT NULL DEFAULT '',
    lora_ref        TEXT    NOT NULL DEFAULT '',
    ref_images      TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(ref_images)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (project_id, name)
);

CREATE TABLE music_themes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL DEFAULT 'bgm',
    mood        TEXT    NOT NULL DEFAULT '',
    tempo       TEXT    NOT NULL DEFAULT '',
    instruments TEXT    NOT NULL DEFAULT '',
    key_signature TEXT  NOT NULL DEFAULT '',
    description TEXT    NOT NULL DEFAULT '',
    gen_prompt  TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE sound_effects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL DEFAULT '',
    category    TEXT    NOT NULL DEFAULT '',
    description TEXT    NOT NULL DEFAULT '',
    tags        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE episodes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    number     INTEGER NOT NULL DEFAULT 1,
    title      TEXT    NOT NULL DEFAULT '',
    summary    TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'draft',
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (project_id, number)
);

CREATE TABLE shots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    episode_id      INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    script_id       INTEGER REFERENCES scripts(id) ON DELETE SET NULL,
    act_number      INTEGER NOT NULL DEFAULT 1,
    scene_number    INTEGER NOT NULL DEFAULT 1,
    shot_number     INTEGER NOT NULL DEFAULT 1,
    location        TEXT    NOT NULL DEFAULT '',
    shot_type       TEXT    NOT NULL DEFAULT '',
    mood            TEXT    NOT NULL DEFAULT '',
    time_of_day     TEXT    NOT NULL DEFAULT '',
    weather         TEXT    NOT NULL DEFAULT '',
    characters      TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(characters)),
    narration       TEXT    NOT NULL DEFAULT '',
    dialogue        TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(dialogue)),
    camera_notes    TEXT    NOT NULL DEFAULT '',
    visual_prompt   TEXT    NOT NULL DEFAULT '',
    -- versioned shot contract, e.g. "shot.v2"; "legacy" for imported rows
    payload_version TEXT    NOT NULL DEFAULT 'shot.v3',
    render_payload  TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(render_payload)),
    status          TEXT    NOT NULL DEFAULT 'draft',
    locked          INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0,1)),
    error           TEXT    NOT NULL DEFAULT '',
    -- carried over from the legacy DB so media_assets can be back-filled; not authoritative
    legacy_video_path TEXT  NOT NULL DEFAULT '',
    legacy_audio_path TEXT  NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (episode_id, act_number, scene_number, shot_number)
);
CREATE INDEX idx_shots_project ON shots(project_id);
CREATE INDEX idx_shots_episode ON shots(episode_id);

-- LLM call audit (one row per generate call)
CREATE TABLE generation_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    agent       TEXT    NOT NULL DEFAULT '',
    model       TEXT    NOT NULL DEFAULT '',
    prompt      TEXT    NOT NULL DEFAULT '',
    response    TEXT    NOT NULL DEFAULT '',
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_genlogs_project ON generation_logs(project_id, agent);
