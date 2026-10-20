-- 003_library: a cross-project asset library (voices, portraits, LoRAs, music ...).
-- A library item points at its OWN asset row (project_id NULL, file copied under <home>/library/), so deleting
-- the project it came from never breaks it. Jobs reference items as "library:<role>/<name>".

CREATE TABLE library_items (
    id                 TEXT    PRIMARY KEY,
    role               TEXT    NOT NULL,                     -- voice | portrait | lora | music | sfx | reference | video
    name               TEXT    NOT NULL,
    asset_id           TEXT    NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    description        TEXT    NOT NULL DEFAULT '',
    tags               TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
    meta               TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(meta)),  -- voice: text, consent, consent_note; lora: base_model, trigger
    source_asset_id    TEXT,
    source_project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    trashed_at         TEXT
);
CREATE UNIQUE INDEX uq_library_name ON library_items(role, name) WHERE trashed_at IS NULL;
CREATE INDEX idx_library_role ON library_items(role, trashed_at);
