-- 009_episode_outline: what the season outline says about an episode (read by the script stage).
ALTER TABLE episodes ADD COLUMN hook TEXT NOT NULL DEFAULT '';
ALTER TABLE episodes ADD COLUMN cliffhanger TEXT NOT NULL DEFAULT '';
