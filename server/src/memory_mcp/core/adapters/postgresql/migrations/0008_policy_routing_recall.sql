-- 记录策略指纹，并让捕获幂等身份独立于 Profile 版本。
ALTER TABLE memory_capture_runs
    ADD COLUMN profile_fingerprint TEXT;

UPDATE memory_capture_runs
SET profile_fingerprint = 'legacy-unknown'
WHERE profile_fingerprint IS NULL;

ALTER TABLE memory_capture_runs
    ALTER COLUMN profile_fingerprint SET NOT NULL;

ALTER TABLE memory_capture_runs
    ADD CONSTRAINT memory_capture_runs_profile_fingerprint_non_empty
    CHECK (length(btrim(profile_fingerprint)) > 0);

ALTER TABLE memory_capture_runs
    DROP CONSTRAINT memory_capture_runs_source_unique;

CREATE UNIQUE INDEX memory_capture_runs_source_unique
    ON memory_capture_runs (
        owner_id,
        profile_id,
        conversation_id,
        source_turn_id
    )
    WHERE event_id IS NULL;

DROP INDEX memory_capture_runs_event_unique;

CREATE UNIQUE INDEX memory_capture_runs_event_unique
    ON memory_capture_runs (owner_id, event_id)
    WHERE event_id IS NOT NULL;
