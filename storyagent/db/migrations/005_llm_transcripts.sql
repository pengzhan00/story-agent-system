-- 005_llm_transcripts: generation_logs keeps the whole conversation (it used to cut prompts at 500 characters and
-- answers at 2000), plus the system prompt and which agent asked, so every model call can be read back in full.
ALTER TABLE generation_logs ADD COLUMN system TEXT NOT NULL DEFAULT '';
ALTER TABLE generation_logs ADD COLUMN label  TEXT NOT NULL DEFAULT '';
