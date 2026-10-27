-- Removing a terminal job from history must retain provenance, dependencies and idempotency.
ALTER TABLE media_jobs ADD COLUMN history_removed_at TEXT;
CREATE INDEX idx_jobs_history ON media_jobs(history_removed_at, created_at DESC);
