-- 002_media: editable generation specs + job ledger + asset ledger.
--   gen_specs   : what the creator edits (append-only versions)
--   media_jobs  : one execution = a frozen snapshot of a spec version
--   media_assets: what an execution produced (versions; one "current" per slot)
-- Ids of specs/jobs/assets are uuid4 hex so adapters/APIs can reference them without a round trip.

ALTER TABLE projects ADD COLUMN settings TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(settings));
ALTER TABLE characters ADD COLUMN voice_ref_consent INTEGER NOT NULL DEFAULT 0 CHECK (voice_ref_consent IN (0,1));
ALTER TABLE characters ADD COLUMN voice_consent_note TEXT NOT NULL DEFAULT '';

CREATE TABLE gen_specs (
    id              TEXT    PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    subject_type    TEXT    NOT NULL,                 -- e.g. character.portrait, dialogue.line, music.theme_song
    subject_id      TEXT    NOT NULL DEFAULT '',      -- character/scene/shot id; dialogue: '<shot_id>#<line_idx>'
    slot            TEXT    NOT NULL DEFAULT 'main',
    version         INTEGER NOT NULL CHECK (version >= 1),
    parent_version  INTEGER,
    kind            TEXT    NOT NULL,                 -- MediaKind
    model_ref       TEXT    NOT NULL DEFAULT '',      -- '' = default model for the kind
    prompt          TEXT    NOT NULL DEFAULT '',
    negative_prompt TEXT    NOT NULL DEFAULT '',
    text_payload    TEXT    NOT NULL DEFAULT '',      -- dialogue text / lyrics
    params          TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(params)),
    seed            INTEGER,
    refs            TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(refs)),        -- reference images/audio/LoRAs
    depends_on      TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(depends_on)),  -- [{subject_type,subject_id,slot,version}]
    author          TEXT    NOT NULL DEFAULT 'user' CHECK (author IN ('user','agent')),
    note            TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (project_id, subject_type, subject_id, slot, version)
);
CREATE INDEX idx_specs_subject ON gen_specs(project_id, subject_type, subject_id, slot, version);

-- Presence of a row = the subject is locked against edits.
CREATE TABLE gen_spec_locks (
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    subject_type TEXT    NOT NULL,
    subject_id   TEXT    NOT NULL DEFAULT '',
    slot         TEXT    NOT NULL DEFAULT 'main',
    note         TEXT    NOT NULL DEFAULT '',
    locked_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (project_id, subject_type, subject_id, slot)
);

CREATE VIEW gen_specs_current AS
SELECT s.* FROM gen_specs s
WHERE s.version = (
    SELECT MAX(v.version) FROM gen_specs v
    WHERE v.project_id = s.project_id AND v.subject_type = s.subject_type
      AND v.subject_id = s.subject_id AND v.slot = s.slot
);

CREATE TABLE media_jobs (
    id                TEXT    PRIMARY KEY,
    project_id        INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    shot_id           INTEGER REFERENCES shots(id) ON DELETE SET NULL,
    spec_id           TEXT    REFERENCES gen_specs(id) ON DELETE SET NULL,
    spec_version      INTEGER,
    kind              TEXT    NOT NULL,
    provider          TEXT    NOT NULL,
    model_ref         TEXT    NOT NULL,
    tier              TEXT    NOT NULL DEFAULT 'final' CHECK (tier IN ('draft','preview','final')),
    slot              TEXT    NOT NULL DEFAULT 'main',
    status            TEXT    NOT NULL DEFAULT 'queued' CHECK (status IN (
                          'queued','running','completed','workflow_error','transport_error',
                          'timeout','failed_validation','cancelled')),
    priority          INTEGER NOT NULL DEFAULT 5,
    inputs            TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(inputs)),
    params            TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(params)),   -- fully resolved, reproducible
    seed              INTEGER,
    idempotency_key   TEXT    NOT NULL DEFAULT '',
    heavy             INTEGER NOT NULL DEFAULT 0 CHECK (heavy IN (0,1)),
    est_memory_gib    REAL    NOT NULL DEFAULT 0,
    est_seconds       INTEGER NOT NULL DEFAULT 0,
    license_tag       TEXT    NOT NULL DEFAULT '',
    commercial_ok     INTEGER NOT NULL DEFAULT 1 CHECK (commercial_ok IN (0,1)),
    attempt           INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 2,
    parent_job_id     TEXT,
    lease_owner       TEXT,
    lease_expires_at  TEXT,
    external_id       TEXT,                                 -- e.g. ComfyUI prompt_id; enables re-attach
    progress          REAL    NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    progress_msg      TEXT    NOT NULL DEFAULT '',          -- i18n key or free text from the adapter
    error_code        TEXT,
    error_message     TEXT,
    cancel_requested  INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    started_at        TEXT,
    finished_at       TEXT,
    actual_seconds    REAL,
    peak_memory_gib   REAL
);
CREATE INDEX idx_jobs_queue ON media_jobs(status, priority, created_at);
CREATE INDEX idx_jobs_project ON media_jobs(project_id, created_at);
CREATE INDEX idx_jobs_shot ON media_jobs(shot_id);
CREATE INDEX idx_jobs_spec ON media_jobs(spec_id, spec_version);
-- A second identical request while the first is still active returns the first job.
CREATE UNIQUE INDEX uq_jobs_active_idem ON media_jobs(idempotency_key)
    WHERE idempotency_key <> '' AND status IN ('queued','running');

CREATE TABLE media_assets (
    id            TEXT    PRIMARY KEY,
    job_id        TEXT    REFERENCES media_jobs(id) ON DELETE SET NULL,
    project_id    INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    shot_id       INTEGER REFERENCES shots(id) ON DELETE SET NULL,
    spec_id       TEXT    REFERENCES gen_specs(id) ON DELETE SET NULL,
    spec_version  INTEGER,
    kind          TEXT    NOT NULL,
    slot          TEXT    NOT NULL DEFAULT 'main',
    path          TEXT    NOT NULL,                         -- relative to STORY_AGENT_HOME
    sha256        TEXT    NOT NULL DEFAULT '',
    bytes         INTEGER NOT NULL DEFAULT 0,
    duration_sec  REAL,
    width         INTEGER,
    height        INTEGER,
    fps           REAL,
    has_audio     INTEGER CHECK (has_audio IN (0,1)),
    metadata      TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(metadata)),
    provenance    TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(provenance)),
    version       INTEGER NOT NULL DEFAULT 1,
    is_current    INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0,1)),
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    trashed_at    TEXT
);
CREATE INDEX idx_assets_shot ON media_assets(project_id, shot_id, kind, slot);
CREATE INDEX idx_assets_job ON media_assets(job_id);
-- Exactly one current asset per (project, shot, kind, slot); candidates are the rest.
CREATE UNIQUE INDEX uq_assets_current ON media_assets(project_id, COALESCE(shot_id, 0), kind, slot)
    WHERE is_current = 1 AND trashed_at IS NULL;

CREATE TABLE job_events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT    NOT NULL REFERENCES media_jobs(id) ON DELETE CASCADE,
    ts     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    level  TEXT    NOT NULL DEFAULT 'info' CHECK (level IN ('debug','info','warning','error')),
    event  TEXT    NOT NULL,
    data   TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(data))
);
CREATE INDEX idx_events_job ON job_events(job_id, id);
