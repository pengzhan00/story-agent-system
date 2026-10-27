-- A shot's video ends on some other shot's keyframe so consecutive clips join; NULL = automatic (the next shot),
-- 0 = cut free (only the first frame is controlled), >0 = end on that shot's keyframe.
ALTER TABLE shots ADD COLUMN end_shot_id INTEGER;
