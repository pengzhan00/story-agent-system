-- The standing requirements an author gives the AI assistant of a workshop module (one text per module: song, image,
-- video ...). The assistant reads them on every call, together with the wiki and what the author asks this time.
CREATE TABLE workshop_assist (
    module       TEXT PRIMARY KEY,
    requirements TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL,
    updated_by   TEXT NOT NULL DEFAULT ''
);
