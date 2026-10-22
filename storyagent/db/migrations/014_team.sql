-- 014_team: accounts, sign-in sessions and the audit trail. Passwords are salted scrypt hashes; a session token is
-- only ever stored as its SHA-256 (the plain token is shown once, at login). audit_log is append-only.
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT    NOT NULL DEFAULT '',
    role          TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until  TEXT,
    last_login_at TEXT,
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE user_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT    NOT NULL UNIQUE,
    label        TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    expires_at   TEXT    NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX idx_sessions_user ON user_sessions(user_id);
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor       TEXT    NOT NULL DEFAULT '',          -- username at the time (or 'local' with auth off, 'system')
    action      TEXT    NOT NULL,                     -- 'http.POST' or a named event ('auth.login', 'user.create', ...)
    path        TEXT    NOT NULL DEFAULT '',
    project_id  INTEGER,
    status      INTEGER,
    detail      TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(detail)),
    client      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX idx_audit_at ON audit_log(at);
CREATE INDEX idx_audit_user ON audit_log(user_id);
CREATE INDEX idx_audit_project ON audit_log(project_id);
