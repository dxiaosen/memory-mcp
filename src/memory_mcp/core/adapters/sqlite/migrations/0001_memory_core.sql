CREATE TABLE memory_scenarios (
    scenario_id TEXT PRIMARY KEY,
    CONSTRAINT memory_scenarios_non_empty
        CHECK (length(trim(scenario_id)) > 0)
);

CREATE TABLE memory_scenario_types (
    scenario_id TEXT NOT NULL
        REFERENCES memory_scenarios (scenario_id) ON DELETE RESTRICT,
    memory_type TEXT NOT NULL,
    PRIMARY KEY (scenario_id, memory_type),
    CONSTRAINT memory_scenario_types_non_empty
        CHECK (length(trim(memory_type)) > 0)
);

CREATE TABLE memory_items (
    memory_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    scenario TEXT NOT NULL,
    subject TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CONSTRAINT memory_items_owner_non_empty
        CHECK (length(trim(owner_id)) > 0),
    CONSTRAINT memory_items_subject_non_empty
        CHECK (length(trim(subject)) > 0),
    CONSTRAINT memory_items_registered_type
        FOREIGN KEY (scenario, memory_type)
        REFERENCES memory_scenario_types (scenario_id, memory_type)
        ON DELETE RESTRICT,
    CONSTRAINT memory_items_owner_identity UNIQUE (memory_id, owner_id)
);

CREATE INDEX memory_items_owner_scope_idx
    ON memory_items (owner_id, scenario, subject, memory_type);

CREATE TABLE memory_revisions (
    revision_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    assertion_kind TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    business_progress TEXT,
    save_rationale TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    primary_evidence_id TEXT NOT NULL,
    CONSTRAINT memory_revisions_owned_memory
        FOREIGN KEY (memory_id, owner_id)
        REFERENCES memory_items (memory_id, owner_id)
        ON DELETE CASCADE,
    CONSTRAINT memory_revisions_primary_evidence
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
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT memory_revisions_positive_number
        CHECK (revision_number > 0),
    CONSTRAINT memory_revisions_content_non_empty
        CHECK (length(trim(content)) > 0),
    CONSTRAINT memory_revisions_rationale_non_empty
        CHECK (length(trim(save_rationale)) > 0),
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
    CONSTRAINT memory_revisions_is_current
        CHECK (is_current IN (0, 1)),
    CONSTRAINT memory_revisions_number_unique
        UNIQUE (memory_id, revision_number),
    CONSTRAINT memory_revisions_owner_identity
        UNIQUE (revision_id, memory_id, owner_id)
);

CREATE UNIQUE INDEX memory_revisions_one_current_idx
    ON memory_revisions (memory_id)
    WHERE is_current = 1;

CREATE INDEX memory_revisions_owner_status_idx
    ON memory_revisions (owner_id, lifecycle_status)
    WHERE is_current = 1;

CREATE TABLE memory_evidence (
    evidence_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    source_expression TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CONSTRAINT memory_evidence_owned_revision
        FOREIGN KEY (revision_id, memory_id, owner_id)
        REFERENCES memory_revisions (revision_id, memory_id, owner_id)
        ON DELETE CASCADE,
    CONSTRAINT memory_evidence_conversation_non_empty
        CHECK (length(trim(conversation_id)) > 0),
    CONSTRAINT memory_evidence_turn_non_empty
        CHECK (length(trim(source_turn_id)) > 0),
    CONSTRAINT memory_evidence_expression_non_empty
        CHECK (length(trim(source_expression)) > 0),
    CONSTRAINT memory_evidence_revision_identity
        UNIQUE (evidence_id, revision_id, memory_id, owner_id)
);

CREATE INDEX memory_evidence_owner_revision_idx
    ON memory_evidence (owner_id, revision_id);
