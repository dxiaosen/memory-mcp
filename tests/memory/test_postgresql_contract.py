from agent_lab.memory.adapters.postgresql import PostgreSQLMemoryRepository
from agent_lab.memory.adapters.postgresql.runtime import load_migrations


def test_postgresql_migration_preserves_authoritative_invariants() -> None:
    migrations = load_migrations()

    assert [migration.version for migration in migrations] == ["0001_memory_core.sql"]
    assert all(len(migration.checksum) == 64 for migration in migrations)

    sql = migrations[0].sql
    for required_fragment in (
        "memory_items",
        "memory_capture_runs_event_unique",
        "memory_revisions_one_current_idx",
        "DEFERRABLE INITIALLY DEFERRED",
        "TIMESTAMPTZ",
        "UUID",
        "owner_id",
    ):
        assert required_fragment in sql


def test_postgresql_repository_exposes_the_memory_repository_contract() -> None:
    required_methods = {
        "register_scenario",
        "add",
        "get",
        "list",
        "get_capture",
        "commit_capture",
        "list_reviews",
        "get_review",
        "resolve_review",
    }

    assert required_methods.issubset(dir(PostgreSQLMemoryRepository))
