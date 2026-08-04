-- 合并后的 Memory MCP 完整 schema（原 0001-0009 migration 折叠）。
-- 开发阶段已清空历史 migration 记录，因此可安全合并为单个文件。
-- 外键约束全部移除：owner/profile 引用完整性由应用层事务和 advisory lock 保证，
-- 不依赖数据库外键；CHECK 和 UNIQUE 约束保留。

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE memory_profiles (
    profile_id TEXT PRIMARY KEY,
    CONSTRAINT memory_profiles_non_empty
        CHECK (length(btrim(profile_id)) > 0)
);

CREATE TABLE memory_profile_types (
    profile_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    PRIMARY KEY (profile_id, memory_type),
    CONSTRAINT memory_profile_types_non_empty
        CHECK (length(btrim(memory_type)) > 0)
);

CREATE TABLE memory_profile_relations (
    profile_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    PRIMARY KEY (profile_id, relation_type),
    CONSTRAINT memory_profile_relations_type_non_empty
        CHECK (length(btrim(relation_type)) > 0)
);

CREATE TABLE memory_items (
    memory_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT memory_items_owner_non_empty
        CHECK (length(btrim(owner_id)) > 0),
    CONSTRAINT memory_items_subject_non_empty
        CHECK (length(btrim(subject)) > 0),
    CONSTRAINT memory_items_owner_identity UNIQUE (memory_id, owner_id),
    CONSTRAINT memory_items_owner_profile_identity
        UNIQUE (memory_id, owner_id, profile_id)
);

CREATE INDEX memory_items_owner_scope_idx
    ON memory_items (owner_id, profile_id, subject, memory_type);

CREATE INDEX memory_items_owner_profile_type_idx
    ON memory_items (owner_id, profile_id, memory_type, created_at);

CREATE INDEX memory_items_recall_subject_trgm_idx
    ON memory_items USING GIN (lower(subject) gin_trgm_ops);

CREATE TABLE memory_revisions (
    revision_id UUID PRIMARY KEY,
    memory_id UUID NOT NULL,
    owner_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    assertion_kind TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    business_progress TEXT,
    save_rationale TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    primary_evidence_id UUID NOT NULL,
    original_time_expression TEXT,
    normalized_time TIMESTAMPTZ,
    extraction_confidence DOUBLE PRECISION,
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    sensitivity_level TEXT NOT NULL DEFAULT 'confidential',
    valid_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    valid_until TIMESTAMPTZ,
    CONSTRAINT memory_revisions_positive_number
        CHECK (revision_number > 0),
    CONSTRAINT memory_revisions_content_non_empty
        CHECK (length(btrim(content)) > 0),
    CONSTRAINT memory_revisions_rationale_non_empty
        CHECK (length(btrim(save_rationale)) > 0),
    CONSTRAINT memory_revisions_assertion_kind
        CHECK (
            assertion_kind IN (
                'user_view',
                'user_provided_fact',
                'external_fact',
                'system_inference'
            )
        ),
    CONSTRAINT memory_revisions_lifecycle_status
        CHECK (
            lifecycle_status IN (
                'active',
                'superseded',
                'expired',
                'revoked'
            )
        ),
    CONSTRAINT memory_revisions_extraction_confidence
        CHECK (
            extraction_confidence IS NULL
            OR extraction_confidence BETWEEN 0.0 AND 1.0
        ),
    CONSTRAINT memory_revisions_verification_status
        CHECK (
            verification_status IN (
                'unverified',
                'user_asserted',
                'user_confirmed',
                'source_verified'
            )
        ),
    CONSTRAINT memory_revisions_sensitivity_level
        CHECK (
            sensitivity_level IN (
                'public',
                'internal',
                'confidential',
                'restricted'
            )
        ),
    CONSTRAINT memory_revisions_valid_window
        CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT memory_revisions_number_unique
        UNIQUE (memory_id, revision_number),
    CONSTRAINT memory_revisions_owner_identity
        UNIQUE (revision_id, memory_id, owner_id),
    embedding vector(1024)
);

CREATE UNIQUE INDEX memory_revisions_one_current_idx
    ON memory_revisions (memory_id)
    WHERE is_current;

CREATE INDEX memory_revisions_owner_status_idx
    ON memory_revisions (owner_id, lifecycle_status)
    WHERE is_current;

CREATE INDEX memory_revisions_current_active_idx
    ON memory_revisions (owner_id, memory_id, revision_number)
    WHERE is_current AND lifecycle_status = 'active';

CREATE INDEX memory_revisions_owner_effective_idx
    ON memory_revisions (owner_id, lifecycle_status, valid_from, valid_until)
    WHERE is_current;

CREATE INDEX memory_revisions_recall_content_trgm_idx
    ON memory_revisions USING GIN (lower(content) gin_trgm_ops)
    WHERE is_current AND lifecycle_status = 'active';

CREATE INDEX memory_revisions_embedding_idx
    ON memory_revisions USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100)
    WHERE is_current AND lifecycle_status = 'active';

CREATE INDEX memory_revisions_maintenance_expiry_idx
    ON memory_revisions (valid_until, owner_id, memory_id)
    WHERE is_current
      AND lifecycle_status = 'active'
      AND valid_until IS NOT NULL;

CREATE TABLE memory_evidence (
    evidence_id UUID PRIMARY KEY,
    memory_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    owner_id TEXT NOT NULL,
    conversation_id TEXT,
    source_turn_id TEXT NOT NULL,
    source_expression TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    source_role TEXT,
    source_message_id TEXT,
    source_tool_name TEXT,
    source_type TEXT NOT NULL DEFAULT 'conversation',
    CONSTRAINT memory_evidence_conversation_non_empty
        CHECK (conversation_id IS NULL OR length(btrim(conversation_id)) > 0),
    CONSTRAINT memory_evidence_turn_non_empty
        CHECK (length(btrim(source_turn_id)) > 0),
    CONSTRAINT memory_evidence_expression_non_empty
        CHECK (length(btrim(source_expression)) > 0),
    CONSTRAINT memory_evidence_source_role
        CHECK (
            source_role IS NULL
            OR source_role IN ('user', 'assistant', 'tool')
        ),
    CONSTRAINT memory_evidence_source_message_non_empty
        CHECK (
            source_message_id IS NULL
            OR length(btrim(source_message_id)) > 0
        ),
    CONSTRAINT memory_evidence_source_tool_non_empty
        CHECK (
            source_tool_name IS NULL
            OR length(btrim(source_tool_name)) > 0
        ),
    CONSTRAINT memory_evidence_tool_role
        CHECK (source_tool_name IS NULL OR source_role = 'tool'),
    CONSTRAINT memory_evidence_source_type
        CHECK (source_type IN ('conversation', 'tool', 'document', 'web')),
    CONSTRAINT memory_evidence_revision_identity
        UNIQUE (evidence_id, revision_id, memory_id, owner_id)
);

CREATE INDEX memory_evidence_owner_revision_idx
    ON memory_evidence (owner_id, revision_id);

CREATE TABLE memory_evidence_documents (
    evidence_id UUID PRIMARY KEY,
    source_uri TEXT,
    source_title TEXT,
    source_publisher TEXT,
    published_at TIMESTAMPTZ,
    retrieved_at TIMESTAMPTZ,
    content_hash TEXT,
    citation_locator TEXT,
    CONSTRAINT memory_evidence_documents_uri_non_empty
        CHECK (source_uri IS NULL OR length(btrim(source_uri)) > 0),
    CONSTRAINT memory_evidence_documents_title_non_empty
        CHECK (source_title IS NULL OR length(btrim(source_title)) > 0),
    CONSTRAINT memory_evidence_documents_publisher_non_empty
        CHECK (source_publisher IS NULL OR length(btrim(source_publisher)) > 0),
    CONSTRAINT memory_evidence_documents_hash_non_empty
        CHECK (content_hash IS NULL OR length(btrim(content_hash)) > 0),
    CONSTRAINT memory_evidence_documents_locator_non_empty
        CHECK (citation_locator IS NULL OR length(btrim(citation_locator)) > 0)
);

CREATE TABLE memory_capture_runs (
    capture_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    profile_fingerprint TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL,
    failure_code TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    event_id TEXT,
    contract_version TEXT,
    payload_fingerprint TEXT,
    CONSTRAINT memory_capture_runs_owner_non_empty
        CHECK (length(btrim(owner_id)) > 0),
    CONSTRAINT memory_capture_runs_conversation_non_empty
        CHECK (length(btrim(conversation_id)) > 0),
    CONSTRAINT memory_capture_runs_turn_non_empty
        CHECK (length(btrim(source_turn_id)) > 0),
    CONSTRAINT memory_capture_runs_profile_version_non_empty
        CHECK (length(btrim(profile_version)) > 0),
    CONSTRAINT memory_capture_runs_profile_fingerprint_non_empty
        CHECK (length(btrim(profile_fingerprint)) > 0),
    CONSTRAINT memory_capture_runs_prompt_non_empty
        CHECK (length(btrim(prompt_version)) > 0),
    CONSTRAINT memory_capture_runs_schema_non_empty
        CHECK (length(btrim(schema_version)) > 0),
    CONSTRAINT memory_capture_runs_model_non_empty
        CHECK (length(btrim(model_id)) > 0),
    CONSTRAINT memory_capture_runs_status
        CHECK (
            status IN ('completed', 'failed', 'reprocess_required')
        ),
    CONSTRAINT memory_capture_runs_failure_state
        CHECK (
            (status = 'completed' AND failure_code IS NULL)
            OR
            (
                status IN ('failed', 'reprocess_required')
                AND length(btrim(failure_code)) > 0
            )
        ),
    CONSTRAINT memory_capture_runs_event_shape
        CHECK (
            (
                event_id IS NULL
                AND contract_version IS NULL
                AND payload_fingerprint IS NULL
            )
            OR
            (
                length(btrim(event_id)) > 0
                AND length(btrim(contract_version)) > 0
                AND length(btrim(payload_fingerprint)) > 0
            )
        ),
    CONSTRAINT memory_capture_runs_owner_identity
        UNIQUE (capture_id, owner_id)
);

CREATE UNIQUE INDEX memory_capture_runs_source_unique
    ON memory_capture_runs (
        owner_id,
        profile_id,
        conversation_id,
        source_turn_id
    )
    WHERE event_id IS NULL;

CREATE UNIQUE INDEX memory_capture_runs_event_unique
    ON memory_capture_runs (owner_id, event_id)
    WHERE event_id IS NOT NULL;

CREATE INDEX memory_capture_runs_owner_status_idx
    ON memory_capture_runs (owner_id, status, completed_at);

CREATE TABLE memory_review_items (
    review_id UUID PRIMARY KEY,
    candidate_id UUID NOT NULL,
    capture_id UUID NOT NULL,
    owner_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    assertion_kind TEXT NOT NULL,
    business_progress TEXT,
    conversation_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    source_expression TEXT NOT NULL,
    save_rationale TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    durability TEXT NOT NULL,
    expression_basis TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    candidate_created_at TIMESTAMPTZ NOT NULL,
    original_time_expression TEXT,
    normalized_time TIMESTAMPTZ,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ,
    resolved_memory_id UUID,
    source_role TEXT,
    source_message_id TEXT,
    source_tool_name TEXT,
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    sensitivity_level TEXT NOT NULL DEFAULT 'confidential',
    valid_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    valid_until TIMESTAMPTZ,
    source_type TEXT NOT NULL DEFAULT 'conversation',
    CONSTRAINT memory_review_items_content_non_empty
        CHECK (length(btrim(content)) > 0),
    CONSTRAINT memory_review_items_source_non_empty
        CHECK (length(btrim(source_expression)) > 0),
    CONSTRAINT memory_review_items_rationale_non_empty
        CHECK (length(btrim(save_rationale)) > 0),
    CONSTRAINT memory_review_items_confidence
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CONSTRAINT memory_review_items_assertion_kind
        CHECK (
            assertion_kind IN (
                'user_view',
                'user_provided_fact',
                'external_fact',
                'system_inference'
            )
        ),
    CONSTRAINT memory_review_items_durability
        CHECK (durability IN ('durable', 'uncertain', 'temporary')),
    CONSTRAINT memory_review_items_expression_basis
        CHECK (expression_basis IN ('explicit', 'inferred', 'ambiguous')),
    CONSTRAINT memory_review_items_source_role
        CHECK (
            source_role IS NULL
            OR source_role IN ('user', 'assistant', 'tool')
        ),
    CONSTRAINT memory_review_items_source_message_non_empty
        CHECK (
            source_message_id IS NULL
            OR length(btrim(source_message_id)) > 0
        ),
    CONSTRAINT memory_review_items_source_tool_non_empty
        CHECK (
            source_tool_name IS NULL
            OR length(btrim(source_tool_name)) > 0
        ),
    CONSTRAINT memory_review_items_tool_role
        CHECK (source_tool_name IS NULL OR source_role = 'tool'),
    CONSTRAINT memory_review_items_status
        CHECK (status IN ('pending', 'confirmed', 'rejected', 'expired')),
    CONSTRAINT memory_review_items_decision_state
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
        ),
    CONSTRAINT memory_review_items_verification_status
        CHECK (
            verification_status IN (
                'unverified',
                'user_asserted',
                'user_confirmed',
                'source_verified'
            )
        ),
    CONSTRAINT memory_review_items_sensitivity_level
        CHECK (
            sensitivity_level IN (
                'public',
                'internal',
                'confidential',
                'restricted'
            )
        ),
    CONSTRAINT memory_review_items_valid_window
        CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT memory_review_items_source_type
        CHECK (source_type IN ('conversation', 'tool', 'document', 'web')),
    CONSTRAINT memory_review_items_capture_candidate_unique
        UNIQUE (capture_id, candidate_id),
    CONSTRAINT memory_review_items_owner_identity
        UNIQUE (review_id, owner_id)
);

CREATE INDEX memory_review_items_owner_status_idx
    ON memory_review_items (owner_id, status, created_at);

CREATE INDEX memory_review_items_resolved_memory_idx
    ON memory_review_items (owner_id, resolved_memory_id)
    WHERE resolved_memory_id IS NOT NULL;

CREATE INDEX memory_review_items_maintenance_idx
    ON memory_review_items (valid_until, created_at, owner_id, review_id)
    WHERE status = 'pending';

CREATE TABLE memory_review_item_documents (
    review_id UUID PRIMARY KEY,
    source_uri TEXT,
    source_title TEXT,
    source_publisher TEXT,
    published_at TIMESTAMPTZ,
    retrieved_at TIMESTAMPTZ,
    content_hash TEXT,
    citation_locator TEXT,
    CONSTRAINT memory_review_item_documents_uri_non_empty
        CHECK (source_uri IS NULL OR length(btrim(source_uri)) > 0),
    CONSTRAINT memory_review_item_documents_title_non_empty
        CHECK (source_title IS NULL OR length(btrim(source_title)) > 0),
    CONSTRAINT memory_review_item_documents_publisher_non_empty
        CHECK (source_publisher IS NULL OR length(btrim(source_publisher)) > 0),
    CONSTRAINT memory_review_item_documents_hash_non_empty
        CHECK (content_hash IS NULL OR length(btrim(content_hash)) > 0),
    CONSTRAINT memory_review_item_documents_locator_non_empty
        CHECK (citation_locator IS NULL OR length(btrim(citation_locator)) > 0)
);

CREATE TABLE memory_relations (
    relation_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    source_memory_id UUID NOT NULL,
    target_memory_id UUID NOT NULL,
    relation_type TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'legacy',
    scope TEXT NOT NULL DEFAULT 'item',
    source_revision_id UUID,
    target_revision_id UUID,
    capture_id UUID,
    conversation_id TEXT,
    source_turn_id TEXT,
    source_expression TEXT,
    confidence DOUBLE PRECISION,
    expression_basis TEXT,
    model_id TEXT,
    prompt_version TEXT,
    schema_version TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    stale_at TIMESTAMPTZ,
    stale_reason TEXT,
    CONSTRAINT memory_relations_owner_non_empty
        CHECK (length(btrim(owner_id)) > 0),
    CONSTRAINT memory_relations_type_non_empty
        CHECK (length(btrim(relation_type)) > 0),
    CONSTRAINT memory_relations_not_self
        CHECK (source_memory_id <> target_memory_id),
    CONSTRAINT memory_relations_origin
        CHECK (origin IN ('legacy', 'manual', 'automatic')),
    CONSTRAINT memory_relations_scope
        CHECK (scope IN ('item', 'revision')),
    CONSTRAINT memory_relations_status
        CHECK (status IN ('active', 'stale', 'revoked')),
    CONSTRAINT memory_relations_provenance_state
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
    CONSTRAINT memory_relations_terminal_state
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
        )
);

CREATE UNIQUE INDEX memory_relations_one_active_idx
    ON memory_relations (
        owner_id,
        source_memory_id,
        target_memory_id,
        relation_type
    )
    WHERE status = 'active';

CREATE INDEX memory_relations_owner_profile_idx
    ON memory_relations (owner_id, profile_id, status, created_at);

CREATE INDEX memory_relations_owner_source_idx
    ON memory_relations (owner_id, source_memory_id, status);

CREATE INDEX memory_relations_owner_target_idx
    ON memory_relations (owner_id, target_memory_id, status);

CREATE INDEX memory_relations_owner_revision_scope_idx
    ON memory_relations (
        owner_id,
        scope,
        status,
        source_memory_id,
        target_memory_id
    )
    WHERE scope = 'revision' AND status = 'active';

CREATE TABLE memory_capture_outcomes (
    capture_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    owner_id TEXT NOT NULL,
    outcome_order INTEGER NOT NULL,
    decision TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    memory_id UUID,
    review_id UUID,
    PRIMARY KEY (capture_id, candidate_id),
    CONSTRAINT memory_capture_outcomes_order
        CHECK (outcome_order >= 0),
    CONSTRAINT memory_capture_outcomes_decision
        CHECK (
            decision IN ('auto_save', 'pending', 'discard', 'blocked')
        ),
    CONSTRAINT memory_capture_outcomes_reason_non_empty
        CHECK (length(btrim(reason_code)) > 0),
    CONSTRAINT memory_capture_outcomes_reference_shape
        CHECK (
            (
                decision = 'auto_save'
                AND memory_id IS NOT NULL
                AND review_id IS NULL
            )
            OR
            (
                decision = 'pending'
                AND memory_id IS NULL
                AND review_id IS NOT NULL
            )
            OR
            (
                decision IN ('discard', 'blocked')
                AND memory_id IS NULL
                AND review_id IS NULL
            )
        )
);

CREATE INDEX memory_capture_outcomes_owner_decision_idx
    ON memory_capture_outcomes (owner_id, decision);

CREATE TABLE memory_team_extraction_runs (
    run_id UUID PRIMARY KEY,
    team_owner_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    member_count INTEGER NOT NULL DEFAULT 0,
    memory_count INTEGER NOT NULL DEFAULT 0,
    cluster_count INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
