-- 021_wiki: the in-system wiki. Pages are copied in by `storyagent wiki sync` from the repository's docs and from the
-- notes of an Obsidian vault the operator names (an allow-list, see wiki.toml); the source files are never touched.
CREATE TABLE wiki_pages (
    slug          TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    path          TEXT NOT NULL,
    title         TEXT NOT NULL,
    rating        TEXT NOT NULL DEFAULT 'general',
    tags          TEXT NOT NULL DEFAULT '[]',
    summary       TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL,
    meta          TEXT NOT NULL DEFAULT '{}',
    content_hash  TEXT NOT NULL,
    source_mtime  TEXT,
    synced_at     TEXT NOT NULL
);
CREATE INDEX idx_wiki_pages_source ON wiki_pages(source);

CREATE TABLE wiki_links (
    src TEXT NOT NULL REFERENCES wiki_pages(slug) ON DELETE CASCADE,
    dst TEXT NOT NULL,
    PRIMARY KEY (src, dst)
);
CREATE INDEX idx_wiki_links_dst ON wiki_links(dst);
