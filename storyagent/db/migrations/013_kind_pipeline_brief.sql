-- 013_kind_pipeline_brief: what a project is (kind of work), which pipeline plan it runs on (one complete set of models,
-- chosen at creation), and the plan made up front (seasons, episodes, episode length, audience, platform).
-- Empty kind/pipeline = the configured defaults, so projects made before this keep working unchanged.
ALTER TABLE projects ADD COLUMN kind TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN pipeline TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN brief TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(brief));
