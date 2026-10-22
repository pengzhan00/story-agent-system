-- 017_content_rating: content ratings (config-driven, content.toml), the adult declaration of a character and the
-- time-limited unlock sessions that let a signed-in user (or the local operator) see restricted material.
ALTER TABLE projects      ADD COLUMN rating TEXT NOT NULL DEFAULT 'general';
ALTER TABLE media_assets  ADD COLUMN rating TEXT NOT NULL DEFAULT 'general';
ALTER TABLE library_items ADD COLUMN rating TEXT NOT NULL DEFAULT 'general';
ALTER TABLE characters    ADD COLUMN adult_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (adult_confirmed IN (0,1));
CREATE INDEX idx_assets_rating ON media_assets(project_id, rating);
CREATE TABLE content_unlocks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,      -- NULL = the local operator (auth off)
    actor      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    expires_at TEXT    NOT NULL,
    revoked_at TEXT
);
CREATE INDEX idx_unlocks_user ON content_unlocks(user_id);
