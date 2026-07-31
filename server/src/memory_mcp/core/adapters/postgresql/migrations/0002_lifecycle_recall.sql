CREATE INDEX memory_items_owner_scenario_type_idx
    ON memory_items (owner_id, scenario, memory_type, created_at);

CREATE INDEX memory_revisions_current_active_idx
    ON memory_revisions (owner_id, memory_id, revision_number)
    WHERE is_current AND lifecycle_status = 'active';
