CREATE TABLE control_templates (
    id TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL,
    definition TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(id, version)
);
CREATE TABLE control_runs (
    id TEXT PRIMARY KEY, project_id INTEGER REFERENCES projects(id), name TEXT NOT NULL,
    plan TEXT NOT NULL, digest TEXT NOT NULL, request_key TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued', cancel_requested INTEGER NOT NULL DEFAULT 0,
    actor TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX control_runs_status ON control_runs(status, created_at);
CREATE TABLE control_nodes (
    run_id TEXT NOT NULL REFERENCES control_runs(id), node_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 1,
    job_id TEXT REFERENCES media_jobs(id), outputs TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(run_id, node_id), UNIQUE(job_id)
);
CREATE TABLE control_attempts (
    run_id TEXT NOT NULL, node_id TEXT NOT NULL, attempt INTEGER NOT NULL,
    job_id TEXT REFERENCES media_jobs(id), status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, node_id, attempt),
    FOREIGN KEY(run_id,node_id) REFERENCES control_nodes(run_id,node_id)
);
CREATE TABLE control_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES control_runs(id),
    node_id TEXT, event TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE asset_relations (
    parent_id TEXT NOT NULL REFERENCES media_assets(id),
    child_id TEXT NOT NULL REFERENCES media_assets(id), role TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
    PRIMARY KEY(parent_id,child_id,role), CHECK(parent_id != child_id)
);
CREATE TABLE asset_selections (
    owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, slot TEXT NOT NULL,
    asset_id TEXT NOT NULL REFERENCES media_assets(id), revision INTEGER NOT NULL,
    actor TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(owner_type,owner_id,slot)
);
CREATE TABLE publication_targets (
    id TEXT PRIMARY KEY, platform TEXT NOT NULL, account TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE(platform,account,channel)
);
CREATE TABLE publication_categories (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE publications (
    id TEXT PRIMARY KEY, target_id TEXT NOT NULL REFERENCES publication_targets(id),
    project_id INTEGER REFERENCES projects(id), category_id TEXT REFERENCES publication_categories(id),
    status TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL DEFAULT 1,
    external_id TEXT, url TEXT NOT NULL DEFAULT '',
    actual_published_at TEXT, scheduled_at TEXT, timezone TEXT NOT NULL DEFAULT 'UTC',
    confirmation TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL,
    request_key TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX publication_external_id ON publications(target_id,external_id)
    WHERE external_id IS NOT NULL AND external_id != '';
CREATE TABLE publication_revisions (
    publication_id TEXT NOT NULL REFERENCES publications(id), revision INTEGER NOT NULL,
    title TEXT NOT NULL, body TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
    items TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(publication_id,revision)
);
CREATE TABLE publication_items (
    publication_id TEXT NOT NULL, revision INTEGER NOT NULL, position INTEGER NOT NULL,
    asset_id TEXT NOT NULL REFERENCES media_assets(id), role TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(publication_id,revision,position),
    FOREIGN KEY(publication_id,revision) REFERENCES publication_revisions(publication_id,revision)
);
CREATE INDEX publication_asset_lookup ON publication_items(asset_id);
CREATE TABLE publication_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id TEXT NOT NULL REFERENCES publications(id), revision INTEGER NOT NULL,
    event TEXT NOT NULL, detail TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
