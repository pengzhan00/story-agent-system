-- 010_shot_treatment: how a shot is shown. 'auto' = the tiering policy decides (a few key shots get a video clip, the
-- rest are a keyframe with a slow move); 'video' / 'still' = the creator decided.
ALTER TABLE shots ADD COLUMN treatment TEXT NOT NULL DEFAULT 'auto' CHECK (treatment IN ('auto','video','still'));
