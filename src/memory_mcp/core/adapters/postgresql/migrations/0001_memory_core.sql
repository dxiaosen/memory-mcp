CREATE TABLE memory_scenarios (
    scenario_id TEXT PRIMARY KEY,
    CONSTRAINT memory_scenarios_non_empty
        CHECK (length(btrim(scenario_id)) > 0)
);

CREATE TABLE memory_scenario_types (
    scenario_id TEXT NOT NULL
        REFERENCES memory_scenarios (scenario_id) ON DELETE RESTRICT,
    memory_type TEXT NOT NULL,
    PRIMARY KEY (scenario_id, memory_type),
    CONSTRAINT memory_scenario_types_non_empty
        CHECK (length(btrim(memory_type)) > 0)
);

CREATE TABLE memory_items (
    memory_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    scenario TEXT NOT NULL,
    subject TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT memory_items_owner_non_empty
        CHECK (length(btrim(owner_id)) > 0),
    CONSTRAINT memory_items_subject_non_empty
        CHECK (length(btrim(subject)) > 0),
    CONSTRAINT memory_items_registered_type
        FOREIGN KEY (scenario, memory_type)
        REFERENCES memory_scenario_types (scenario_id, memory_type)
        ON DELETE RESTRICT,
    CONSTRAINT memory_items_owner_identity UNIQUE (memory_id, owner_id)
);

CREATE INDEX memory_items_owner_scope_idx
    ON memory_items (owner_id, scenario, subject, memory_type);

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
    CONSTRAINT memory_revisions_owned_memory
        FOREIGN KEY (memory_id, owner_id)
        REFERENCES memory_items (memory_id, owner_id)
        ON DELETE CASCADE,
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
    CONSTRAINT memory_revisions_number_unique
        UNIQUE (memory_id, revision_number),
    CONSTRAINT memory_revisions_owner_identity
        UNIQUE (revision_id, memory_id, owner_id)
);

CREATE UNIQUE INDEX memory_revisions_one_current_idx
    ON memory_revisions (memory_id)
    WHERE is_current;

CREATE INDEX memory_revisions_owner_status_idx
    ON memory_revisions (owner_id, lifecycle_status)
    WHERE is_current;

CREATE TABLE memory_evidence (
    evidence_id UUID PRIMARY KEY,
    memory_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    owner_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    source_expression TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    source_role TEXT,
    source_message_id TEXT,
    source_tool_name TEXT,
    CONSTRAINT memory_evidence_owned_revision
        FOREIGN KEY (revision_id, memory_id, owner_id)
        REFERENCES memory_revisions (revision_id, memory_id, owner_id)
        ON DELETE CASCADE,
    CONSTRAINT memory_evidence_conversation_non_empty
        CHECK (length(btrim(conversation_id)) > 0),
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
    CONSTRAINT memory_evidence_revision_identity
        UNIQUE (evidence_id, revision_id, memory_id, owner_id)
);

ALTER TABLE memory_revisions
    ADD CONSTRAINT memory_revisions_primary_evidence
    FOREIGN KEY (
        primary_evidence_id,
        revision_id,
        memory_id,
        owner_id
    )
    REFERENCES memory_evidence (
        evidence_id,
        revision_id,
        memory_id,
        owner_id
    )
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX memory_evidence_owner_revision_idx
    ON memory_evidence (owner_id, revision_id);

CREATE TABLE memory_capture_runs (
    capture_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    scenario TEXT NOT NULL
        REFERENCES memory_scenarios (scenario_id) ON DELETE RESTRICT,
    conversation_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
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
    CONSTRAINT memory_capture_runs_policy_non_empty
        CHECK (length(btrim(policy_version)) > 0),
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
    CONSTRAINT memory_capture_runs_source_unique
        UNIQUE (
            owner_id,
            scenario,
            conversation_id,
            source_turn_id,
            policy_version
        ),
    CONSTRAINT memory_capture_runs_owner_identity
        UNIQUE (capture_id, owner_id)
);

CREATE UNIQUE INDEX memory_capture_runs_event_unique
    ON memory_capture_runs (
        owner_id,
        scenario,
        event_id,
        policy_version
    )
    WHERE event_id IS NOT NULL;

CREATE INDEX memory_capture_runs_owner_status_idx
    ON memory_capture_runs (owner_id, status, completed_at);

CREATE TABLE memory_review_items (
    review_id UUID PRIMARY KEY,
    candidate_id UUID NOT NULL,
    capture_id UUID NOT NULL,
    owner_id TEXT NOT NULL,
    scenario TEXT NOT NULL,
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
    CONSTRAINT memory_review_items_owned_capture
        FOREIGN KEY (capture_id, owner_id)
        REFERENCES memory_capture_runs (capture_id, owner_id)
        ON DELETE CASCADE,
    CONSTRAINT memory_review_items_registered_type
        FOREIGN KEY (scenario, memory_type)
        REFERENCES memory_scenario_types (scenario_id, memory_type)
        ON DELETE RESTRICT,
    CONSTRAINT memory_review_items_owned_memory
        FOREIGN KEY (resolved_memory_id, owner_id)
        REFERENCES memory_items (memory_id, owner_id)
        ON DELETE RESTRICT,
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
        CHECK (status IN ('pending', 'confirmed', 'rejected')),
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
                status = 'rejected'
                AND decided_at IS NOT NULL
                AND resolved_memory_id IS NULL
            )
        ),
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
    CONSTRAINT memory_capture_outcomes_owned_capture
        FOREIGN KEY (capture_id, owner_id)
        REFERENCES memory_capture_runs (capture_id, owner_id)
        ON DELETE CASCADE,
    CONSTRAINT memory_capture_outcomes_owned_memory
        FOREIGN KEY (memory_id, owner_id)
        REFERENCES memory_items (memory_id, owner_id)
        ON DELETE RESTRICT,
    CONSTRAINT memory_capture_outcomes_owned_review
        FOREIGN KEY (review_id, owner_id)
        REFERENCES memory_review_items (review_id, owner_id)
        ON DELETE RESTRICT,
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
