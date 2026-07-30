"""Owner-scoped Memory Repository implemented with standard-library SQLite."""

import logging
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from os import PathLike
from uuid import UUID

from memory_mcp.core.domain import (
    AdmissionDecision,
    AssertionKind,
    Candidate,
    CandidateDurability,
    CaptureOutcome,
    CaptureResult,
    CaptureStatus,
    Evidence,
    ExpressionBasis,
    ExtractionMetadata,
    LifecycleStatus,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    MessageRole,
    PrincipalContext,
    ReviewItem,
    ReviewStatus,
)
from memory_mcp.core.ports import CaptureWrite, ScenarioPolicy
from memory_mcp.logging import log_event, stable_reference

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

        with closing(self._connect()) as connection, connection:
            self._insert_record(connection, record)
        item = record.item
        revision = record.current_revision
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

    def get_capture(
        self,
        principal: PrincipalContext,
        *,
        scenario: str,
        conversation_id: str,
        source_turn_id: str,
        policy_version: str,
        event_id: str | None = None,
    ) -> CaptureResult | None:
        if event_id is not None:
            where_clause = """
                owner_id = ?
                AND scenario = ?
                AND event_id = ?
                AND policy_version = ?
            """
            parameters: tuple[object, ...] = (
                principal.owner_id,
                scenario,
                event_id,
                policy_version,
            )
        else:
            where_clause = """
                owner_id = ?
                AND scenario = ?
                AND conversation_id = ?
                AND source_turn_id = ?
                AND policy_version = ?
            """
            parameters = (
                principal.owner_id,
                scenario,
                conversation_id,
                source_turn_id,
                policy_version,
            )
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""
                SELECT capture_id, owner_id, scenario, conversation_id,
                       source_turn_id, policy_version, prompt_version,
                       schema_version, model_id, status, failure_code,
                       created_at, completed_at, event_id,
                       contract_version, payload_fingerprint
                FROM memory_capture_runs
                WHERE {where_clause}
                """,
                parameters,
            ).fetchone()
            if row is None:
                return None
            return self._to_capture_result(connection, row)

    def commit_capture(
        self,
        principal: PrincipalContext,
        write: CaptureWrite,
    ) -> None:
        self._validate_capture_write(principal, write)
        result = write.result
        with closing(self._connect()) as connection, connection:
            if result.event_id is not None:
                where_clause = """
                    owner_id = ?
                    AND scenario = ?
                    AND event_id = ?
                    AND policy_version = ?
                """
                parameters: tuple[object, ...] = (
                    result.owner_id,
                    result.scenario,
                    result.event_id,
                    result.metadata.policy_version,
                )
            else:
                where_clause = """
                    owner_id = ?
                    AND scenario = ?
                    AND conversation_id = ?
                    AND source_turn_id = ?
                    AND policy_version = ?
                """
                parameters = (
                    result.owner_id,
                    result.scenario,
                    result.conversation_id,
                    result.source_turn_id,
                    result.metadata.policy_version,
                )
            existing = connection.execute(
                f"""
                SELECT capture_id, status
                FROM memory_capture_runs
                WHERE {where_clause}
                """,
                parameters,
            ).fetchone()
            if existing is None:
                self._insert_capture_run(connection, result)
            else:
                if existing["status"] != CaptureStatus.REPROCESS_REQUIRED.value:
                    raise ValueError("completed capture cannot be replaced")
                if existing["capture_id"] != str(result.capture_id):
                    raise ValueError("reprocessed capture must preserve capture_id")
                connection.execute(
                    """
                    DELETE FROM memory_capture_outcomes
                    WHERE capture_id = ? AND owner_id = ?
                    """,
                    (str(result.capture_id), result.owner_id),
                )
                connection.execute(
                    """
                    UPDATE memory_capture_runs
                    SET prompt_version = ?,
                        schema_version = ?,
                        model_id = ?,
                        status = ?,
                        failure_code = ?,
                        completed_at = ?
                    WHERE capture_id = ? AND owner_id = ?
                    """,
                    (
                        result.metadata.prompt_version,
                        result.metadata.schema_version,
                        result.metadata.model_id,
                        result.status.value,
                        result.failure_code,
                        result.completed_at.isoformat(),
                        str(result.capture_id),
                        result.owner_id,
                    ),
                )

            for record in write.memories:
                self._insert_record(connection, record)
            for review in write.reviews:
                self._insert_review(
                    connection,
                    result.capture_id,
                    review,
                )
            connection.executemany(
                """
                INSERT INTO memory_capture_outcomes (
                    capture_id, candidate_id, owner_id, decision,
                    reason_code, memory_id, review_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(result.capture_id),
                        str(outcome.candidate_id),
                        result.owner_id,
                        outcome.decision.value,
                        outcome.reason_code,
                        (
                            str(outcome.memory_id)
                            if outcome.memory_id is not None
                            else None
                        ),
                        (
                            str(outcome.review_id)
                            if outcome.review_id is not None
                            else None
                        ),
                    )
                    for outcome in result.outcomes
                ],
            )
        log_event(
            _LOGGER,
            logging.DEBUG,
            "memory.sqlite.capture_committed",
            capture_id=result.capture_id,
            outcome_count=len(result.outcomes),
            owner_ref=stable_reference(principal.owner_id),
            status=result.status.value,
        )

    def list_reviews(
        self,
        principal: PrincipalContext,
        *,
        status: ReviewStatus,
    ) -> Sequence[ReviewItem]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"{_SELECT_REVIEW} "
                "WHERE owner_id = ? AND status = ? "
                "ORDER BY created_at, review_id",
                (principal.owner_id, status.value),
            ).fetchall()
        return tuple(self._to_review(row) for row in rows)

    def get_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"{_SELECT_REVIEW} WHERE owner_id = ? AND review_id = ?",
                (principal.owner_id, str(review_id)),
            ).fetchone()
        if row is None:
            return None
        return self._to_review(row)

    def resolve_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
        *,
        status: ReviewStatus,
        decided_at: datetime,
        memory: MemoryRecord | None = None,
    ) -> ReviewItem | None:
        if status not in {ReviewStatus.CONFIRMED, ReviewStatus.REJECTED}:
            raise ValueError("review resolution must be confirmed or rejected")
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                f"{_SELECT_REVIEW} WHERE owner_id = ? AND review_id = ?",
                (principal.owner_id, str(review_id)),
            ).fetchone()
            if row is None:
                return None
            review = self._to_review(row)
            if review.status is status:
                return review
            if review.status is not ReviewStatus.PENDING:
                return None
            if status is ReviewStatus.CONFIRMED:
                if memory is None:
                    raise ValueError("confirmed review requires memory")
                self._validate_review_memory(review, memory)
                self._insert_record(connection, memory)
            elif memory is not None:
                raise ValueError("rejected review cannot create memory")

            cursor = connection.execute(
                """
                UPDATE memory_review_items
                SET status = ?, decided_at = ?, resolved_memory_id = ?
                WHERE owner_id = ?
                  AND review_id = ?
                  AND status = 'pending'
                """,
                (
                    status.value,
                    decided_at.isoformat(),
                    (
                        str(memory.item.memory_id)
                        if status is ReviewStatus.CONFIRMED and memory is not None
                        else None
                    ),
                    principal.owner_id,
                    str(review_id),
                ),
            )
            if cursor.rowcount != 1:
                raise sqlite3.IntegrityError("pending review changed during resolution")
        return replace(
            review,
            status=status,
            decided_at=decided_at,
            resolved_memory_id=(
                memory.item.memory_id
                if status is ReviewStatus.CONFIRMED and memory is not None
                else None
            ),
        )

    def _insert_capture_run(
        self,
        connection: sqlite3.Connection,
        result: CaptureResult,
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_capture_runs (
                capture_id, owner_id, scenario, conversation_id,
                source_turn_id, policy_version, prompt_version,
                schema_version, model_id, status, failure_code,
                created_at, completed_at, event_id, contract_version,
                payload_fingerprint
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(result.capture_id),
                result.owner_id,
                result.scenario,
                result.conversation_id,
                result.source_turn_id,
                result.metadata.policy_version,
                result.metadata.prompt_version,
                result.metadata.schema_version,
                result.metadata.model_id,
                result.status.value,
                result.failure_code,
                result.created_at.isoformat(),
                result.completed_at.isoformat(),
                result.event_id,
                result.contract_version,
                result.payload_fingerprint,
            ),
        )

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        record: MemoryRecord,
    ) -> None:
        item = record.item
        revision = record.current_revision
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
                primary_evidence_id, original_time_expression, normalized_time
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                revision.original_time_expression,
                (
                    revision.normalized_time.isoformat()
                    if revision.normalized_time is not None
                    else None
                ),
            ),
        )
        connection.executemany(
            """
            INSERT INTO memory_evidence (
                evidence_id, memory_id, revision_id, owner_id,
                conversation_id, source_turn_id, source_expression,
                observed_at, created_at, source_role, source_message_id,
                source_tool_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    (
                        source.source_role.value
                        if source.source_role is not None
                        else None
                    ),
                    source.source_message_id,
                    source.source_tool_name,
                )
                for source in record.evidence
            ],
        )

    def _insert_review(
        self,
        connection: sqlite3.Connection,
        capture_id: UUID,
        review: ReviewItem,
    ) -> None:
        candidate = review.candidate
        connection.execute(
            """
            INSERT INTO memory_review_items (
                review_id, candidate_id, capture_id, owner_id, scenario,
                subject, memory_type, content, assertion_kind,
                business_progress, conversation_id, source_turn_id,
                source_expression, save_rationale, confidence, durability,
                expression_basis, observed_at, candidate_created_at,
                original_time_expression, normalized_time, status,
                created_at, decided_at, source_role, source_message_id,
                source_tool_name
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                str(review.review_id),
                str(candidate.candidate_id),
                str(capture_id),
                candidate.owner_id,
                candidate.scenario,
                candidate.subject,
                candidate.memory_type,
                candidate.content,
                candidate.assertion_kind.value,
                candidate.business_progress,
                candidate.conversation_id,
                candidate.source_turn_id,
                candidate.source_expression,
                candidate.save_rationale,
                candidate.confidence,
                candidate.durability.value,
                candidate.expression_basis.value,
                candidate.observed_at.isoformat(),
                candidate.created_at.isoformat(),
                candidate.original_time_expression,
                (
                    candidate.normalized_time.isoformat()
                    if candidate.normalized_time is not None
                    else None
                ),
                review.status.value,
                review.created_at.isoformat(),
                (
                    review.decided_at.isoformat()
                    if review.decided_at is not None
                    else None
                ),
                (
                    candidate.source_role.value
                    if candidate.source_role is not None
                    else None
                ),
                candidate.source_message_id,
                candidate.source_tool_name,
            ),
        )

    def _to_capture_result(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> CaptureResult:
        outcomes = tuple(
            CaptureOutcome(
                candidate_id=UUID(outcome["candidate_id"]),
                decision=AdmissionDecision(outcome["decision"]),
                reason_code=outcome["reason_code"],
                memory_id=(
                    UUID(outcome["memory_id"])
                    if outcome["memory_id"] is not None
                    else None
                ),
                review_id=(
                    UUID(outcome["review_id"])
                    if outcome["review_id"] is not None
                    else None
                ),
            )
            for outcome in connection.execute(
                """
                SELECT candidate_id, decision, reason_code, memory_id, review_id
                FROM memory_capture_outcomes
                WHERE capture_id = ? AND owner_id = ?
                ORDER BY rowid
                """,
                (row["capture_id"], row["owner_id"]),
            ).fetchall()
        )
        return CaptureResult(
            capture_id=UUID(row["capture_id"]),
            owner_id=row["owner_id"],
            scenario=row["scenario"],
            conversation_id=row["conversation_id"],
            source_turn_id=row["source_turn_id"],
            metadata=ExtractionMetadata(
                model_id=row["model_id"],
                prompt_version=row["prompt_version"],
                schema_version=row["schema_version"],
                policy_version=row["policy_version"],
            ),
            status=CaptureStatus(row["status"]),
            outcomes=outcomes,
            failure_code=row["failure_code"],
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]),
            event_id=row["event_id"],
            contract_version=row["contract_version"],
            payload_fingerprint=row["payload_fingerprint"],
        )

    @staticmethod
    def _to_review(row: sqlite3.Row) -> ReviewItem:
        normalized_time = (
            datetime.fromisoformat(row["normalized_time"])
            if row["normalized_time"] is not None
            else None
        )
        candidate = Candidate(
            candidate_id=UUID(row["candidate_id"]),
            owner_id=row["owner_id"],
            scenario=row["scenario"],
            subject=row["subject"],
            memory_type=row["memory_type"],
            content=row["content"],
            assertion_kind=AssertionKind(row["assertion_kind"]),
            conversation_id=row["conversation_id"],
            source_turn_id=row["source_turn_id"],
            source_expression=row["source_expression"],
            save_rationale=row["save_rationale"],
            confidence=row["confidence"],
            durability=CandidateDurability(row["durability"]),
            expression_basis=ExpressionBasis(row["expression_basis"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            created_at=datetime.fromisoformat(row["candidate_created_at"]),
            business_progress=row["business_progress"],
            original_time_expression=row["original_time_expression"],
            normalized_time=normalized_time,
            source_role=(
                MessageRole(row["source_role"])
                if row["source_role"] is not None
                else None
            ),
            source_message_id=row["source_message_id"],
            source_tool_name=row["source_tool_name"],
        )
        return ReviewItem(
            review_id=UUID(row["review_id"]),
            candidate=candidate,
            status=ReviewStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=(
                datetime.fromisoformat(row["decided_at"])
                if row["decided_at"] is not None
                else None
            ),
            resolved_memory_id=(
                UUID(row["resolved_memory_id"])
                if row["resolved_memory_id"] is not None
                else None
            ),
        )

    @staticmethod
    def _validate_capture_write(
        principal: PrincipalContext,
        write: CaptureWrite,
    ) -> None:
        result = write.result
        if result.owner_id != principal.owner_id:
            raise ValueError("capture owner must match trusted principal")
        if result.status is not CaptureStatus.COMPLETED and (
            write.memories or write.reviews
        ):
            raise ValueError("failed capture cannot persist candidate content")
        memory_ids = {record.item.memory_id for record in write.memories}
        review_ids = {review.review_id for review in write.reviews}
        if len(memory_ids) != len(write.memories):
            raise ValueError("capture contains duplicate memory ids")
        if len(review_ids) != len(write.reviews):
            raise ValueError("capture contains duplicate review ids")
        for record in write.memories:
            if record.item.owner_id != principal.owner_id:
                raise ValueError("record owner must match trusted principal")
        for review in write.reviews:
            if review.owner_id != principal.owner_id:
                raise ValueError("review owner must match trusted principal")
            if review.status is not ReviewStatus.PENDING:
                raise ValueError("new review must be pending")
        for outcome in result.outcomes:
            if outcome.memory_id is not None and outcome.memory_id not in memory_ids:
                raise ValueError("capture outcome references unknown memory")
            if outcome.review_id is not None and outcome.review_id not in review_ids:
                raise ValueError("capture outcome references unknown review")

    @staticmethod
    def _validate_review_memory(
        review: ReviewItem,
        memory: MemoryRecord,
    ) -> None:
        candidate = review.candidate
        item = memory.item
        revision = memory.current_revision
        if (
            item.owner_id != candidate.owner_id
            or item.scenario != candidate.scenario
            or item.subject != candidate.subject
            or item.memory_type != candidate.memory_type
            or revision.content != candidate.content
            or revision.assertion_kind is not candidate.assertion_kind
            or revision.business_progress != candidate.business_progress
            or revision.save_rationale != candidate.save_rationale
            or revision.observed_at != candidate.observed_at
            or revision.original_time_expression != candidate.original_time_expression
            or revision.normalized_time != candidate.normalized_time
        ):
            raise ValueError("confirmed memory must match pending candidate")
        if len(memory.evidence) != 1:
            raise ValueError("confirmed memory requires one source evidence")
        source = memory.evidence[0]
        if (
            source.owner_id != candidate.owner_id
            or source.conversation_id != candidate.conversation_id
            or source.source_turn_id != candidate.source_turn_id
            or source.source_expression != candidate.source_expression
            or source.observed_at != candidate.observed_at
            or source.source_role is not candidate.source_role
            or source.source_message_id != candidate.source_message_id
            or source.source_tool_name != candidate.source_tool_name
        ):
            raise ValueError("confirmed memory source must match pending candidate")

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
            original_time_expression=row["original_time_expression"],
            normalized_time=(
                datetime.fromisoformat(row["normalized_time"])
                if row["normalized_time"] is not None
                else None
            ),
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
                source_role=(
                    MessageRole(source["source_role"])
                    if source["source_role"] is not None
                    else None
                ),
                source_message_id=source["source_message_id"],
                source_tool_name=source["source_tool_name"],
            )
            for source in connection.execute(
                """
                SELECT evidence_id, memory_id, revision_id, owner_id,
                       conversation_id, source_turn_id, source_expression,
                       observed_at, created_at, source_role,
                       source_message_id, source_tool_name
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
       r.created_at AS revision_created_at, r.is_current,
       r.original_time_expression, r.normalized_time
FROM memory_items AS i
JOIN memory_revisions AS r
  ON r.memory_id = i.memory_id
 AND r.owner_id = i.owner_id
 AND r.is_current = 1
"""

_SELECT_REVIEW = """
SELECT review_id, candidate_id, owner_id, scenario, subject, memory_type,
       content, assertion_kind, business_progress, conversation_id,
       source_turn_id, source_expression, save_rationale, confidence,
       durability, expression_basis, observed_at, candidate_created_at,
       original_time_expression, normalized_time, status, created_at,
       decided_at, resolved_memory_id, source_role, source_message_id,
       source_tool_name
FROM memory_review_items
"""
