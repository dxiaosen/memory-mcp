ALTER TABLE memory_revisions
    ADD COLUMN original_time_expression TEXT;

ALTER TABLE memory_revisions
    ADD COLUMN normalized_time TEXT;

CREATE TABLE memory_capture_runs (
    capture_id TEXT PRIMARY KEY,
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
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    CONSTRAINT memory_capture_runs_owner_non_empty
        CHECK (length(trim(owner_id)) > 0),
    CONSTRAINT memory_capture_runs_conversation_non_empty
        CHECK (length(trim(conversation_id)) > 0),
    CONSTRAINT memory_capture_runs_turn_non_empty
        CHECK (length(trim(source_turn_id)) > 0),
    CONSTRAINT memory_capture_runs_policy_non_empty
        CHECK (length(trim(policy_version)) > 0),
    CONSTRAINT memory_capture_runs_prompt_non_empty
        CHECK (length(trim(prompt_version)) > 0),
    CONSTRAINT memory_capture_runs_schema_non_empty
        CHECK (length(trim(schema_version)) > 0),
    CONSTRAINT memory_capture_runs_model_non_empty
        CHECK (length(trim(model_id)) > 0),
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
                AND length(trim(failure_code)) > 0
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

CREATE INDEX memory_capture_runs_owner_status_idx
    ON memory_capture_runs (owner_id, status, completed_at);

CREATE TABLE memory_review_items (
    review_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    capture_id TEXT NOT NULL,
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
    confidence REAL NOT NULL,
    durability TEXT NOT NULL,
    expression_basis TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    candidate_created_at TEXT NOT NULL,
    original_time_expression TEXT,
    normalized_time TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    CONSTRAINT memory_review_items_owned_capture
        FOREIGN KEY (capture_id, owner_id)
        REFERENCES memory_capture_runs (capture_id, owner_id)
        ON DELETE CASCADE,
    CONSTRAINT memory_review_items_registered_type
        FOREIGN KEY (scenario, memory_type)
        REFERENCES memory_scenario_types (scenario_id, memory_type)
        ON DELETE RESTRICT,
    CONSTRAINT memory_review_items_content_non_empty
        CHECK (length(trim(content)) > 0),
    CONSTRAINT memory_review_items_source_non_empty
        CHECK (length(trim(source_expression)) > 0),
    CONSTRAINT memory_review_items_rationale_non_empty
        CHECK (length(trim(save_rationale)) > 0),
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
    CONSTRAINT memory_review_items_status
        CHECK (status IN ('pending', 'confirmed', 'rejected')),
    CONSTRAINT memory_review_items_decision_state
        CHECK (
            (status = 'pending' AND decided_at IS NULL)
            OR
            (status IN ('confirmed', 'rejected') AND decided_at IS NOT NULL)
        ),
    CONSTRAINT memory_review_items_capture_candidate_unique
        UNIQUE (capture_id, candidate_id),
    CONSTRAINT memory_review_items_owner_identity
        UNIQUE (review_id, owner_id)
);

CREATE INDEX memory_review_items_owner_status_idx
    ON memory_review_items (owner_id, status, created_at);

CREATE TABLE memory_capture_outcomes (
    capture_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    memory_id TEXT,
    review_id TEXT,
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
    CONSTRAINT memory_capture_outcomes_decision
        CHECK (
            decision IN ('auto_save', 'pending', 'discard', 'blocked')
        ),
    CONSTRAINT memory_capture_outcomes_reason_non_empty
        CHECK (length(trim(reason_code)) > 0),
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
