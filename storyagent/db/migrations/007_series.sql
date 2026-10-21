-- 007_series: series > season > episode.
--   seasons         : a group of episodes with its own arc / planned length (every project has season 1)
--   episodes        : season + number within the season (episodes.number stays the series-wide key everything uses)
--   workflow_stages : state per scope. (season 0, episode 0) = the whole series (analyze, cast); an episode stage is
--                     kept per episode so episode 2 can run while episode 1 stays completed.

CREATE TABLE seasons (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    number           INTEGER NOT NULL DEFAULT 1,
    title            TEXT    NOT NULL DEFAULT '',
    summary          TEXT    NOT NULL DEFAULT '',
    planned_episodes INTEGER NOT NULL DEFAULT 0,
    target_sec       REAL,
    status           TEXT    NOT NULL DEFAULT 'draft',
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (project_id, number)
);
INSERT INTO seasons(project_id, number) SELECT id, 1 FROM projects;

ALTER TABLE episodes ADD COLUMN season INTEGER NOT NULL DEFAULT 1;
ALTER TABLE episodes ADD COLUMN number_in_season INTEGER NOT NULL DEFAULT 0;
UPDATE episodes SET number_in_season = number;

CREATE TABLE workflow_stages_new (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage       TEXT    NOT NULL,
    season      INTEGER NOT NULL DEFAULT 0,
    episode     INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','stale')),
    started_at  TEXT,
    finished_at TEXT,
    error_code  TEXT,
    error_message TEXT,
    summary     TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(summary)),
    attempts    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, stage, season, episode)
);
INSERT INTO workflow_stages_new(project_id, stage, season, episode, status, started_at, finished_at, error_code,
                                error_message, summary, attempts)
    SELECT project_id, stage,
           CASE WHEN stage IN ('analyze','cast') THEN 0 ELSE 1 END,
           CASE WHEN stage IN ('analyze','cast') THEN 0 ELSE 1 END,
           status, started_at, finished_at, error_code, error_message, summary, attempts
    FROM workflow_stages;
DROP TABLE workflow_stages;
ALTER TABLE workflow_stages_new RENAME TO workflow_stages;
