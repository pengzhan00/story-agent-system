-- 018_job_error_params: the values that fill the placeholders of a failed job's error text ({model}, {tool} ...), kept
-- with the job so every screen can show the localized message complete (before this they lived only in job_events).
ALTER TABLE media_jobs ADD COLUMN error_params TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(error_params));
