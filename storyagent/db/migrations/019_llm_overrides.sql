-- 019_llm_overrides: the language model chosen for a role from the screen, without a restart. scope 'global' applies to
-- every project, 'project:<id>' to one project (which wins). Without a row the role comes from llm.toml.
CREATE TABLE llm_overrides (
    scope      TEXT NOT NULL,
    role       TEXT NOT NULL,
    backend    TEXT NOT NULL,
    model      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (scope, role)
);
