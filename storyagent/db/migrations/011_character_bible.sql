-- 011_character_bible: the series bible's character rows. introduced_episode = where the character first appears
-- (later episodes add characters); locked = the cast stage never overwrites a locked character (a force re-run
-- redesigns the rest).
ALTER TABLE characters ADD COLUMN introduced_episode INTEGER NOT NULL DEFAULT 1;
ALTER TABLE characters ADD COLUMN locked INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0,1));
