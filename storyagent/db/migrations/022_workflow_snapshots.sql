-- 022_workflow_snapshots: one-click undo for stages that rewrite story tables in place (seasons, episodes).
-- Before a stage like ``outline`` runs it snapshots the rows it will touch; restoring a snapshot puts them back.
CREATE TABLE workflow_snapshots (
    id          TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL,
    stage        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE INDEX idx_workflow_snapshots_project ON workflow_snapshots(project_id, created_at DESC);
