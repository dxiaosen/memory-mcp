ALTER TABLE memory_evidence
    ADD COLUMN source_role TEXT
        CHECK (
            source_role IS NULL
            OR source_role IN ('user', 'assistant', 'tool')
        );

ALTER TABLE memory_evidence
    ADD COLUMN source_message_id TEXT
        CHECK (
            source_message_id IS NULL
            OR length(trim(source_message_id)) > 0
        );

ALTER TABLE memory_evidence
    ADD COLUMN source_tool_name TEXT
        CHECK (
            source_tool_name IS NULL
            OR length(trim(source_tool_name)) > 0
        );

ALTER TABLE memory_review_items
    ADD COLUMN source_role TEXT
        CHECK (
            source_role IS NULL
            OR source_role IN ('user', 'assistant', 'tool')
        );

ALTER TABLE memory_review_items
    ADD COLUMN source_message_id TEXT
        CHECK (
            source_message_id IS NULL
            OR length(trim(source_message_id)) > 0
        );

ALTER TABLE memory_review_items
    ADD COLUMN source_tool_name TEXT
        CHECK (
            source_tool_name IS NULL
            OR length(trim(source_tool_name)) > 0
        );
