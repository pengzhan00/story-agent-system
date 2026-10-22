-- 020_age: accounts carry an age that an admin approves. The approved birth date is what counts (the age is worked out
-- from it every time, so it grows by itself); a claim is what the person said and waits for the review.
ALTER TABLE users ADD COLUMN birth_date       TEXT;
ALTER TABLE users ADD COLUMN age_status       TEXT NOT NULL DEFAULT 'none'
    CHECK (age_status IN ('none','pending','approved','rejected'));
ALTER TABLE users ADD COLUMN age_claim        TEXT;
ALTER TABLE users ADD COLUMN age_note         TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN age_reviewed_by  TEXT;
ALTER TABLE users ADD COLUMN age_reviewed_at  TEXT;
