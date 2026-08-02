"""使用 PostgreSQL 实现的 owner-scoped Memory Repository。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from math import ceil
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from memory_mcp.core.adapters.postgresql.maintenance import run_maintenance
from memory_mcp.core.adapters.postgresql.mapping import (
    as_uuid,
    load_evidence,
    to_capture_result,
    to_record,
    to_review,
    to_revision,
)
from memory_mcp.core.adapters.postgresql.recall import (
    find_recall_candidates as query_recall_candidates,
)
from memory_mcp.core.adapters.postgresql.schema import validate_schema
from memory_mcp.core.adapters.postgresql.validation import (
    validate_capture_write,
    validate_review_memory,
)
from memory_mcp.core.domain import (
    CaptureResult,
    CaptureStatus,
    Evidence,
    ExpressionBasis,
    MaintenanceResult,
    MemoryHistoryEntry,
    MemoryRecord,
    MemoryRelation,
    MemoryRelationSummary,
    MemoryRevision,
    PrincipalContext,
    RelationDirection,
    RelationOrigin,
    RelationProvenance,
    RelationScope,
    RelationStatus,
    ReviewItem,
    ReviewStatus,
    normalize_memory_text,
)
from memory_mcp.core.ports import (
    CaptureWrite,
    DuplicateEvidenceWrite,
    MemoryProfile,
    RecallCandidateSet,
    ReplacementWrite,
)
from memory_mcp.logging import log_event, stable_reference

PostgreSQLPool = ConnectionPool[Mapping[str, Any]]
_LOGGER = logging.getLogger(__name__)


def create_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 5,
    timeout: float = 10.0,
) -> PostgreSQLPool:
    """为 MCP 服务打开有界同步连接池。"""

    pool: PostgreSQLPool = ConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        kwargs={
            "connect_timeout": max(1, ceil(timeout)),
            "row_factory": dict_row,
        },
        name="memory-mcp",
        open=False,
    )
    pool.open(wait=True, timeout=timeout)
    return pool


class PostgreSQLMemoryRepository:
    """以 PostgreSQL 事务和约束作为持久化边界。"""

    def __init__(self, pool: PostgreSQLPool) -> None:
        self._pool = pool
        self._profiles: dict[str, MemoryProfile] = {}

    def close(self) -> None:
        """关闭连接池中的全部连接。"""

        self._pool.close()

    def check_health(self) -> None:
        """验证连接池能够访问已迁移 schema。"""

        with self._pool.connection() as connection:
            validate_schema(connection)

    def register_profile(self, profile: MemoryProfile) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO memory_profiles (profile_id)
                VALUES (%s)
                ON CONFLICT (profile_id) DO NOTHING
                """,
                (profile.profile_id,),
            )
            _executemany(
                connection,
                """
                INSERT INTO memory_profile_types (profile_id, memory_type)
                VALUES (%s, %s)
                ON CONFLICT (profile_id, memory_type) DO NOTHING
                """,
                [
                    (profile.profile_id, memory_type)
                    for memory_type in sorted(profile.memory_types)
                ],
            )
            _executemany(
                connection,
                """
                INSERT INTO memory_profile_relations (profile_id, relation_type)
                VALUES (%s, %s)
                ON CONFLICT (profile_id, relation_type) DO NOTHING
                """,
                [
                    (profile.profile_id, relation_type)
                    for relation_type in sorted(profile.relation_policies)
                ],
            )
            registered_types = {
                row["memory_type"]
                for row in connection.execute(
                    """
                    SELECT memory_type
                    FROM memory_profile_types
                    WHERE profile_id = %s
                    """,
                    (profile.profile_id,),
                ).fetchall()
            }
            removed_types = registered_types - profile.memory_types
            _executemany(
                connection,
                """
                DELETE FROM memory_profile_types
                WHERE profile_id = %s AND memory_type = %s
                """,
                [
                    (profile.profile_id, memory_type)
                    for memory_type in sorted(removed_types)
                ],
            )
        self._profiles[profile.profile_id] = profile
        log_event(
            _LOGGER,
            logging.DEBUG,
            "memory.postgresql.profile_registered",
            memory_type_count=len(profile.memory_types),
            profile_id=profile.profile_id,
        )

    def add(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        if record.item.owner_id != principal.owner_id:
            raise ValueError("record owner must match trusted principal")
        with self._pool.connection() as connection:
            self._insert_record(connection, record)
        log_event(
            _LOGGER,
            logging.DEBUG,
            "memory.postgresql.record_committed",
            evidence_count=len(record.evidence),
            memory_id=record.item.memory_id,
            owner_ref=stable_reference(principal.owner_id),
            revision_id=record.current_revision.revision_id,
        )

    def get(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                f"{_SELECT_CURRENT_RECORD} WHERE i.owner_id = %s AND i.memory_id = %s",
                (principal.owner_id, memory_id),
            ).fetchone()
            if row is None:
                return None
            return to_record(connection, row, principal.owner_id)

    def list(
        self,
        principal: PrincipalContext,
        *,
        active_only: bool,
        effective_at: datetime | None = None,
    ) -> Sequence[MemoryRecord]:
        conditions = ["i.owner_id = %s"]
        parameters: list[object] = [principal.owner_id]
        if active_only:
            resolved_time = effective_at or datetime.now(UTC)
            conditions.extend(
                (
                    "r.lifecycle_status = 'active'",
                    "r.valid_from <= %s",
                    "(r.valid_until IS NULL OR r.valid_until > %s)",
                )
            )
            parameters.extend((resolved_time, resolved_time))
        query = (
            f"{_SELECT_CURRENT_RECORD} WHERE {' AND '.join(conditions)} "
            "ORDER BY i.created_at, i.memory_id"
        )
        with self._pool.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
            return tuple(to_record(connection, row, principal.owner_id) for row in rows)

    def find_current(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        subject: str | None = None,
        memory_type: str | None = None,
        effective_at: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[MemoryRecord]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        conditions = [
            "i.owner_id = %s",
            "i.profile_id = %s",
            "r.lifecycle_status = 'active'",
            "r.valid_from <= %s",
            "(r.valid_until IS NULL OR r.valid_until > %s)",
        ]
        resolved_time = effective_at or datetime.now(UTC)
        parameters: list[object] = [
            principal.owner_id,
            profile_id,
            resolved_time,
            resolved_time,
        ]
        if memory_type is not None:
            conditions.append("i.memory_type = %s")
            parameters.append(memory_type)
        if subject is not None:
            conditions.append(
                "lower(regexp_replace(btrim(i.subject), '\\s+', ' ', 'g')) = "
                "lower(regexp_replace(btrim(%s), '\\s+', ' ', 'g'))"
            )
            parameters.append(subject)
        query = (
            f"{_SELECT_CURRENT_RECORD} WHERE {' AND '.join(conditions)} "
            "ORDER BY r.observed_at DESC, i.memory_id DESC"
        )
        if limit is not None:
            query += " LIMIT %s"
            parameters.append(limit)
        subject_key = normalize_memory_text(subject) if subject is not None else None
        with self._pool.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
            records = tuple(
                to_record(connection, row, principal.owner_id) for row in rows
            )
        if subject_key is None:
            return records
        return tuple(
            record
            for record in records
            if normalize_memory_text(record.item.subject) == subject_key
        )

    def find_recall_candidates(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        search_text: str,
        subject: str | None,
        effective_at: datetime,
        limit: int,
    ) -> RecallCandidateSet:
        """在 owner/Profile 边界内用 trigram 优先、近期记录补齐候选。"""

        with self._pool.connection() as connection:
            return query_recall_candidates(
                connection,
                principal,
                profile_id=profile_id,
                search_text=search_text,
                subject=subject,
                effective_at=effective_at,
                limit=limit,
            )

    def maintain(
        self,
        *,
        effective_at: datetime,
        review_cutoff: datetime,
        limit: int,
    ) -> MaintenanceResult:
        """原子物化一批过期 revision/review 及其关系终态。"""

        with self._pool.connection() as connection:
            return run_maintenance(
                connection,
                effective_at=effective_at,
                review_cutoff=review_cutoff,
                limit=limit,
            )

    def revoke(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                f"{_SELECT_CURRENT_RECORD} "
                "WHERE i.owner_id = %s AND i.memory_id = %s FOR UPDATE",
                (principal.owner_id, memory_id),
            ).fetchone()
            if row is None:
                return None
            if row["lifecycle_status"] == "revoked":
                return to_record(connection, row, principal.owner_id)
            if row["lifecycle_status"] != "active":
                return None
            connection.execute(
                """
                UPDATE memory_revisions
                SET lifecycle_status = 'revoked'
                WHERE owner_id = %s
                  AND memory_id = %s
                  AND revision_id = %s
                  AND is_current
                  AND lifecycle_status = 'active'
                """,
                (principal.owner_id, memory_id, row["revision_id"]),
            )
            row["lifecycle_status"] = "revoked"
            return to_record(connection, row, principal.owner_id)

    def link_relation(
        self,
        principal: PrincipalContext,
        relation: MemoryRelation,
        *,
        effective_at: datetime,
    ) -> MemoryRelation:
        if relation.owner_id != principal.owner_id:
            raise ValueError("relation owner must match trusted principal")
        if relation.status is not RelationStatus.ACTIVE:
            raise ValueError("new relation must be active")
        if relation.origin is not RelationOrigin.MANUAL:
            raise ValueError("explicit relation write must be manual")
        with self._pool.connection() as connection:
            committed = _insert_relation(
                connection,
                self._profiles,
                principal,
                relation,
                effective_at=effective_at,
            )
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.postgresql.relation_linked",
            relation_id=committed.relation_id,
            relation_origin=committed.origin.value,
            relation_scope=committed.scope.value,
            relation_type=committed.relation_type,
            source_memory_id=committed.source_memory_id,
            target_memory_id=committed.target_memory_id,
        )
        return committed

    def revoke_relation(
        self,
        principal: PrincipalContext,
        relation_id: UUID,
        *,
        revoked_at: datetime,
    ) -> MemoryRelation | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                f"{_SELECT_RELATION} "
                "WHERE owner_id = %s AND relation_id = %s FOR UPDATE",
                (principal.owner_id, relation_id),
            ).fetchone()
            if row is None:
                return None
            existing = _load_relation(row)
            if existing.status is RelationStatus.REVOKED:
                return existing
            cursor = connection.execute(
                """
                UPDATE memory_relations
                SET status = 'revoked', revoked_at = %s
                WHERE owner_id = %s AND relation_id = %s
                """,
                (revoked_at, principal.owner_id, relation_id),
            )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                f"{_SELECT_RELATION} WHERE owner_id = %s AND relation_id = %s",
                (principal.owner_id, relation_id),
            ).fetchone()
            return _load_relation(updated)

    def list_relations(
        self,
        principal: PrincipalContext,
        *,
        memory_ids: Sequence[UUID],
        active_only: bool,
        effective_at: datetime | None = None,
    ) -> Sequence[MemoryRelationSummary]:
        requested = frozenset(memory_ids)
        if not requested:
            return ()
        conditions = [
            "rel.owner_id = %s",
            "(rel.source_memory_id = ANY(%s) OR rel.target_memory_id = ANY(%s))",
        ]
        parameters: list[object] = [
            principal.owner_id,
            list(requested),
            list(requested),
        ]
        if active_only:
            resolved = effective_at or datetime.now(UTC)
            conditions.extend(
                (
                    "rel.status = 'active'",
                    "sr.lifecycle_status = 'active'",
                    "tr.lifecycle_status = 'active'",
                    "sr.valid_from <= %s",
                    "(sr.valid_until IS NULL OR sr.valid_until > %s)",
                    "tr.valid_from <= %s",
                    "(tr.valid_until IS NULL OR tr.valid_until > %s)",
                )
            )
            parameters.extend((resolved, resolved, resolved, resolved))
        query = f"""
            SELECT rel.relation_id, rel.owner_id, rel.profile_id,
                   rel.source_memory_id, rel.target_memory_id,
                   rel.relation_type, rel.origin, rel.scope,
                   rel.source_revision_id, rel.target_revision_id,
                   rel.capture_id, rel.conversation_id, rel.source_turn_id,
                   rel.source_expression, rel.confidence, rel.expression_basis,
                   rel.model_id, rel.prompt_version, rel.schema_version,
                   rel.status, rel.created_at, rel.revoked_at,
                   rel.stale_at, rel.stale_reason,
                   source.subject AS source_subject,
                   source.memory_type AS source_memory_type,
                   target.subject AS target_subject,
                   target.memory_type AS target_memory_type
            FROM memory_relations AS rel
            JOIN memory_items AS source
              ON source.memory_id = rel.source_memory_id
             AND source.owner_id = rel.owner_id
             AND source.profile_id = rel.profile_id
            JOIN memory_items AS target
              ON target.memory_id = rel.target_memory_id
             AND target.owner_id = rel.owner_id
             AND target.profile_id = rel.profile_id
            JOIN memory_revisions AS sr
              ON sr.memory_id = source.memory_id
             AND sr.owner_id = source.owner_id
             AND sr.is_current
            JOIN memory_revisions AS tr
              ON tr.memory_id = target.memory_id
             AND tr.owner_id = target.owner_id
             AND tr.is_current
            WHERE {" AND ".join(conditions)}
            ORDER BY rel.created_at, rel.relation_id
        """
        with self._pool.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        summaries: list[MemoryRelationSummary] = []
        for row in rows:
            relation = _load_relation(row)
            if relation.source_memory_id in requested:
                summaries.append(
                    MemoryRelationSummary(
                        relation=relation,
                        direction=RelationDirection.OUTGOING,
                        related_memory_id=relation.target_memory_id,
                        related_subject=row["target_subject"],
                        related_memory_type=row["target_memory_type"],
                    )
                )
            if relation.target_memory_id in requested:
                summaries.append(
                    MemoryRelationSummary(
                        relation=relation,
                        direction=RelationDirection.INCOMING,
                        related_memory_id=relation.source_memory_id,
                        related_subject=row["source_subject"],
                        related_memory_type=row["source_memory_type"],
                    )
                )
        return tuple(summaries)

    def get_history(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> Sequence[MemoryHistoryEntry]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT r.revision_id, r.memory_id, r.owner_id,
                       r.revision_number, r.content, r.assertion_kind,
                       r.lifecycle_status, r.business_progress,
                       r.save_rationale, r.observed_at, r.created_at,
                       r.is_current, r.original_time_expression,
                       r.normalized_time, r.extraction_confidence,
                       r.verification_status, r.sensitivity_level,
                       r.valid_from, r.valid_until, r.last_verified_at
                FROM memory_items AS i
                JOIN memory_revisions AS r
                  ON r.memory_id = i.memory_id
                 AND r.owner_id = i.owner_id
                WHERE i.owner_id = %s AND i.memory_id = %s
                ORDER BY r.revision_number DESC
                """,
                (principal.owner_id, memory_id),
            ).fetchall()
            return tuple(
                MemoryHistoryEntry(
                    revision=to_revision(row),
                    evidence=load_evidence(
                        connection,
                        principal.owner_id,
                        as_uuid(row["revision_id"]),
                    ),
                )
                for row in rows
            )

    def get_capture(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        conversation_id: str,
        source_turn_id: str,
        event_id: str | None = None,
    ) -> CaptureResult | None:
        if event_id is not None:
            where_clause = """
                owner_id = %s
                AND event_id = %s
            """
            parameters: tuple[object, ...] = (
                principal.owner_id,
                event_id,
            )
        else:
            where_clause = """
                owner_id = %s
                AND profile_id = %s
                AND conversation_id = %s
                AND source_turn_id = %s
            """
            parameters = (
                principal.owner_id,
                profile_id,
                conversation_id,
                source_turn_id,
            )
        with self._pool.connection() as connection:
            row = connection.execute(
                f"""
                SELECT capture_id, owner_id, profile_id, conversation_id,
                       source_turn_id, profile_version, profile_fingerprint,
                       prompt_version, schema_version, model_id, status, failure_code,
                       created_at, completed_at, event_id,
                       contract_version, payload_fingerprint
                FROM memory_capture_runs
                WHERE {where_clause}
                """,
                parameters,
            ).fetchone()
            if row is None:
                return None
            return to_capture_result(connection, row)

    def commit_capture(
        self,
        principal: PrincipalContext,
        write: CaptureWrite,
    ) -> None:
        validate_capture_write(principal, write)
        result = write.result
        stale_relation_count = 0
        with self._pool.connection() as connection:
            if result.event_id is not None:
                idempotency_key = f"{result.owner_id}\x1fevent\x1f{result.event_id}"
            else:
                idempotency_key = (
                    f"{result.owner_id}\x1flegacy\x1f{result.profile_id}\x1f"
                    f"{result.conversation_id}\x1f{result.source_turn_id}"
                )
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (idempotency_key,),
            )
            if result.event_id is not None:
                where_clause = """
                    owner_id = %s
                    AND event_id = %s
                """
                parameters: tuple[object, ...] = (
                    result.owner_id,
                    result.event_id,
                )
            else:
                where_clause = """
                    owner_id = %s
                    AND profile_id = %s
                    AND conversation_id = %s
                    AND source_turn_id = %s
                """
                parameters = (
                    result.owner_id,
                    result.profile_id,
                    result.conversation_id,
                    result.source_turn_id,
                )
            existing = connection.execute(
                f"""
                SELECT capture_id, status
                FROM memory_capture_runs
                WHERE {where_clause}
                FOR UPDATE
                """,
                parameters,
            ).fetchone()
            if existing is None:
                self._insert_capture_run(connection, result)
            else:
                if existing["status"] != CaptureStatus.REPROCESS_REQUIRED.value:
                    raise ValueError("completed capture cannot be replaced")
                if as_uuid(existing["capture_id"]) != result.capture_id:
                    raise ValueError("reprocessed capture must preserve capture_id")
                connection.execute(
                    """
                    DELETE FROM memory_capture_outcomes
                    WHERE capture_id = %s AND owner_id = %s
                    """,
                    (result.capture_id, result.owner_id),
                )
                connection.execute(
                    """
                    UPDATE memory_capture_runs
                    SET profile_version = %s,
                        profile_fingerprint = %s,
                        prompt_version = %s,
                        schema_version = %s,
                        model_id = %s,
                        status = %s,
                        failure_code = %s,
                        completed_at = %s
                    WHERE capture_id = %s AND owner_id = %s
                    """,
                    (
                        result.metadata.profile_version,
                        result.metadata.profile_fingerprint,
                        result.metadata.prompt_version,
                        result.metadata.schema_version,
                        result.metadata.model_id,
                        result.status.value,
                        result.failure_code,
                        result.completed_at,
                        result.capture_id,
                        result.owner_id,
                    ),
                )
            for record in write.memories:
                self._insert_record(connection, record)
            for duplicate in write.duplicate_evidence:
                target = connection.execute(
                    """
                    SELECT 1
                    FROM memory_revisions
                    WHERE owner_id = %s
                      AND memory_id = %s
                      AND revision_id = %s
                      AND is_current
                      AND lifecycle_status = 'active'
                    FOR UPDATE
                    """,
                    (
                        principal.owner_id,
                        duplicate.memory_id,
                        duplicate.expected_revision_id,
                    ),
                ).fetchone()
                if target is None:
                    raise RuntimeError("duplicate target is no longer current")
                self._insert_evidence(connection, (duplicate.evidence,))
            for replacement in write.replacements:
                cursor = connection.execute(
                    """
                    UPDATE memory_revisions
                    SET is_current = FALSE, lifecycle_status = 'superseded'
                    WHERE owner_id = %s
                      AND memory_id = %s
                      AND revision_id = %s
                      AND is_current
                      AND lifecycle_status = 'active'
                    """,
                    (
                        principal.owner_id,
                        replacement.memory_id,
                        replacement.expected_revision_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("replacement target is no longer current")
                self._insert_revision(
                    connection,
                    replacement.revision,
                    replacement.evidence,
                )
                stale_relation_count += _stale_revision_relations(
                    connection,
                    principal,
                    replacement.memory_id,
                    replacement.revision.revision_id,
                    stale_at=replacement.revision.created_at,
                )
            for relation in write.relations:
                _insert_relation(
                    connection,
                    self._profiles,
                    principal,
                    relation,
                    effective_at=relation.created_at,
                )
            for review in write.reviews:
                self._insert_review(connection, result.capture_id, review)
            _executemany(
                connection,
                """
                INSERT INTO memory_capture_outcomes (
                    capture_id, candidate_id, owner_id, outcome_order,
                    decision, reason_code, memory_id, review_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        result.capture_id,
                        outcome.candidate_id,
                        result.owner_id,
                        outcome_order,
                        outcome.decision.value,
                        outcome.reason_code,
                        outcome.memory_id,
                        outcome.review_id,
                    )
                    for outcome_order, outcome in enumerate(result.outcomes)
                ],
            )
        log_event(
            _LOGGER,
            logging.DEBUG,
            "memory.postgresql.capture_committed",
            capture_id=result.capture_id,
            outcome_count=len(result.outcomes),
            owner_ref=stable_reference(principal.owner_id),
            relation_count=len(write.relations),
            stale_relation_count=stale_relation_count,
            status=result.status.value,
        )

    def list_reviews(
        self,
        principal: PrincipalContext,
        *,
        status: ReviewStatus,
    ) -> Sequence[ReviewItem]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                f"{_SELECT_REVIEW} "
                "WHERE owner_id = %s AND status = %s "
                "ORDER BY created_at, review_id",
                (principal.owner_id, status.value),
            ).fetchall()
        return tuple(to_review(row) for row in rows)

    def get_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                f"{_SELECT_REVIEW} WHERE owner_id = %s AND review_id = %s",
                (principal.owner_id, review_id),
            ).fetchone()
        if row is None:
            return None
        return to_review(row)

    def resolve_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
        *,
        status: ReviewStatus,
        decided_at: datetime,
        memory: MemoryRecord | None = None,
        duplicate_evidence: DuplicateEvidenceWrite | None = None,
        replacement: ReplacementWrite | None = None,
    ) -> ReviewItem | None:
        if status not in {ReviewStatus.CONFIRMED, ReviewStatus.REJECTED}:
            raise ValueError("review resolution must be confirmed or rejected")
        with self._pool.connection() as connection:
            row = connection.execute(
                f"{_SELECT_REVIEW} WHERE owner_id = %s AND review_id = %s FOR UPDATE",
                (principal.owner_id, review_id),
            ).fetchone()
            if row is None:
                return None
            review = to_review(row)
            if review.status is status:
                return review
            if review.status is not ReviewStatus.PENDING:
                return None
            if status is ReviewStatus.CONFIRMED:
                writes = tuple(
                    value
                    for value in (memory, duplicate_evidence, replacement)
                    if value is not None
                )
                if len(writes) != 1:
                    raise ValueError("confirmed review requires one memory write")
                if memory is not None:
                    validate_review_memory(review, memory)
                    self._insert_record(connection, memory)
                elif duplicate_evidence is not None:
                    target = connection.execute(
                        """
                        SELECT 1
                        FROM memory_revisions
                        WHERE owner_id = %s
                          AND memory_id = %s
                          AND revision_id = %s
                          AND is_current
                          AND lifecycle_status = 'active'
                        FOR UPDATE
                        """,
                        (
                            principal.owner_id,
                            duplicate_evidence.memory_id,
                            duplicate_evidence.expected_revision_id,
                        ),
                    ).fetchone()
                    if target is None:
                        raise RuntimeError("duplicate target is no longer current")
                    self._insert_evidence(
                        connection,
                        (duplicate_evidence.evidence,),
                    )
                elif replacement is not None:
                    cursor = connection.execute(
                        """
                        UPDATE memory_revisions
                        SET is_current = FALSE,
                            lifecycle_status = 'superseded'
                        WHERE owner_id = %s
                          AND memory_id = %s
                          AND revision_id = %s
                          AND is_current
                          AND lifecycle_status = 'active'
                        """,
                        (
                            principal.owner_id,
                            replacement.memory_id,
                            replacement.expected_revision_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("replacement target is no longer current")
                    self._insert_revision(
                        connection,
                        replacement.revision,
                        replacement.evidence,
                    )
                    _stale_revision_relations(
                        connection,
                        principal,
                        replacement.memory_id,
                        replacement.revision.revision_id,
                        stale_at=replacement.revision.created_at,
                    )
            elif any(
                value is not None for value in (memory, duplicate_evidence, replacement)
            ):
                raise ValueError("rejected review cannot create memory")
            resolved_memory_id = (
                memory.item.memory_id
                if memory is not None
                else (
                    duplicate_evidence.memory_id
                    if duplicate_evidence is not None
                    else replacement.memory_id
                    if replacement is not None
                    else None
                )
            )
            cursor = connection.execute(
                """
                UPDATE memory_review_items
                SET status = %s, decided_at = %s, resolved_memory_id = %s
                WHERE owner_id = %s
                  AND review_id = %s
                  AND status = 'pending'
                """,
                (
                    status.value,
                    decided_at,
                    (resolved_memory_id if status is ReviewStatus.CONFIRMED else None),
                    principal.owner_id,
                    review_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("pending review changed during resolution")
        return replace(
            review,
            status=status,
            decided_at=decided_at,
            resolved_memory_id=(
                resolved_memory_id if status is ReviewStatus.CONFIRMED else None
            ),
        )

    @staticmethod
    def _insert_capture_run(
        connection,
        result: CaptureResult,
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_capture_runs (
                capture_id, owner_id, profile_id, conversation_id,
                source_turn_id, profile_version, profile_fingerprint, prompt_version,
                schema_version, model_id, status, failure_code,
                created_at, completed_at, event_id, contract_version,
                payload_fingerprint
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                result.capture_id,
                result.owner_id,
                result.profile_id,
                result.conversation_id,
                result.source_turn_id,
                result.metadata.profile_version,
                result.metadata.profile_fingerprint,
                result.metadata.prompt_version,
                result.metadata.schema_version,
                result.metadata.model_id,
                result.status.value,
                result.failure_code,
                result.created_at,
                result.completed_at,
                result.event_id,
                result.contract_version,
                result.payload_fingerprint,
            ),
        )

    @classmethod
    def _insert_record(cls, connection, record: MemoryRecord) -> None:
        item = record.item
        revision = record.current_revision
        connection.execute(
            """
            INSERT INTO memory_items (
                memory_id, owner_id, profile_id, subject, memory_type, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                item.memory_id,
                item.owner_id,
                item.profile_id,
                item.subject,
                item.memory_type,
                item.created_at,
            ),
        )
        cls._insert_revision(connection, revision, record.evidence)

    @classmethod
    def _insert_revision(
        cls,
        connection,
        revision: MemoryRevision,
        evidence: tuple[Evidence, ...],
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_revisions (
                revision_id, memory_id, owner_id, revision_number, content,
                assertion_kind, lifecycle_status, business_progress,
                save_rationale, observed_at, created_at, is_current,
                primary_evidence_id, original_time_expression, normalized_time
                , extraction_confidence, verification_status,
                sensitivity_level, valid_from, valid_until, last_verified_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            (
                revision.revision_id,
                revision.memory_id,
                revision.owner_id,
                revision.revision_number,
                revision.content,
                revision.assertion_kind.value,
                revision.lifecycle_status.value,
                revision.business_progress,
                revision.save_rationale,
                revision.observed_at,
                revision.created_at,
                revision.is_current,
                evidence[0].evidence_id,
                revision.original_time_expression,
                revision.normalized_time,
                revision.extraction_confidence,
                revision.verification_status.value,
                revision.sensitivity_level.value,
                revision.valid_from,
                revision.valid_until,
                revision.last_verified_at,
            ),
        )
        cls._insert_evidence(connection, evidence)

    @staticmethod
    def _insert_evidence(
        connection,
        evidence: tuple[Evidence, ...],
    ) -> None:
        _executemany(
            connection,
            """
            INSERT INTO memory_evidence (
                evidence_id, memory_id, revision_id, owner_id,
                conversation_id, source_turn_id, source_expression,
                observed_at, created_at, source_role, source_message_id,
                source_tool_name, source_type, source_uri, source_title,
                source_publisher, published_at, retrieved_at, content_hash,
                citation_locator
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            [
                (
                    source.evidence_id,
                    source.memory_id,
                    source.revision_id,
                    source.owner_id,
                    source.conversation_id,
                    source.source_turn_id,
                    source.source_expression,
                    source.observed_at,
                    source.created_at,
                    (
                        source.source_role.value
                        if source.source_role is not None
                        else None
                    ),
                    source.source_message_id,
                    source.source_tool_name,
                    source.source_type.value,
                    source.source_uri,
                    source.source_title,
                    source.source_publisher,
                    source.published_at,
                    source.retrieved_at,
                    source.content_hash,
                    source.citation_locator,
                )
                for source in evidence
            ],
        )

    @staticmethod
    def _insert_review(
        connection,
        capture_id: UUID,
        review: ReviewItem,
    ) -> None:
        candidate = review.candidate
        connection.execute(
            """
            INSERT INTO memory_review_items (
                review_id, candidate_id, capture_id, owner_id, profile_id,
                subject, memory_type, content, assertion_kind,
                business_progress, conversation_id, source_turn_id,
                source_expression, save_rationale, confidence, durability,
                expression_basis, observed_at, candidate_created_at,
                original_time_expression, normalized_time, status,
                created_at, decided_at, source_role, source_message_id,
                source_tool_name, verification_status, sensitivity_level,
                valid_from, valid_until, last_verified_at, source_type,
                source_uri, source_title, source_publisher, published_at,
                retrieved_at, content_hash, citation_locator
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            """,
            (
                review.review_id,
                candidate.candidate_id,
                capture_id,
                candidate.owner_id,
                candidate.profile_id,
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
                candidate.observed_at,
                candidate.created_at,
                candidate.original_time_expression,
                candidate.normalized_time,
                review.status.value,
                review.created_at,
                review.decided_at,
                (
                    candidate.source_role.value
                    if candidate.source_role is not None
                    else None
                ),
                candidate.source_message_id,
                candidate.source_tool_name,
                candidate.verification_status.value,
                candidate.sensitivity_level.value,
                candidate.valid_from,
                candidate.valid_until,
                candidate.last_verified_at,
                candidate.source_type.value,
                candidate.source_uri,
                candidate.source_title,
                candidate.source_publisher,
                candidate.published_at,
                candidate.retrieved_at,
                candidate.content_hash,
                candidate.citation_locator,
            ),
        )


def _stale_revision_relations(
    connection,
    principal: PrincipalContext,
    memory_id: UUID,
    current_revision_id: UUID,
    *,
    stale_at: datetime,
) -> int:
    """在 replacement 事务内物化只对旧 revision 成立的关系。"""

    cursor = connection.execute(
        """
        UPDATE memory_relations
        SET status = 'stale',
            stale_at = %s,
            stale_reason = 'endpoint_revision_changed'
        WHERE owner_id = %s
          AND scope = 'revision'
          AND status = 'active'
          AND (
              (source_memory_id = %s AND source_revision_id <> %s)
              OR
              (target_memory_id = %s AND target_revision_id <> %s)
          )
        """,
        (
            stale_at,
            principal.owner_id,
            memory_id,
            current_revision_id,
            memory_id,
            current_revision_id,
        ),
    )
    return cursor.rowcount


def _insert_relation(
    connection,
    profiles: Mapping[str, MemoryProfile],
    principal: PrincipalContext,
    relation: MemoryRelation,
    *,
    effective_at: datetime,
) -> MemoryRelation:
    """在调用方事务内重新校验端点并幂等写入关系。"""

    if relation.owner_id != principal.owner_id:
        raise ValueError("relation owner must match trusted principal")
    if relation.status is not RelationStatus.ACTIVE:
        raise ValueError("new relation must be active")
    if relation.origin is RelationOrigin.LEGACY:
        raise ValueError("new relation cannot use legacy origin")
    endpoints = connection.execute(
        """
        SELECT i.memory_id, i.profile_id, i.memory_type, r.revision_id,
               r.lifecycle_status, r.valid_from, r.valid_until
        FROM memory_items AS i
        JOIN memory_revisions AS r
          ON r.memory_id = i.memory_id
         AND r.owner_id = i.owner_id
         AND r.is_current
        WHERE i.owner_id = %s
          AND i.memory_id = ANY(%s)
        FOR UPDATE OF i, r
        """,
        (
            principal.owner_id,
            [relation.source_memory_id, relation.target_memory_id],
        ),
    ).fetchall()
    by_id = {as_uuid(row["memory_id"]): row for row in endpoints}
    source = by_id.get(relation.source_memory_id)
    target = by_id.get(relation.target_memory_id)
    if source is None or target is None:
        raise ValueError("relation endpoints are unavailable")
    if (
        source["profile_id"] != relation.profile_id
        or target["profile_id"] != relation.profile_id
    ):
        raise ValueError("relation endpoints must share the relation profile")
    profile = profiles.get(relation.profile_id)
    policy = (
        profile.relation_policies.get(relation.relation_type)
        if profile is not None
        else None
    )
    if (
        policy is None
        or source["memory_type"] not in policy.source_memory_types
        or target["memory_type"] not in policy.target_memory_types
    ):
        raise ValueError("relation does not match the registered policy")
    if any(
        row["lifecycle_status"] != "active"
        or row["valid_from"] > effective_at
        or (row["valid_until"] is not None and row["valid_until"] <= effective_at)
        for row in (source, target)
    ):
        raise ValueError("relation endpoints must be active and effective")
    if relation.source_revision_id != as_uuid(
        source["revision_id"]
    ) or relation.target_revision_id != as_uuid(target["revision_id"]):
        raise ValueError("relation revision snapshots must match current endpoints")
    provenance = relation.provenance
    if relation.origin is RelationOrigin.AUTOMATIC:
        if provenance is None:
            raise ValueError("automatic relation requires provenance")
        capture = connection.execute(
            """
            SELECT 1
            FROM memory_capture_runs
            WHERE capture_id = %s
              AND owner_id = %s
              AND profile_id = %s
              AND conversation_id = %s
              AND source_turn_id = %s
              AND status = 'completed'
            """,
            (
                provenance.capture_id,
                principal.owner_id,
                relation.profile_id,
                provenance.conversation_id,
                provenance.source_turn_id,
            ),
        ).fetchone()
        if capture is None:
            raise ValueError("relation provenance capture is unavailable")
    inserted = connection.execute(
        """
        INSERT INTO memory_relations (
            relation_id, owner_id, profile_id, source_memory_id,
            target_memory_id, relation_type, origin, scope,
            source_revision_id, target_revision_id, capture_id,
            conversation_id, source_turn_id, source_expression, confidence,
            expression_basis, model_id, prompt_version, schema_version,
            status, created_at, revoked_at, stale_at, stale_reason
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            'active', %s, NULL, NULL, NULL
        )
        ON CONFLICT (
            owner_id, source_memory_id, target_memory_id, relation_type
        ) WHERE status = 'active'
        DO NOTHING
        RETURNING relation_id
        """,
        (
            relation.relation_id,
            relation.owner_id,
            relation.profile_id,
            relation.source_memory_id,
            relation.target_memory_id,
            relation.relation_type,
            relation.origin.value,
            relation.scope.value,
            relation.source_revision_id,
            relation.target_revision_id,
            provenance.capture_id if provenance is not None else None,
            provenance.conversation_id if provenance is not None else None,
            provenance.source_turn_id if provenance is not None else None,
            provenance.source_expression if provenance is not None else None,
            provenance.confidence if provenance is not None else None,
            (provenance.expression_basis.value if provenance is not None else None),
            provenance.model_id if provenance is not None else None,
            provenance.prompt_version if provenance is not None else None,
            provenance.schema_version if provenance is not None else None,
            relation.created_at,
        ),
    ).fetchone()
    if inserted is not None:
        row = connection.execute(
            f"{_SELECT_RELATION} WHERE relation_id = %s",
            (inserted["relation_id"],),
        ).fetchone()
    else:
        row = connection.execute(
            f"{_SELECT_RELATION} "
            "WHERE owner_id = %s AND source_memory_id = %s "
            "AND target_memory_id = %s AND relation_type = %s "
            "AND status = 'active'",
            (
                relation.owner_id,
                relation.source_memory_id,
                relation.target_memory_id,
                relation.relation_type,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("active relation was not committed")
    return _load_relation(row)


def _executemany(
    connection, query: str, parameters: Sequence[Sequence[object]]
) -> None:
    if not parameters:
        return
    with connection.cursor() as cursor:
        cursor.executemany(query, parameters)


_SELECT_CURRENT_RECORD = """
SELECT i.memory_id, i.owner_id, i.profile_id, i.subject, i.memory_type,
       i.created_at AS item_created_at,
       r.revision_id, r.revision_number, r.content, r.assertion_kind,
       r.lifecycle_status, r.business_progress, r.save_rationale,
       r.observed_at AS revision_observed_at,
       r.created_at AS revision_created_at, r.is_current,
       r.original_time_expression, r.normalized_time,
       r.extraction_confidence, r.verification_status, r.sensitivity_level,
       r.valid_from, r.valid_until, r.last_verified_at
FROM memory_items AS i
JOIN memory_revisions AS r
  ON r.memory_id = i.memory_id
 AND r.owner_id = i.owner_id
 AND r.is_current
"""

_SELECT_REVIEW = """
SELECT review_id, candidate_id, owner_id, profile_id, subject, memory_type,
       content, assertion_kind, business_progress, conversation_id,
       source_turn_id, source_expression, save_rationale, confidence,
       durability, expression_basis, observed_at, candidate_created_at,
       original_time_expression, normalized_time, status, created_at,
       decided_at, resolved_memory_id, source_role, source_message_id,
       source_tool_name, verification_status, sensitivity_level,
       valid_from, valid_until, last_verified_at, source_type, source_uri,
       source_title, source_publisher, published_at, retrieved_at,
       content_hash, citation_locator
FROM memory_review_items
"""

_SELECT_RELATION = """
SELECT relation_id, owner_id, profile_id, source_memory_id, target_memory_id,
       relation_type, origin, scope, source_revision_id, target_revision_id,
       capture_id, conversation_id, source_turn_id, source_expression,
       confidence, expression_basis, model_id, prompt_version, schema_version,
       status, created_at, revoked_at, stale_at, stale_reason
FROM memory_relations
"""


def _load_relation(row: Mapping[str, Any]) -> MemoryRelation:
    origin = RelationOrigin(row["origin"])
    provenance = (
        RelationProvenance(
            capture_id=as_uuid(row["capture_id"]),
            conversation_id=row["conversation_id"],
            source_turn_id=row["source_turn_id"],
            source_expression=row["source_expression"],
            confidence=row["confidence"],
            expression_basis=ExpressionBasis(row["expression_basis"]),
            model_id=row["model_id"],
            prompt_version=row["prompt_version"],
            schema_version=row["schema_version"],
        )
        if origin is RelationOrigin.AUTOMATIC
        else None
    )
    return MemoryRelation(
        relation_id=as_uuid(row["relation_id"]),
        owner_id=row["owner_id"],
        profile_id=row["profile_id"],
        source_memory_id=as_uuid(row["source_memory_id"]),
        target_memory_id=as_uuid(row["target_memory_id"]),
        relation_type=row["relation_type"],
        origin=origin,
        scope=RelationScope(row["scope"]),
        source_revision_id=(
            as_uuid(row["source_revision_id"])
            if row["source_revision_id"] is not None
            else None
        ),
        target_revision_id=(
            as_uuid(row["target_revision_id"])
            if row["target_revision_id"] is not None
            else None
        ),
        provenance=provenance,
        status=RelationStatus(row["status"]),
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
        stale_at=row["stale_at"],
        stale_reason=row["stale_reason"],
    )
