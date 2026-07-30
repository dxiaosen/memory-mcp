ALTER TABLE memory_capture_runs
    ADD COLUMN event_id TEXT;

ALTER TABLE memory_capture_runs
    ADD COLUMN contract_version TEXT;

ALTER TABLE memory_capture_runs
    ADD COLUMN payload_fingerprint TEXT;

CREATE UNIQUE INDEX memory_capture_runs_event_unique
    ON memory_capture_runs (
        owner_id,
        scenario,
        event_id,
        policy_version
    )
    WHERE event_id IS NOT NULL;

ALTER TABLE memory_review_items
    ADD COLUMN resolved_memory_id TEXT;

CREATE INDEX memory_review_items_resolved_memory_idx
    ON memory_review_items (owner_id, resolved_memory_id)
    WHERE resolved_memory_id IS NOT NULL;
