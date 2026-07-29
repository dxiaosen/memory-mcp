"""Owner-scoped Memory Repository implemented with standard-library SQLite."""

import logging
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import datetime
from os import PathLike
from uuid import UUID

from agent_lab.memory.domain import (
    AssertionKind,
    Evidence,
    LifecycleStatus,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    PrincipalContext,
)
from agent_lab.memory.ports import ScenarioPolicy
from agent_lab.observability import log_event, stable_reference

ConnectionFactory = Callable[[], sqlite3.Connection]
DatabasePath = str | PathLike[str]
_LOGGER = logging.getLogger(__name__)


def connection_factory(database_path: DatabasePath) -> ConnectionFactory:
    """Create SQLite connections with the constraints required by Memory Core."""

    normalized_path = str(database_path)

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(normalized_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    return connect


class SQLiteMemoryRepository:
    """Use SQLite transactions and constraints as the persistence boundary."""

    def __init__(self, connect: ConnectionFactory) -> None:
        self._connect = connect

    def register_scenario(self, policy: ScenarioPolicy) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO memory_scenarios (scenario_id)
                VALUES (?)
                ON CONFLICT (scenario_id) DO NOTHING
                """,
                (policy.scenario_id,),
            )
            connection.executemany(
                """
                INSERT INTO memory_scenario_types (scenario_id, memory_type)
                VALUES (?, ?)
                ON CONFLICT (scenario_id, memory_type) DO NOTHING
                """,
                [
                    (policy.scenario_id, memory_type)
                    for memory_type in sorted(policy.memory_types)
                ],
            )
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
            removed_types = registered_types - policy.memory_types
            connection.executemany(
                """
                DELETE FROM memory_scenario_types
                WHERE scenario_id = ? AND memory_type = ?
                """,
                [
                    (policy.scenario_id, memory_type)
                    for memory_type in sorted(removed_types)
                ],
            )
        log_event(
            _LOGGER,
            logging.DEBUG,
            "memory.sqlite.scenario_registered",
            memory_type_count=len(policy.memory_types),
            scenario_id=policy.scenario_id,
        )

    def add(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        if record.item.owner_id != principal.owner_id:
            raise ValueError("record owner must match trusted principal")

        item = record.item
        revision = record.current_revision
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO memory_items (
                    memory_id, owner_id, scenario, subject, memory_type, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.memory_id),
                    item.owner_id,
                    item.scenario,
                    item.subject,
                    item.memory_type,
                    item.created_at.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_revisions (
                    revision_id, memory_id, owner_id, revision_number, content,
                    assertion_kind, lifecycle_status, business_progress,
                    save_rationale, observed_at, created_at, is_current,
                    primary_evidence_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(revision.revision_id),
                    str(revision.memory_id),
                    revision.owner_id,
                    revision.revision_number,
                    revision.content,
                    revision.assertion_kind.value,
                    revision.lifecycle_status.value,
                    revision.business_progress,
                    revision.save_rationale,
                    revision.observed_at.isoformat(),
                    revision.created_at.isoformat(),
                    int(revision.is_current),
                    str(record.evidence[0].evidence_id),
                ),
            )
            connection.executemany(
                """
                INSERT INTO memory_evidence (
                    evidence_id, memory_id, revision_id, owner_id,
                    conversation_id, source_turn_id, source_expression,
                    observed_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(source.evidence_id),
                        str(source.memory_id),
                        str(source.revision_id),
                        source.owner_id,
                        source.conversation_id,
                        source.source_turn_id,
                        source.source_expression,
                        source.observed_at.isoformat(),
                        source.created_at.isoformat(),
                    )
                    for source in record.evidence
                ],
            )
        log_event(
            _LOGGER,
            logging.DEBUG,
            "memory.sqlite.record_committed",
            evidence_count=len(record.evidence),
            memory_id=item.memory_id,
            owner_ref=stable_reference(principal.owner_id),
            revision_id=revision.revision_id,
        )

    def get(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"{_SELECT_CURRENT_RECORD} WHERE i.owner_id = ? AND i.memory_id = ?",
                (principal.owner_id, str(memory_id)),
            ).fetchone()
            if row is None:
                return None
            return self._to_record(connection, row, principal.owner_id)

    def list(
        self,
        principal: PrincipalContext,
        *,
        active_only: bool,
    ) -> Sequence[MemoryRecord]:
        conditions = ["i.owner_id = ?"]
        parameters: list[object] = [principal.owner_id]
        if active_only:
            conditions.append("r.lifecycle_status = 'active'")
        query = (
            f"{_SELECT_CURRENT_RECORD} WHERE {' AND '.join(conditions)} "
            "ORDER BY i.created_at, i.memory_id"
        )
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
            return tuple(
                self._to_record(connection, row, principal.owner_id) for row in rows
            )

    def _to_record(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        owner_id: str,
    ) -> MemoryRecord:
        item = MemoryItem(
            memory_id=UUID(row["memory_id"]),
            owner_id=row["owner_id"],
            scenario=row["scenario"],
            subject=row["subject"],
            memory_type=row["memory_type"],
            created_at=datetime.fromisoformat(row["item_created_at"]),
        )
        revision = MemoryRevision(
            revision_id=UUID(row["revision_id"]),
            memory_id=item.memory_id,
            owner_id=item.owner_id,
            revision_number=row["revision_number"],
            content=row["content"],
            assertion_kind=AssertionKind(row["assertion_kind"]),
            lifecycle_status=LifecycleStatus(row["lifecycle_status"]),
            business_progress=row["business_progress"],
            save_rationale=row["save_rationale"],
            observed_at=datetime.fromisoformat(row["revision_observed_at"]),
            created_at=datetime.fromisoformat(row["revision_created_at"]),
            is_current=bool(row["is_current"]),
        )
        evidence = tuple(
            Evidence(
                evidence_id=UUID(source["evidence_id"]),
                memory_id=UUID(source["memory_id"]),
                revision_id=UUID(source["revision_id"]),
                owner_id=source["owner_id"],
                conversation_id=source["conversation_id"],
                source_turn_id=source["source_turn_id"],
                source_expression=source["source_expression"],
                observed_at=datetime.fromisoformat(source["observed_at"]),
                created_at=datetime.fromisoformat(source["created_at"]),
            )
            for source in connection.execute(
                """
                SELECT evidence_id, memory_id, revision_id, owner_id,
                       conversation_id, source_turn_id, source_expression,
                       observed_at, created_at
                FROM memory_evidence
                WHERE owner_id = ? AND revision_id = ?
                ORDER BY created_at, evidence_id
                """,
                (owner_id, str(revision.revision_id)),
            ).fetchall()
        )
        return MemoryRecord(
            item=item,
            current_revision=revision,
            evidence=evidence,
        )


_SELECT_CURRENT_RECORD = """
SELECT i.memory_id, i.owner_id, i.scenario, i.subject, i.memory_type,
       i.created_at AS item_created_at,
       r.revision_id, r.revision_number, r.content, r.assertion_kind,
       r.lifecycle_status, r.business_progress, r.save_rationale,
       r.observed_at AS revision_observed_at,
       r.created_at AS revision_created_at, r.is_current
FROM memory_items AS i
JOIN memory_revisions AS r
  ON r.memory_id = i.memory_id
 AND r.owner_id = i.owner_id
 AND r.is_current = 1
"""
