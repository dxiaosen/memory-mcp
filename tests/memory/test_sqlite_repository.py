from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from agent_lab.memory import LifecycleStatus, MemoryNotFoundError, PrincipalContext
from agent_lab.memory.adapters.sqlite import (
    SQLiteMemoryRepository,
    connection_factory,
)
from agent_lab.memory.adapters.sqlite.runtime import (
    apply_migrations,
    check_health,
)
from agent_lab.memory.composition import create_memory_service
from memory.fakes import TestScenarioPolicy, project_preference_command


@pytest.fixture
def database_path() -> Iterator[Path]:
    test_directory = Path(".agent-lab/test-memory")
    test_directory.mkdir(parents=True, exist_ok=True)
    path = test_directory / f"{uuid4().hex}.db"
    try:
        assert apply_migrations(path) == (
            "0001_memory_core.sql",
            "0002_memory_capture.sql",
            "0003_mcp_events.sql",
            "0004_message_provenance.sql",
        )
        assert apply_migrations(path) == ()
        check_health(path)
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_sqlite_repository_persists_and_isolates_memory(
    database_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(connection_factory(database_path))
    service = create_memory_service(repository, [TestScenarioPolicy()])
    analyst_a = PrincipalContext("analyst-a")
    analyst_b = PrincipalContext("analyst-b")

    created = service.create_memory(analyst_a, project_preference_command())

    assert service.get_memory(analyst_a, created.item.memory_id) == created
    assert service.list_memories(analyst_a) == (created,)
    assert service.list_memories(analyst_b) == ()
    with pytest.raises(MemoryNotFoundError, match="unavailable"):
        service.get_memory(analyst_b, created.item.memory_id)

    reopened = SQLiteMemoryRepository(connection_factory(database_path))
    assert reopened.get(analyst_a, created.item.memory_id) == created


def test_default_list_hides_inactive_memory(database_path: Path) -> None:
    repository = SQLiteMemoryRepository(connection_factory(database_path))
    service = create_memory_service(repository, [TestScenarioPolicy()])
    principal = PrincipalContext("analyst-a")

    active = service.create_memory(principal, project_preference_command())
    revoked = service.create_memory(
        principal,
        project_preference_command(lifecycle_status=LifecycleStatus.REVOKED),
    )

    assert service.list_memories(principal) == (active,)
    assert set(service.list_memories(principal, include_inactive=True)) == {
        active,
        revoked,
    }


def test_register_scenario_synchronizes_removed_memory_types(
    database_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(connection_factory(database_path))
    policy = TestScenarioPolicy()
    repository.register_scenario(policy)

    repository.register_scenario(
        replace(policy, memory_types=frozenset({"preference"}))
    )

    with closing(connection_factory(database_path)()) as connection:
        registered_types = {
            row[0]
            for row in connection.execute(
                """
                SELECT memory_type
                FROM memory_scenario_types
                WHERE scenario_id = ?
                """,
                (policy.scenario_id,),
            ).fetchall()
        }
    assert registered_types == {"preference"}


def test_database_rejects_unregistered_scenario_type(database_path: Path) -> None:
    now = datetime.now(UTC).isoformat()
    with closing(connection_factory(database_path)()) as connection:
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(
                """
                INSERT INTO memory_items (
                    memory_id, owner_id, scenario, subject, memory_type, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    "analyst-a",
                    "unregistered",
                    "weekly-report",
                    "preference",
                    now,
                ),
            )


def test_database_rejects_invalid_revision_status(database_path: Path) -> None:
    memory_id = _insert_registered_item(database_path)
    now = datetime.now(UTC).isoformat()
    with closing(connection_factory(database_path)()) as connection:
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(
                """
                INSERT INTO memory_revisions (
                    revision_id, memory_id, owner_id, revision_number, content,
                    assertion_kind, lifecycle_status, save_rationale,
                    observed_at, created_at, is_current, primary_evidence_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    memory_id,
                    "analyst-a",
                    1,
                    "content",
                    "user_view",
                    "unknown",
                    "rationale",
                    now,
                    now,
                    0,
                    str(uuid4()),
                ),
            )


def test_database_requires_primary_evidence(database_path: Path) -> None:
    memory_id = _insert_registered_item(database_path)
    now = datetime.now(UTC).isoformat()
    with closing(connection_factory(database_path)()) as connection:
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(
                """
                INSERT INTO memory_revisions (
                    revision_id, memory_id, owner_id, revision_number, content,
                    assertion_kind, lifecycle_status, save_rationale,
                    observed_at, created_at, is_current, primary_evidence_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    memory_id,
                    "analyst-a",
                    1,
                    "content",
                    "user_view",
                    "active",
                    "rationale",
                    now,
                    now,
                    1,
                    str(uuid4()),
                ),
            )


def test_database_allows_only_one_current_revision(database_path: Path) -> None:
    repository = SQLiteMemoryRepository(connection_factory(database_path))
    service = create_memory_service(repository, [TestScenarioPolicy()])
    created = service.create_memory(
        PrincipalContext("analyst-a"),
        project_preference_command(),
    )
    now = datetime.now(UTC).isoformat()

    with closing(connection_factory(database_path)()) as connection:
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(
                """
                INSERT INTO memory_revisions (
                    revision_id, memory_id, owner_id, revision_number, content,
                    assertion_kind, lifecycle_status, save_rationale,
                    observed_at, created_at, is_current, primary_evidence_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(created.item.memory_id),
                    "analyst-a",
                    2,
                    "replacement",
                    "user_view",
                    "active",
                    "rationale",
                    now,
                    now,
                    1,
                    str(uuid4()),
                ),
            )


def test_database_rejects_cross_owner_evidence(database_path: Path) -> None:
    repository = SQLiteMemoryRepository(connection_factory(database_path))
    service = create_memory_service(repository, [TestScenarioPolicy()])
    created = service.create_memory(
        PrincipalContext("analyst-a"),
        project_preference_command(),
    )
    now = datetime.now(UTC).isoformat()

    with closing(connection_factory(database_path)()) as connection:
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(
                """
                INSERT INTO memory_evidence (
                    evidence_id, memory_id, revision_id, owner_id,
                    conversation_id, source_turn_id, source_expression,
                    observed_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(created.item.memory_id),
                    str(created.current_revision.revision_id),
                    "analyst-b",
                    "session-b",
                    "turn-b",
                    "cross-owner source",
                    now,
                    now,
                ),
            )


def test_health_check_rejects_missing_database() -> None:
    missing_path = Path(".agent-lab/test-memory") / f"missing-{uuid4().hex}.db"
    with pytest.raises(RuntimeError, match="does not exist"):
        check_health(missing_path)


def _insert_registered_item(database_path: Path) -> str:
    memory_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    with closing(connection_factory(database_path)()) as connection, connection:
        connection.execute(
            "INSERT OR IGNORE INTO memory_scenarios (scenario_id) VALUES (?)",
            ("project-work",),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO memory_scenario_types (
                scenario_id, memory_type
            )
            VALUES (?, ?)
            """,
            ("project-work", "preference"),
        )
        connection.execute(
            """
            INSERT INTO memory_items (
                memory_id, owner_id, scenario, subject, memory_type, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                "analyst-a",
                "project-work",
                "weekly-report",
                "preference",
                now,
            ),
        )
    return memory_id
