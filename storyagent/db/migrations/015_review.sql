-- 015_review: review notes. A note belongs to a spec (what a take was made from) and may name the take it is about.
-- status: open -> redoing (a redo was queued from it) -> resolved | dismissed (reopen goes back to open).
CREATE TABLE review_notes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    subject_type     TEXT    NOT NULL,
    subject_id       TEXT    NOT NULL,
    slot             TEXT    NOT NULL DEFAULT 'main',
    shot_id          INTEGER,
    asset_id         TEXT,
    body             TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open','redoing','resolved','dismissed')),
    author           TEXT    NOT NULL DEFAULT '',
    resolution       TEXT    NOT NULL DEFAULT '',
    resolved_by      TEXT    NOT NULL DEFAULT '',
    resolved_at      TEXT,
    redo_spec_version INTEGER,
    redo_job_ids     TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(redo_job_ids)),
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_notes_project_status ON review_notes(project_id, status);
CREATE INDEX idx_notes_subject ON review_notes(project_id, subject_type, subject_id);
CREATE INDEX idx_notes_shot ON review_notes(project_id, shot_id);
