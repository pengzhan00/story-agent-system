-- 006_workshop: every module can run on its own (jobs without a project) or as a step of a pipeline.
--   media_jobs.depends_on    job that must have completed first; its output is addressed as "job:<id>" in inputs
--   media_jobs.pipeline_id   jobs submitted together as one pipeline (pipeline_step = position, from 1)
--   workshop_presets         the model, quality tier and parameter overrides a module starts from (per media kind)
ALTER TABLE media_jobs ADD COLUMN depends_on TEXT;
ALTER TABLE media_jobs ADD COLUMN pipeline_id TEXT;
ALTER TABLE media_jobs ADD COLUMN pipeline_step INTEGER;
CREATE INDEX idx_media_jobs_depends ON media_jobs(depends_on) WHERE depends_on IS NOT NULL;
CREATE INDEX idx_media_jobs_pipeline ON media_jobs(pipeline_id) WHERE pipeline_id IS NOT NULL;

CREATE TABLE workshop_presets (
    kind       TEXT PRIMARY KEY,
    model_ref  TEXT NOT NULL DEFAULT '',
    tier       TEXT NOT NULL DEFAULT '',
    params     TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
