CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE memory_review_items
    DROP CONSTRAINT memory_review_items_status,
    DROP CONSTRAINT memory_review_items_decision_state,
    ADD CONSTRAINT memory_review_items_status
        CHECK (status IN ('pending', 'confirmed', 'rejected', 'expired')),
    ADD CONSTRAINT memory_review_items_decision_state
        CHECK (
            (
                status = 'pending'
                AND decided_at IS NULL
                AND resolved_memory_id IS NULL
            )
            OR
            (
                status = 'confirmed'
                AND decided_at IS NOT NULL
                AND resolved_memory_id IS NOT NULL
            )
            OR
            (
                status IN ('rejected', 'expired')
                AND decided_at IS NOT NULL
                AND resolved_memory_id IS NULL
            )
        );

ALTER TABLE memory_relations
    DROP CONSTRAINT memory_relations_terminal_state,
    ADD CONSTRAINT memory_relations_terminal_state
        CHECK (
            (
                status = 'active'
                AND revoked_at IS NULL
                AND stale_at IS NULL
                AND stale_reason IS NULL
            )
            OR
            (
                status = 'stale'
                AND revoked_at IS NULL
                AND stale_at IS NOT NULL
                AND stale_at >= created_at
                AND stale_reason IS NOT NULL
                AND length(btrim(stale_reason)) > 0
            )
            OR
            (
                status = 'revoked'
                AND revoked_at IS NOT NULL
                AND revoked_at >= created_at
                AND (
                    (stale_at IS NULL AND stale_reason IS NULL)
                    OR
                    (
                        stale_at IS NOT NULL
                        AND stale_at >= created_at
                        AND stale_reason IS NOT NULL
                        AND length(btrim(stale_reason)) > 0
                    )
                )
            )
        );

CREATE INDEX memory_items_recall_subject_trgm_idx
    ON memory_items USING GIN (lower(subject) gin_trgm_ops);

CREATE INDEX memory_revisions_recall_content_trgm_idx
    ON memory_revisions USING GIN (lower(content) gin_trgm_ops)
    WHERE is_current AND lifecycle_status = 'active';

CREATE INDEX memory_revisions_maintenance_expiry_idx
    ON memory_revisions (valid_until, owner_id, memory_id)
    WHERE is_current
      AND lifecycle_status = 'active'
      AND valid_until IS NOT NULL;

CREATE INDEX memory_review_items_maintenance_idx
    ON memory_review_items (valid_until, created_at, owner_id, review_id)
    WHERE status = 'pending';
