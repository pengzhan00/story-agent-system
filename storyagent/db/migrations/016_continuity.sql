-- 016_continuity: what a character wears and which props exist, so a shot's prompt is built from the same words every
-- time. A look applies from (episode, act, scene) onward until the next look of the same character; props are named
-- objects with one fixed description, added to the prompt of every shot whose text mentions them.
CREATE TABLE character_looks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    character_id  INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    label         TEXT    NOT NULL DEFAULT '',
    costume       TEXT    NOT NULL,
    from_episode  INTEGER NOT NULL DEFAULT 1 CHECK (from_episode >= 1),
    from_act      INTEGER NOT NULL DEFAULT 1 CHECK (from_act >= 1),
    from_scene    INTEGER NOT NULL DEFAULT 1 CHECK (from_scene >= 1),
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_looks_character ON character_looks(character_id);
CREATE TABLE props (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id         INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name               TEXT    NOT NULL,
    description        TEXT    NOT NULL DEFAULT '',
    introduced_episode INTEGER NOT NULL DEFAULT 1 CHECK (introduced_episode >= 1),
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (project_id, name)
);
