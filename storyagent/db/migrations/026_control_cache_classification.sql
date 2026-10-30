-- 025 was applied to the production database before its last two additions were written, and an applied migration is
-- never edited (its checksum is checked on every start). The additions live here: the cache of shared control-graph
-- results, and the classification a publication revision was filed under.
CREATE TABLE control_cache (
    cache_key TEXT PRIMARY KEY, outputs TEXT NOT NULL, created_at TEXT NOT NULL
);
ALTER TABLE publication_revisions ADD COLUMN classification TEXT NOT NULL DEFAULT '{}';
