-- 将“运行场景”命名升级为“记忆配置”，保留现有数据和历史 migration checksum。
ALTER TABLE memory_scenarios RENAME TO memory_profiles;
ALTER TABLE memory_profiles RENAME COLUMN scenario_id TO profile_id;
ALTER TABLE memory_profiles
    RENAME CONSTRAINT memory_scenarios_pkey TO memory_profiles_pkey;
ALTER TABLE memory_profiles
    RENAME CONSTRAINT memory_scenarios_non_empty TO memory_profiles_non_empty;

ALTER TABLE memory_scenario_types RENAME TO memory_profile_types;
ALTER TABLE memory_profile_types RENAME COLUMN scenario_id TO profile_id;
ALTER TABLE memory_profile_types
    RENAME CONSTRAINT memory_scenario_types_pkey TO memory_profile_types_pkey;
ALTER TABLE memory_profile_types
    RENAME CONSTRAINT memory_scenario_types_scenario_id_fkey
    TO memory_profile_types_profile_id_fkey;
ALTER TABLE memory_profile_types
    RENAME CONSTRAINT memory_scenario_types_non_empty
    TO memory_profile_types_non_empty;

ALTER TABLE memory_items RENAME COLUMN scenario TO profile_id;

ALTER TABLE memory_capture_runs RENAME COLUMN scenario TO profile_id;
ALTER TABLE memory_capture_runs RENAME COLUMN policy_version TO profile_version;
ALTER TABLE memory_capture_runs
    RENAME CONSTRAINT memory_capture_runs_scenario_fkey
    TO memory_capture_runs_profile_id_fkey;
ALTER TABLE memory_capture_runs
    RENAME CONSTRAINT memory_capture_runs_policy_non_empty
    TO memory_capture_runs_profile_version_non_empty;

ALTER TABLE memory_review_items RENAME COLUMN scenario TO profile_id;

ALTER INDEX memory_items_owner_scenario_type_idx
    RENAME TO memory_items_owner_profile_type_idx;
