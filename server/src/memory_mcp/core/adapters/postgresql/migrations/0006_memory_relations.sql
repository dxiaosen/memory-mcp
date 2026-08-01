ALTER TABLE memory_items
    ADD CONSTRAINT memory_items_owner_profile_identity
    UNIQUE (memory_id, owner_id, profile_id);

CREATE TABLE memory_profile_relations (
    profile_id TEXT NOT NULL
        REFERENCES memory_profiles (profile_id) ON DELETE RESTRICT,
    relation_type TEXT NOT NULL,
    PRIMARY KEY (profile_id, relation_type),
    CONSTRAINT memory_profile_relations_type_non_empty
        CHECK (length(btrim(relation_type)) > 0)
);

CREATE TABLE memory_relations (
    relation_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    source_memory_id UUID NOT NULL,
    target_memory_id UUID NOT NULL,
    relation_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT memory_relations_registered_type
        FOREIGN KEY (profile_id, relation_type)
        REFERENCES memory_profile_relations (profile_id, relation_type)
        ON DELETE RESTRICT,
    CONSTRAINT memory_relations_owned_source
        FOREIGN KEY (source_memory_id, owner_id, profile_id)
        REFERENCES memory_items (memory_id, owner_id, profile_id)
        ON DELETE RESTRICT,
    CONSTRAINT memory_relations_owned_target
        FOREIGN KEY (target_memory_id, owner_id, profile_id)
        REFERENCES memory_items (memory_id, owner_id, profile_id)
        ON DELETE RESTRICT,
    CONSTRAINT memory_relations_owner_non_empty
        CHECK (length(btrim(owner_id)) > 0),
    CONSTRAINT memory_relations_type_non_empty
        CHECK (length(btrim(relation_type)) > 0),
    CONSTRAINT memory_relations_not_self
        CHECK (source_memory_id <> target_memory_id),
    CONSTRAINT memory_relations_status
        CHECK (status IN ('active', 'revoked')),
    CONSTRAINT memory_relations_revocation_state
        CHECK (
            (status = 'active' AND revoked_at IS NULL)
            OR
            (
                status = 'revoked'
                AND revoked_at IS NOT NULL
                AND revoked_at >= created_at
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
