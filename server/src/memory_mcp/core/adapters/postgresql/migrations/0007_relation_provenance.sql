ALTER TABLE memory_relations
    ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN scope TEXT NOT NULL DEFAULT 'item',
    ADD COLUMN source_revision_id UUID,
    ADD COLUMN target_revision_id UUID,
    ADD COLUMN capture_id UUID,
    ADD COLUMN conversation_id TEXT,
    ADD COLUMN source_turn_id TEXT,
    ADD COLUMN source_expression TEXT,
    ADD COLUMN confidence DOUBLE PRECISION,
    ADD COLUMN expression_basis TEXT,
    ADD COLUMN model_id TEXT,
    ADD COLUMN prompt_version TEXT,
    ADD COLUMN schema_version TEXT,
    ADD COLUMN stale_at TIMESTAMPTZ,
    ADD COLUMN stale_reason TEXT;

ALTER TABLE memory_relations
    DROP CONSTRAINT memory_relations_status,
    DROP CONSTRAINT memory_relations_revocation_state,
    ADD CONSTRAINT memory_relations_origin
        CHECK (origin IN ('legacy', 'manual', 'automatic')),
    ADD CONSTRAINT memory_relations_scope
        CHECK (scope IN ('item', 'revision')),
    ADD CONSTRAINT memory_relations_status
        CHECK (status IN ('active', 'stale', 'revoked')),
    ADD CONSTRAINT memory_relations_source_revision
        FOREIGN KEY (source_revision_id, source_memory_id, owner_id)
        REFERENCES memory_revisions (revision_id, memory_id, owner_id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT memory_relations_target_revision
        FOREIGN KEY (target_revision_id, target_memory_id, owner_id)
        REFERENCES memory_revisions (revision_id, memory_id, owner_id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT memory_relations_capture_owner
        FOREIGN KEY (capture_id, owner_id)
        REFERENCES memory_capture_runs (capture_id, owner_id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT memory_relations_provenance_state
        CHECK (
            (
                origin = 'legacy'
                AND scope = 'item'
                AND source_revision_id IS NULL
                AND target_revision_id IS NULL
                AND capture_id IS NULL
                AND conversation_id IS NULL
                AND source_turn_id IS NULL
                AND source_expression IS NULL
                AND confidence IS NULL
                AND expression_basis IS NULL
                AND model_id IS NULL
                AND prompt_version IS NULL
                AND schema_version IS NULL
            )
            OR
            (
                origin = 'manual'
                AND scope = 'item'
                AND source_revision_id IS NOT NULL
                AND target_revision_id IS NOT NULL
                AND capture_id IS NULL
                AND conversation_id IS NULL
                AND source_turn_id IS NULL
                AND source_expression IS NULL
                AND confidence IS NULL
                AND expression_basis IS NULL
                AND model_id IS NULL
                AND prompt_version IS NULL
                AND schema_version IS NULL
            )
            OR
            (
                origin = 'automatic'
                AND scope = 'revision'
                AND source_revision_id IS NOT NULL
                AND target_revision_id IS NOT NULL
                AND capture_id IS NOT NULL
                AND conversation_id IS NOT NULL
                AND length(btrim(conversation_id)) > 0
                AND source_turn_id IS NOT NULL
                AND length(btrim(source_turn_id)) > 0
                AND source_expression IS NOT NULL
                AND length(btrim(source_expression)) > 0
                AND confidence IS NOT NULL
                AND confidence BETWEEN 0.0 AND 1.0
                AND expression_basis IS NOT NULL
                AND expression_basis = 'explicit'
                AND model_id IS NOT NULL
                AND length(btrim(model_id)) > 0
                AND prompt_version IS NOT NULL
                AND length(btrim(prompt_version)) > 0
                AND schema_version IS NOT NULL
                AND length(btrim(schema_version)) > 0
            )
        ),
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
                AND scope = 'revision'
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

CREATE INDEX memory_relations_owner_revision_scope_idx
    ON memory_relations (
        owner_id,
        scope,
        status,
        source_memory_id,
        target_memory_id
    )
    WHERE scope = 'revision' AND status = 'active';
