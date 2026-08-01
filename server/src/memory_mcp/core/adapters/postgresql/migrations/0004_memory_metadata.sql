ALTER TABLE memory_revisions
    ADD COLUMN extraction_confidence DOUBLE PRECISION,
    ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'unverified',
    ADD COLUMN sensitivity_level TEXT NOT NULL DEFAULT 'confidential',
    ADD COLUMN valid_from TIMESTAMPTZ,
    ADD COLUMN valid_until TIMESTAMPTZ,
    ADD COLUMN last_verified_at TIMESTAMPTZ;

UPDATE memory_revisions
SET valid_from = observed_at
WHERE valid_from IS NULL;

ALTER TABLE memory_revisions
    ALTER COLUMN valid_from SET NOT NULL,
    ADD CONSTRAINT memory_revisions_extraction_confidence
        CHECK (
            extraction_confidence IS NULL
            OR extraction_confidence BETWEEN 0.0 AND 1.0
        ),
    ADD CONSTRAINT memory_revisions_verification_status
        CHECK (
            verification_status IN (
                'unverified',
                'user_asserted',
                'user_confirmed',
                'source_verified'
            )
        ),
    ADD CONSTRAINT memory_revisions_sensitivity_level
        CHECK (
            sensitivity_level IN (
                'public',
                'internal',
                'confidential',
                'restricted'
            )
        ),
    ADD CONSTRAINT memory_revisions_valid_window
        CHECK (valid_until IS NULL OR valid_until > valid_from);

ALTER TABLE memory_evidence
    ADD COLUMN source_type TEXT NOT NULL DEFAULT 'conversation',
    ADD COLUMN source_uri TEXT,
    ADD COLUMN source_title TEXT,
    ADD COLUMN source_publisher TEXT,
    ADD COLUMN published_at TIMESTAMPTZ,
    ADD COLUMN retrieved_at TIMESTAMPTZ,
    ADD COLUMN content_hash TEXT,
    ADD COLUMN citation_locator TEXT,
    ADD CONSTRAINT memory_evidence_source_type
        CHECK (source_type IN ('conversation', 'tool', 'document', 'web')),
    ADD CONSTRAINT memory_evidence_source_uri_non_empty
        CHECK (source_uri IS NULL OR length(btrim(source_uri)) > 0),
    ADD CONSTRAINT memory_evidence_source_title_non_empty
        CHECK (source_title IS NULL OR length(btrim(source_title)) > 0),
    ADD CONSTRAINT memory_evidence_source_publisher_non_empty
        CHECK (source_publisher IS NULL OR length(btrim(source_publisher)) > 0),
    ADD CONSTRAINT memory_evidence_content_hash_non_empty
        CHECK (content_hash IS NULL OR length(btrim(content_hash)) > 0),
    ADD CONSTRAINT memory_evidence_citation_locator_non_empty
        CHECK (citation_locator IS NULL OR length(btrim(citation_locator)) > 0);

ALTER TABLE memory_review_items
    ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'unverified',
    ADD COLUMN sensitivity_level TEXT NOT NULL DEFAULT 'confidential',
    ADD COLUMN valid_from TIMESTAMPTZ,
    ADD COLUMN valid_until TIMESTAMPTZ,
    ADD COLUMN last_verified_at TIMESTAMPTZ,
    ADD COLUMN source_type TEXT NOT NULL DEFAULT 'conversation',
    ADD COLUMN source_uri TEXT,
    ADD COLUMN source_title TEXT,
    ADD COLUMN source_publisher TEXT,
    ADD COLUMN published_at TIMESTAMPTZ,
    ADD COLUMN retrieved_at TIMESTAMPTZ,
    ADD COLUMN content_hash TEXT,
    ADD COLUMN citation_locator TEXT;

UPDATE memory_review_items
SET valid_from = observed_at
WHERE valid_from IS NULL;

ALTER TABLE memory_review_items
    ALTER COLUMN valid_from SET NOT NULL,
    ADD CONSTRAINT memory_review_items_verification_status
        CHECK (
            verification_status IN (
                'unverified',
                'user_asserted',
                'user_confirmed',
                'source_verified'
            )
        ),
    ADD CONSTRAINT memory_review_items_sensitivity_level
        CHECK (
            sensitivity_level IN (
                'public',
                'internal',
                'confidential',
                'restricted'
            )
        ),
    ADD CONSTRAINT memory_review_items_valid_window
        CHECK (valid_until IS NULL OR valid_until > valid_from),
    ADD CONSTRAINT memory_review_items_source_type
        CHECK (source_type IN ('conversation', 'tool', 'document', 'web')),
    ADD CONSTRAINT memory_review_items_source_uri_non_empty
        CHECK (source_uri IS NULL OR length(btrim(source_uri)) > 0),
    ADD CONSTRAINT memory_review_items_source_title_non_empty
        CHECK (source_title IS NULL OR length(btrim(source_title)) > 0),
    ADD CONSTRAINT memory_review_items_source_publisher_non_empty
        CHECK (source_publisher IS NULL OR length(btrim(source_publisher)) > 0),
    ADD CONSTRAINT memory_review_items_content_hash_non_empty
        CHECK (content_hash IS NULL OR length(btrim(content_hash)) > 0),
    ADD CONSTRAINT memory_review_items_citation_locator_non_empty
        CHECK (citation_locator IS NULL OR length(btrim(citation_locator)) > 0);

CREATE INDEX memory_revisions_owner_effective_idx
    ON memory_revisions (owner_id, lifecycle_status, valid_from, valid_until)
    WHERE is_current;
