-- 004_workflow: staged story pipeline (analyze -> cast -> script -> scenes -> music -> shotplan -> specs).
--   workflow_stages : per project and stage, what state it is in (resume / stale detection)
--   workflow_runs   : the queue the worker drains (one row = "run these stages")
--   workflow_data   : small structured results other stages read (the production plan, the style guide)

CREATE TABLE workflow_stages (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage       TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','stale')),
    started_at  TEXT,
    finished_at TEXT,
    error_code  TEXT,
    error_message TEXT,
    summary     TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(summary)),
    attempts    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, stage)
);

CREATE TABLE workflow_runs (
    id               TEXT    PRIMARY KEY,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stages           TEXT    NOT NULL CHECK (json_valid(stages)),
    params           TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(params)),
    status           TEXT    NOT NULL DEFAULT 'queued' CHECK (status IN
                         ('queued','running','completed','failed','cancelled')),
    progress         TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(progress)),
    error_code       TEXT,
    error_message    TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    lease_owner      TEXT,
    lease_expires_at TEXT,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    started_at       TEXT,
    finished_at      TEXT
);
CREATE INDEX idx_wfruns_queue ON workflow_runs(status, created_at);
CREATE INDEX idx_wfruns_project ON workflow_runs(project_id, created_at);

CREATE TABLE workflow_data (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    key        TEXT    NOT NULL,
    value      TEXT    NOT NULL CHECK (json_valid(value)),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (project_id, key)
);

-- Theme songs carry lyrics and a length; BGM cues a length. (Edited through gen_specs; these are the story-side record.)
ALTER TABLE music_themes ADD COLUMN lyrics TEXT NOT NULL DEFAULT '';
ALTER TABLE music_themes ADD COLUMN duration_sec REAL;
ALTER TABLE music_themes ADD COLUMN scene_ref TEXT NOT NULL DEFAULT '';   -- "act01_scene02" for a scene BGM, '' for the theme
