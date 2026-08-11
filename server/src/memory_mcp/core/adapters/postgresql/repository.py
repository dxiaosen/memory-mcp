"""使用 PostgreSQL 实现的 owner-scoped Memory Repository。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from math import ceil
from typing import Any
from uuid import UUID

from psycopg.errors import UniqueViolation
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
from memory_mcp.core.adapters.postgresql.recall import (
    find_recall_candidates_by_ids as query_recall_candidates_by_ids,
)
from memory_mcp.core.adapters.postgresql.recall import (
    load_recall_evidence as query_recall_evidence,
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
    MemoryRecallCandidate,
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
    TeamExtractionResult,
    average_embedding,
    format_divergence_rationale,
    has_conflicting_business_progress,
    normalize_memory_text,
    select_cluster_content,
    select_cluster_subject,
)
from memory_mcp.core.exceptions import (
    IdempotencyConflictError,
    SubjectScopeConflictError,
)
from memory_mcp.core.ports import (
    CaptureWrite,
    DuplicateEvidenceWrite,
    MemoryProfile,
    RecallCandidateSet,
    ReplacementWrite,
)
from memory_mcp.core.support import log_event, stable_reference

PostgreSQLPool = ConnectionPool  # type: ignore[type-arg]
_LOGGER = logging.getLogger(__name__)


def create_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 5,
    timeout: float = 10.0,
) -> PostgreSQLPool:
    """为 MCP 服务打开有界同步连接池。"""

    pool = ConnectionPool(  # type: ignore[assignment]
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
    try:
        pool.open(wait=True, timeout=timeout)
    except Exception as exc:
        log_event(
            _LOGGER,
            logging.ERROR,
            "memory.postgresql.pool_open_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise
    log_event(
        _LOGGER,
        logging.INFO,
        "memory.postgresql.pool_opened",
        min_size=min_size,
        max_size=max_size,
    )
    return pool  # type: ignore[return-value]


class PostgreSQLMemoryRepository:
    """以 PostgreSQL 事务和约束作为持久化边界。

    读取查询用 ``owner_id = ANY(%s)`` 配合 ``principal.visible_owner_ids``
    集合过滤（支持团队可见记忆）；写入语句用单值 ``owner_id = %s``，
    确保数据归属精确到调用 principal 或其授权的团队 owner。
    """

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
        """注册 profile 到进程内 ProfileRegistry；DB 表已删除，Profile 是代码定义的。"""

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
        # 允许写入个人 owner 或 principal 所属团队的 owner（团队提升路径）。
        # 写入用单值 owner_id = %s（见各类 INSERT 语句），读取才用 ANY(%s) 集合。
        if record.item.owner_id not in principal.visible_owner_ids:
            raise ValueError("record owner must match trusted principal or team")
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
        """按 owner 可见集合读取单条当前记忆，不可见时返回 None。"""

        with self._pool.connection() as connection:
            row = connection.execute(
                f"{_SELECT_CURRENT_RECORD} WHERE i.owner_id = ANY(%s) AND i.memory_id = %s",
                (list(principal.visible_owner_ids), memory_id),
            ).fetchone()
            if row is None:
                return None
            return to_record(connection, row, row["owner_id"])

    def list(
        self,
        principal: PrincipalContext,
        *,
        active_only: bool,
        effective_at: datetime | None = None,
    ) -> Sequence[MemoryRecord]:
        """列出 owner 可见集合内的全部当前记忆。"""

        conditions = ["i.owner_id = ANY(%s)"]
        parameters: list[object] = [list(principal.visible_owner_ids)]
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
            return tuple(to_record(connection, row, row["owner_id"]) for row in rows)

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
        """在 owner/profile 边界内查找活动且生效的当前记忆。

        subject 在 SQL 侧先做空白归一化粗筛，回到 Python 侧再用
        ``normalize_memory_text`` 精确比对，避免数据库 collation 差异。
        """

        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        conditions = [
            "i.owner_id = ANY(%s)",
            "i.profile_id = %s",
            "r.lifecycle_status = 'active'",
            "r.valid_from <= %s",
            "(r.valid_until IS NULL OR r.valid_until > %s)",
        ]
        resolved_time = effective_at or datetime.now(UTC)
        parameters: list[object] = [
            list(principal.visible_owner_ids),
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
            records = tuple(to_record(connection, row, row["owner_id"]) for row in rows)
        if subject_key is None:
            return records
        return tuple(
            record
            for record in records
            if normalize_memory_text(record.item.subject) == subject_key
        )

    def find_semantically_similar(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        memory_type: str,
        embedding: Sequence[float],
        threshold: float,
        effective_at: datetime,
    ) -> MemoryRecord | None:
        """按嵌入余弦相似度在同 profile+type 活动记忆中找首条超阈值命中。

        复用 pgvector 余弦距离算子 ``<=>``：``1 - distance`` 即余弦相似度。
        SQL 侧按距离升序取前若干条候选，Python 侧再以 ``similarity >= threshold``
        过滤，兼顾精度与对 collation 无关的稳定判定。
        """

        vector = list(embedding)
        vector_literal = str(vector).replace("'", "''")
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT i.memory_id, i.owner_id, i.profile_id, i.subject,
                       i.memory_type, i.created_at AS item_created_at,
                       r.revision_id, r.revision_number, r.content,
                       r.assertion_kind, r.lifecycle_status, r.business_progress,
                       r.save_rationale,
                       r.observed_at AS revision_observed_at,
                       r.created_at AS revision_created_at, r.is_current,
                       r.original_time_expression, r.normalized_time,
                       r.extraction_confidence, r.verification_status,
                       r.sensitivity_level, r.valid_from, r.valid_until,
                       (r.embedding <=> %s::vector) AS embedding_distance
                FROM memory_items AS i
                JOIN memory_revisions AS r
                  ON r.memory_id = i.memory_id
                 AND r.owner_id = i.owner_id
                 AND r.is_current
                WHERE i.owner_id = ANY(%s)
                  AND i.profile_id = %s
                  AND i.memory_type = %s
                  AND r.lifecycle_status = 'active'
                  AND r.valid_from <= %s
                  AND (r.valid_until IS NULL OR r.valid_until > %s)
                  AND r.embedding IS NOT NULL
                ORDER BY r.embedding <=> %s::vector
                LIMIT 5
                """,
                (
                    vector_literal,
                    list(principal.visible_owner_ids),
                    profile_id,
                    memory_type,
                    effective_at,
                    effective_at,
                    vector_literal,
                ),
            ).fetchall()
            if not rows:
                return None
            best: tuple[float, MemoryRecord] | None = None
            for row in rows:
                distance = row["embedding_distance"]
                similarity = 1.0 - float(distance)
                if similarity < threshold:
                    continue
                if best is None or similarity > best[0]:
                    best = (
                        similarity,
                        to_record(connection, row, row["owner_id"]),
                    )
            return best[1] if best is not None else None

    def find_semantically_similar_top2(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        memory_type: str,
        embedding: Sequence[float],
        threshold: float,
        effective_at: datetime,
    ) -> tuple[
        tuple[float, MemoryRecord] | None,
        tuple[float, MemoryRecord] | None,
    ]:
        """返回相似度最高的两条活动记忆及其相似度。"""

        vector = list(embedding)
        vector_literal = str(vector).replace("'", "''")
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT i.memory_id, i.owner_id, i.profile_id, i.subject,
                       i.memory_type, i.created_at AS item_created_at,
                       r.revision_id, r.revision_number, r.content,
                       r.assertion_kind, r.lifecycle_status, r.business_progress,
                       r.save_rationale,
                       r.observed_at AS revision_observed_at,
                       r.created_at AS revision_created_at, r.is_current,
                       r.original_time_expression, r.normalized_time,
                       r.extraction_confidence, r.verification_status,
                       r.sensitivity_level, r.valid_from, r.valid_until,
                       (r.embedding <=> %s::vector) AS embedding_distance
                FROM memory_items AS i
                JOIN memory_revisions AS r
                  ON r.memory_id = i.memory_id
                 AND r.owner_id = i.owner_id
                 AND r.is_current
                WHERE i.owner_id = ANY(%s)
                  AND i.profile_id = %s
                  AND i.memory_type = %s
                  AND r.lifecycle_status = 'active'
                  AND r.valid_from <= %s
                  AND (r.valid_until IS NULL OR r.valid_until > %s)
                  AND r.embedding IS NOT NULL
                ORDER BY r.embedding <=> %s::vector
                LIMIT 5
                """,
                (
                    vector_literal,
                    list(principal.visible_owner_ids),
                    profile_id,
                    memory_type,
                    effective_at,
                    effective_at,
                    vector_literal,
                ),
            ).fetchall()
            top1: tuple[float, MemoryRecord] | None = None
            top2: tuple[float, MemoryRecord] | None = None
            for row in rows:
                distance = row["embedding_distance"]
                similarity = 1.0 - float(distance)
                if similarity < threshold:
                    continue
                record = to_record(connection, row, row["owner_id"])
                if top1 is None or similarity > top1[0]:
                    top2 = top1
                    top1 = (similarity, record)
                elif top2 is None or similarity > top2[0]:
                    top2 = (similarity, record)
            return top1, top2

    def find_recall_candidates(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        search_text: str,
        subject: str | None,
        effective_at: datetime,
        limit: int,
        query_embedding: Sequence[float] | None = None,
    ) -> RecallCandidateSet:
        """在 owner/Profile 边界内用词法、向量和近期三路混合召回候选。"""

        with self._pool.connection() as connection:
            return query_recall_candidates(
                connection,
                principal,
                profile_id=profile_id,
                search_text=search_text,
                subject=subject,
                effective_at=effective_at,
                limit=limit,
                query_embedding=query_embedding,
            )

    def find_recall_candidates_by_ids(
        self,
        principal: PrincipalContext,
        *,
        memory_ids: Sequence[UUID],
        effective_at: datetime,
    ) -> tuple[MemoryRecallCandidate, ...]:
        """按 memory_id 集合加载可见的当前活动候选（关系感知召回补漏用）。"""

        with self._pool.connection() as connection:
            return query_recall_candidates_by_ids(
                connection,
                principal,
                memory_ids=memory_ids,
                effective_at=effective_at,
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

    def extract_team_common_memories(
        self,
        *,
        team_owner_id: str,
        member_owner_ids: tuple[str, ...],
        profile_id: str,
        effective_at: datetime,
        similarity_threshold: float,
        min_cluster_size: int,
    ) -> TeamExtractionResult:
        """扫描成员个人记忆，用 embedding 聚类提取共性并写入团队 pending review。"""

        if not member_owner_ids:
            return TeamExtractionResult(
                team_owner_id=team_owner_id,
                member_count=0,
                memory_count=0,
                cluster_count=0,
                candidate_count=0,
                completed_at=effective_at,
            )
        run_id = (
            self._id_factory()
            if hasattr(self, "_id_factory")
            else __import__("uuid").uuid4()
        )  # type: ignore[union-attr]
        with self._pool.connection() as connection:
            return _extract_team_common(
                connection,
                team_owner_id=team_owner_id,
                member_owner_ids=member_owner_ids,
                profile_id=profile_id,
                effective_at=effective_at,
                similarity_threshold=similarity_threshold,
                min_cluster_size=min_cluster_size,
                run_id=run_id,
            )

    def load_recall_evidence(
        self,
        principal: PrincipalContext,
        *,
        revision_ids: Sequence[UUID],
        per_revision_limit: int,
    ) -> Mapping[UUID, tuple[Evidence, ...]]:
        """批量加载最终召回项的有限来源。"""

        with self._pool.connection() as connection:
            return query_recall_evidence(
                connection,
                principal,
                revision_ids=tuple(revision_ids),
                per_revision_limit=per_revision_limit,
            )

    def revoke(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        """把可见的 active revision 标记为 revoked，并物化其 revision-scoped 活动关系为 stale。"""

        with self._pool.connection() as connection:
            row = connection.execute(
                f"{_SELECT_CURRENT_RECORD} "
                "WHERE i.owner_id = ANY(%s) AND i.memory_id = %s FOR UPDATE",
                (list(principal.visible_owner_ids), memory_id),
            ).fetchone()
            if row is None:
                return None
            if row["lifecycle_status"] == "revoked":
                return to_record(connection, row, row["owner_id"])
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
                (row["owner_id"], memory_id, row["revision_id"]),
            )
            # 同步 memory_items.lifecycle_status，使部分唯一索引释放该 subject+type 槽位。
            connection.execute(
                """
                UPDATE memory_items
                SET lifecycle_status = 'revoked'
                WHERE owner_id = %s AND memory_id = %s
                """,
                (row["owner_id"], memory_id),
            )
            # 与 replacement 对齐：revoke 也把指向该 memory 当前 revision 的
            # revision-scoped 活动边物化为 stale，避免 memory_relations.status
            # 与端点实际状态不一致。item-scoped 手动边不受影响。
            # stale_at 必须用「撤销时刻」而非 revision_created_at：关系可能在
            # 该 revision 创建之后才建立，用 revision_created_at 会令
            # stale_at < relation.created_at，违反 memory_relations_terminal_state
            # 的 stale_at >= created_at 约束。
            revoked_principal = PrincipalContext(row["owner_id"])
            _stale_revision_relations(
                connection,
                revoked_principal,
                memory_id,
                row["revision_id"],
                stale_at=datetime.now(UTC),
                stale_reason="endpoint_revoked",
            )
            row["lifecycle_status"] = "revoked"  # type: ignore[index]
            return to_record(connection, row, row["owner_id"])

    def link_relation(
        self,
        principal: PrincipalContext,
        relation: MemoryRelation,
        *,
        effective_at: datetime,
    ) -> MemoryRelation:
        """显式写入手动关系，端点须在可见 owner 集合内且 active。"""

        if relation.owner_id not in principal.visible_owner_ids:
            raise ValueError("relation owner must match trusted principal or team")
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
        """把可见的 active relation 标记为 revoked。"""

        with self._pool.connection() as connection:
            row = connection.execute(
                f"{_SELECT_RELATION} "
                "WHERE owner_id = ANY(%s) AND relation_id = %s FOR UPDATE",
                (list(principal.visible_owner_ids), relation_id),
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
                (revoked_at, row["owner_id"], relation_id),
            )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                f"{_SELECT_RELATION} WHERE owner_id = %s AND relation_id = %s",
                (row["owner_id"], relation_id),
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
        """列出与指定 memory 集合关联的关系摘要（含出入方向）。"""

        requested = frozenset(memory_ids)
        if not requested:
            return ()
        conditions = [
            "rel.owner_id = ANY(%s)",
            "(rel.source_memory_id = ANY(%s) OR rel.target_memory_id = ANY(%s))",
        ]
        parameters: list[object] = [
            list(principal.visible_owner_ids),
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
        """按 revision 倒序返回可见记忆的完整历史（含每版 evidence）。"""

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
                       r.valid_from, r.valid_until
                FROM memory_items AS i
                JOIN memory_revisions AS r
                  ON r.memory_id = i.memory_id
                 AND r.owner_id = i.owner_id
                WHERE i.owner_id = ANY(%s) AND i.memory_id = %s
                ORDER BY r.revision_number DESC
                """,
                (list(principal.visible_owner_ids), memory_id),
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
        """按 event_id 或 legacy 四元组查询幂等 capture 结果。"""

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
                FROM memory_captures
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
    ) -> CaptureResult:
        """在一个事务内幂等提交 capture 及其全部派生写入。

        用 advisory lock 防止同一幂等键并发写入；已存在且未标记
        ``REPROCESS_REQUIRED`` 的 capture 直接重放返回，需要重处理时
        先清理旧 outcome 再重新写入。replacement 会同步把引用旧
        revision 的 active relation 置为 stale。
        """

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
                SELECT capture_id, owner_id, profile_id, conversation_id,
                       source_turn_id, profile_version, profile_fingerprint,
                       prompt_version, schema_version, model_id, status, failure_code,
                       created_at, completed_at, event_id,
                       contract_version, payload_fingerprint
                FROM memory_captures
                WHERE {where_clause}
                FOR UPDATE
                """,
                parameters,
            ).fetchone()
            if existing is None:
                self._insert_capture_run(connection, result)
            else:
                if (
                    result.payload_fingerprint is not None
                    and existing["payload_fingerprint"] != result.payload_fingerprint
                ):
                    raise IdempotencyConflictError(
                        "event identifier was reused with a different payload"
                    )
                if existing["status"] != CaptureStatus.REPROCESS_REQUIRED.value:
                    stored = replace(
                        to_capture_result(connection, existing),
                        replayed=True,
                    )
                    log_event(
                        _LOGGER,
                        logging.DEBUG,
                        "memory.postgresql.capture_replayed",
                        capture_id=stored.capture_id,
                        owner_ref=stable_reference(principal.owner_id),
                        status=stored.status.value,
                    )
                    return stored
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
                    UPDATE memory_captures
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
                self._insert_record(
                    connection, record, capture_id=result.capture_id
                )
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
        return result

    def list_reviews(
        self,
        principal: PrincipalContext,
        *,
        status: ReviewStatus,
    ) -> Sequence[ReviewItem]:
        """列出 owner 可见集合内指定状态的待审项。"""

        with self._pool.connection() as connection:
            rows = connection.execute(
                f"{_SELECT_REVIEW} "
                "WHERE ri.owner_id = ANY(%s) AND ri.status = %s "
                "ORDER BY ri.created_at, ri.review_id",
                (list(principal.visible_owner_ids), status.value),
            ).fetchall()
        return tuple(to_review(row) for row in rows)

    def get_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem | None:
        """读取单条可见 review（含 candidate 和可选文档来源）。"""

        with self._pool.connection() as connection:
            row = connection.execute(
                f"{_SELECT_REVIEW} WHERE ri.owner_id = ANY(%s) AND ri.review_id = %s",
                (list(principal.visible_owner_ids), review_id),
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
        """在一个事务内完成 review 决议及其派生写入。

        CONFIRMED 需要 memory/duplicate_evidence/replacement 三选一；
        REJECTED 不允许携带任何派生写入。用 FOR UPDATE 锁定 review 行，
        确保并发决议不会产生竞态。
        """

        if status not in {ReviewStatus.CONFIRMED, ReviewStatus.REJECTED}:
            raise ValueError("review resolution must be confirmed or rejected")
        with self._pool.connection() as connection:
            # 用含 JOIN 的查询获取完整 review（含文档字段），
            # 再用主表 FOR UPDATE 锁行。不能用 JOIN 的 FOR UPDATE（PostgreSQL 限制）。
            row = connection.execute(
                f"{_SELECT_REVIEW} WHERE ri.owner_id = ANY(%s) AND ri.review_id = %s",
                (list(principal.visible_owner_ids), review_id),
            ).fetchone()
            if row is not None:
                # 锁主表行
                connection.execute(
                    "SELECT 1 FROM memory_reviews "
                    "WHERE owner_id = ANY(%s) AND review_id = %s FOR UPDATE",
                    (list(principal.visible_owner_ids), review_id),
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
                UPDATE memory_reviews
                SET status = %s, decided_at = %s, resolved_memory_id = %s
                WHERE owner_id = ANY(%s)
                  AND review_id = %s
                  AND status = 'pending'
                """,
                (
                    status.value,
                    decided_at,
                    (resolved_memory_id if status is ReviewStatus.CONFIRMED else None),
                    list(principal.visible_owner_ids),
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
        """插入一条 capture run 行。"""

        connection.execute(
            """
            INSERT INTO memory_captures (
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
    def _insert_record(
        cls,
        connection,
        record: MemoryRecord,
        *,
        capture_id: UUID | None = None,
    ) -> None:
        """插入 memory_item 及其当前 revision 和 evidence。"""

        item = record.item
        revision = record.current_revision
        try:
            connection.execute(
                """
                INSERT INTO memory_items (
                    memory_id, owner_id, profile_id, subject, memory_type, created_at,
                    lifecycle_status, capture_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item.memory_id,
                    item.owner_id,
                    item.profile_id,
                    item.subject,
                    item.memory_type,
                    item.created_at,
                    revision.lifecycle_status.value,
                    capture_id,
                ),
            )
        except UniqueViolation as exc:
            # find_current 与写入跨事务的 TOCTOU：同 (owner, profile, subject,
            # memory_type) 已有活动记忆。转抛为 Core 语义异常，供工具层映射成
            # 明确的 subject_scope_conflict 而非泛泛 temporarily_unavailable。
            raise SubjectScopeConflictError(
                "active memory already occupies this subject and memory_type"
            ) from exc
        cls._insert_revision(connection, revision, record.evidence)

    @classmethod
    def _insert_revision(
        cls,
        connection,
        revision: MemoryRevision,
        evidence: tuple[Evidence, ...],
    ) -> None:
        """插入 revision 行（首条 evidence 作为 primary_evidence_id）。"""

        connection.execute(
            """
            INSERT INTO memory_revisions (
                revision_id, memory_id, owner_id, revision_number, content,
                assertion_kind, lifecycle_status, business_progress,
                save_rationale, observed_at, created_at, is_current,
                primary_evidence_id, original_time_expression, normalized_time
                , extraction_confidence, verification_status,
                sensitivity_level, valid_from, valid_until, embedding
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
                list(revision.embedding) if revision.embedding else None,
            ),
        )
        cls._insert_evidence(connection, evidence)

    @staticmethod
    def _insert_evidence(
        connection,
        evidence: tuple[Evidence, ...],
    ) -> None:
        """批量插入 evidence 行，有文档来源的再写 evidence_documents 子表。"""

        _executemany(
            connection,
            """
            INSERT INTO memory_evidence (
                evidence_id, memory_id, revision_id, owner_id,
                conversation_id, source_turn_id, source_expression,
                observed_at, created_at, source_role, source_message_id,
                source_tool_name, source_type
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
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
                )
                for source in evidence
            ],
        )
        # 文档来源的元数据写入子表（仅 document/web 来源有行）。
        document_rows = [
            (
                source.evidence_id,
                source.document.source_uri,
                source.document.source_title,
                source.document.source_publisher,
                source.document.published_at,
                source.document.retrieved_at,
                source.document.content_hash,
                source.document.citation_locator,
            )
            for source in evidence
            if source.document is not None
        ]
        if document_rows:
            _executemany(
                connection,
                """
                INSERT INTO memory_evidence_documents (
                    evidence_id, source_uri, source_title, source_publisher,
                    published_at, retrieved_at, content_hash, citation_locator
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                document_rows,
            )

    @staticmethod
    def _insert_review(
        connection,
        capture_id: UUID,
        review: ReviewItem,
    ) -> None:
        """插入 review 行，有文档来源的再写 review_documents 子表。"""

        candidate = review.candidate
        connection.execute(
            """
            INSERT INTO memory_reviews (
                review_id, candidate_id, capture_id, owner_id, profile_id,
                subject, memory_type, content, assertion_kind,
                business_progress, conversation_id, source_turn_id,
                source_expression, save_rationale, confidence, durability,
                expression_basis, observed_at, candidate_created_at,
                original_time_expression, normalized_time, status,
                created_at, decided_at, source_role, source_message_id,
                source_tool_name, verification_status, sensitivity_level,
                valid_from, valid_until, source_type
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
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
                candidate.source_type.value,
            ),
        )
        # 文档来源的元数据写入子表（仅 document/web 来源有行）。
        has_document = any(
            getattr(candidate, field) is not None
            for field in (
                "source_uri",
                "source_title",
                "source_publisher",
                "published_at",
                "retrieved_at",
                "content_hash",
                "citation_locator",
            )
        )
        if has_document:
            connection.execute(
                """
                INSERT INTO memory_review_documents (
                    review_id, source_uri, source_title, source_publisher,
                    published_at, retrieved_at, content_hash, citation_locator
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    review.review_id,
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
    stale_reason: str = "endpoint_revision_changed",
) -> int:
    """在 replacement/revoke 事务内物化只对旧 revision 成立的关系。

    ``stale_reason`` 为 ``endpoint_revision_changed``（replacement，旧 revision 变非 current）
    或 ``endpoint_revoked``（revoke，端点被撤销）。两条路径都只对 revision-scoped
    活动边生效；item-scoped 手动边不受影响。
    """

    if stale_reason == "endpoint_revision_changed":
        clause = (
            "(source_memory_id = %s AND source_revision_id <> %s)"
            " OR "
            "(target_memory_id = %s AND target_revision_id <> %s)"
        )
        params = (
            stale_at,
            stale_reason,
            principal.owner_id,
            memory_id,
            current_revision_id,
            memory_id,
            current_revision_id,
        )
    else:
        # revoke：端点被撤销，所有指向该 memory 当前 revision 的 revision-scoped 活动边失效。
        clause = "source_memory_id = %s OR target_memory_id = %s"
        params = (
            stale_at,
            stale_reason,
            principal.owner_id,
            memory_id,
            memory_id,
        )
    cursor = connection.execute(
        f"""
        UPDATE memory_relations
        SET status = 'stale',
            stale_at = %s,
            stale_reason = %s
        WHERE owner_id = %s
          AND scope = 'revision'
          AND status = 'active'
          AND ({clause})
        """,
        params,
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

    if relation.owner_id not in principal.visible_owner_ids:
        raise ValueError("relation owner must match trusted principal or team")
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
        WHERE i.owner_id = ANY(%s)
          AND i.memory_id = ANY(%s)
        FOR UPDATE OF i, r
        """,
        (
            list(principal.visible_owner_ids),
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
            FROM memory_captures
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
    """批量执行，参数为空时跳过以避免无意义的往返。"""

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
       r.valid_from, r.valid_until
FROM memory_items AS i
JOIN memory_revisions AS r
  ON r.memory_id = i.memory_id
 AND r.owner_id = i.owner_id
 AND r.is_current
"""

_SELECT_REVIEW = """
SELECT ri.review_id, ri.candidate_id, ri.owner_id, ri.profile_id,
       ri.subject, ri.memory_type, ri.content, ri.assertion_kind,
       ri.business_progress, ri.conversation_id, ri.source_turn_id,
       ri.source_expression, ri.save_rationale, ri.confidence,
       ri.durability, ri.expression_basis, ri.observed_at,
       ri.candidate_created_at, ri.original_time_expression,
       ri.normalized_time, ri.status, ri.created_at, ri.decided_at,
       ri.resolved_memory_id, ri.source_role, ri.source_message_id,
       ri.source_tool_name, ri.verification_status, ri.sensitivity_level,
       ri.valid_from, ri.valid_until, ri.source_type,
       rd.source_uri AS doc_source_uri, rd.source_title AS doc_source_title,
       rd.source_publisher AS doc_source_publisher,
       rd.published_at AS doc_published_at,
       rd.retrieved_at AS doc_retrieved_at,
       rd.content_hash AS doc_content_hash,
       rd.citation_locator AS doc_citation_locator
FROM memory_reviews AS ri
LEFT JOIN memory_review_documents AS rd
  ON rd.review_id = ri.review_id
"""

# 不含 JOIN 的 review 查询，用于 FOR UPDATE（FOR UPDATE 不能用于 outer join 的可空侧）。
_SELECT_REVIEW_FOR_UPDATE = """
SELECT review_id, candidate_id, owner_id, profile_id, subject, memory_type,
       content, assertion_kind, business_progress, conversation_id,
       source_turn_id, source_expression, save_rationale, confidence,
       durability, expression_basis, observed_at, candidate_created_at,
       original_time_expression, normalized_time, status, created_at,
       decided_at, resolved_memory_id, source_role, source_message_id,
       source_tool_name, verification_status, sensitivity_level,
       valid_from, valid_until, source_type
FROM memory_reviews
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
    """把 relation 行映射为 MemoryRelation，自动 origin 关系构造 provenance。"""

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



def _extract_team_common(
    connection,
    *,
    team_owner_id: str,
    member_owner_ids: tuple[str, ...],
    profile_id: str,
    effective_at: datetime,
    similarity_threshold: float,
    min_cluster_size: int,
    run_id: UUID,
) -> TeamExtractionResult:
    """在事务内扫描成员记忆、聚类、写团队 pending review 并记录运行。

    Run 级幂等：插入 run 行用 ``ON CONFLICT DO NOTHING``，若同 (team, profile,
    completed_at) 已有 run 则加载其计数直接返回，不重复扫描/聚类/写 pending。

    按 ``memory_type`` 分组后做 embedding 余弦相似度贪心聚类；簇需满足最小尺寸且
    至少 2 个不同成员。簇内 subject/content 选择用确定性纯函数（频次优先 + 字典序
    兜底），并在 save_rationale 保留分歧摘要。簇内同时出现对立 business_progress
    （resolved/invalidated）时丢弃该簇——弱方向校验，避免把立场相反的判断并成共性。
    """

    from uuid import uuid4

    members = list(member_owner_ids)
    # run 级幂等：同 (team, profile, completed_at) 已运行则直接返回既有计数。
    inserted = connection.execute(
        """
        INSERT INTO memory_team_extractions (
            run_id, team_owner_id, profile_id, status, completed_at
        ) VALUES (%s, %s, %s, 'completed', %s)
        ON CONFLICT (team_owner_id, profile_id, completed_at) DO NOTHING
        RETURNING run_id
        """,
        (run_id, team_owner_id, profile_id, effective_at),
    ).fetchone()
    if inserted is None:
        existing = connection.execute(
            """
            SELECT member_count, memory_count, cluster_count, candidate_count
            FROM memory_team_extractions
            WHERE team_owner_id = %s AND profile_id = %s AND completed_at = %s
            """,
            (team_owner_id, profile_id, effective_at),
        ).fetchone()
        if existing is not None:
            return TeamExtractionResult(
                team_owner_id=team_owner_id,
                member_count=existing["member_count"],
                memory_count=existing["memory_count"],
                cluster_count=existing["cluster_count"],
                candidate_count=existing["candidate_count"],
                completed_at=effective_at,
            )
        # 极端情况：冲突但查不到既有行（被并发删除），按零结果返回。
        return TeamExtractionResult(
            team_owner_id=team_owner_id,
            member_count=len(members),
            memory_count=0,
            cluster_count=0,
            candidate_count=0,
            completed_at=effective_at,
        )
    # 查成员的个人 active 记忆（含 embedding）。business_progress 用于弱方向校验。
    rows = connection.execute(
        """
        SELECT i.memory_id, i.owner_id, i.subject, i.memory_type,
               r.revision_id, r.content, r.embedding,
               r.assertion_kind, r.observed_at, r.extraction_confidence,
               r.sensitivity_level, r.valid_from, r.valid_until,
               r.business_progress
        FROM memory_items i
        JOIN memory_revisions r
          ON r.memory_id = i.memory_id AND r.owner_id = i.owner_id AND r.is_current
        WHERE i.owner_id = ANY(%s)
          AND i.profile_id = %s
          AND r.lifecycle_status = 'active'
          AND r.valid_from <= %s
          AND (r.valid_until IS NULL OR r.valid_until > %s)
          AND r.embedding IS NOT NULL
        ORDER BY i.memory_type, i.owner_id, i.memory_id
        """,
        (members, profile_id, effective_at, effective_at),
    ).fetchall()

    memory_count = len(rows)
    if memory_count == 0:
        _update_extraction_run_counts(
            connection,
            run_id,
            member_count=len(members),
            memory_count=0,
            cluster_count=0,
            candidate_count=0,
        )
        return TeamExtractionResult(
            team_owner_id=team_owner_id,
            member_count=len(members),
            memory_count=0,
            cluster_count=0,
            candidate_count=0,
            completed_at=effective_at,
        )

    # 按 memory_type 分组
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["memory_type"], []).append(
            {
                "memory_type": row["memory_type"],
                "memory_id": row["memory_id"],
                "owner_id": row["owner_id"],
                "subject": row["subject"],
                "content": row["content"],
                "embedding": row["embedding"],
                "assertion_kind": row["assertion_kind"],
                "observed_at": row["observed_at"],
                "extraction_confidence": row["extraction_confidence"],
                "sensitivity_level": row["sensitivity_level"],
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "revision_id": row["revision_id"],
                "business_progress": row["business_progress"],
            }
        )

    # 组内 embedding 聚类
    embedding_clusters: list[list[dict]] = []
    for group in groups.values():
        for cluster in _greedy_cluster(group, similarity_threshold):
            embedding_clusters.append(cluster)

    # 簇需同时满足最小尺寸和至少 2 个不同成员，避免单成员回声室产生虚假团队候选。
    # 弱方向校验：簇内同时出现 resolved/invalidated 对立 business_progress 时丢弃，
    # 避免把立场相反的判断（如"风险已解除"与"风险已兑现击穿"）并成同一条团队共性候选。
    valid_embedding_clusters = [
        c
        for c in embedding_clusters
        if len(c) >= min_cluster_size
        and len({m["owner_id"] for m in c}) >= 2
        and not has_conflicting_business_progress(c)
    ]

    cluster_count = len(valid_embedding_clusters)
    candidate_count = 0

    for cluster in valid_embedding_clusters:
        # embedding 簇组内单类型。
        memory_type = cluster[0]["memory_type"]
        # 确定性 subject/content 选择（频次优先 + 字典序兜底），替换非确定性 max(set,...)。
        subject = select_cluster_subject(cluster)
        content = select_cluster_content(cluster)
        # 候选 embedding 取簇内成员均值（簇中心），代表性优于 cluster[0] 原始向量，
        # 且不随成员写新东西/排序变化而漂移，使幂等比对的 embedding 稳定。
        cluster_embedding = average_embedding([m["embedding"] for m in cluster])
        # 幂等：同 subject+type 的 pending 或 confirmed 不重复；并按 embedding 余弦相似度
        # 检测语义重复（距离 < 0.05 视为同义）。扩到 confirmed 防止一条共识被确认后、
        # 成员继续写同样东西时又产出新 pending（确认时才撞 subject 槽位冲突，但垃圾已留）。
        existing = connection.execute(
            """
            SELECT 1 FROM memory_reviews
            WHERE owner_id = %s AND memory_type = %s
              AND status IN ('pending', 'confirmed')
              AND (
                  subject = %s
                  OR (embedding IS NOT NULL
                      AND embedding <=> %s::vector < 0.05)
              )
            """,
            (team_owner_id, memory_type, subject, _embedding_param(cluster_embedding)),
        ).fetchone()
        if existing is not None:
            continue

        review_id = uuid4()
        candidate_id = uuid4()
        now = effective_at
        # confidence = 簇内不同 owner 数 / 成员总数
        unique_owners = len(set(m["owner_id"] for m in cluster))
        confidence = round(unique_owners / len(members), 6)
        # assertion_kind/sensitivity 取众数（频次优先 + 字典序兜底，跨进程可复现）。
        assertion_kind = _cluster_mode_str(cluster, "assertion_kind")
        sensitivity = _cluster_mode_str(cluster, "sensitivity_level")
        valid_from = min(m["valid_from"] for m in cluster)
        valid_until = None
        for m in cluster:
            if m["valid_until"] is not None:
                if valid_until is None or m["valid_until"] < valid_until:
                    valid_until = m["valid_until"]
        base_rationale = f"团队共性提取：{unique_owners} 个成员写了相似内容"
        save_rationale = format_divergence_rationale(
            cluster,
            base=base_rationale,
            subject=subject,
            content=content,
        )

        connection.execute(
            """
            INSERT INTO memory_reviews (
                review_id, candidate_id, capture_id, owner_id, profile_id,
                subject, memory_type, content, assertion_kind,
                business_progress, conversation_id, source_turn_id,
                source_expression, save_rationale, confidence, durability,
                expression_basis, observed_at, candidate_created_at,
                original_time_expression, normalized_time, status,
                created_at, decided_at, source_role, source_message_id,
                source_tool_name, verification_status, sensitivity_level,
                valid_from, valid_until, source_type, embedding
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                NULL, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, NULL, NULL, 'pending',
                %s, NULL, NULL, NULL, NULL, %s, %s,
                %s, %s, %s, %s::vector
            )
            """,
            (
                review_id,
                candidate_id,
                None,
                team_owner_id,
                profile_id,
                subject,
                memory_type,
                content,
                assertion_kind,
                "team-extraction",
                "team-extraction",
                content,
                save_rationale,
                confidence,
                "durable",
                "explicit",
                now,
                now,
                now,
                "user_asserted",
                sensitivity,
                valid_from,
                valid_until,
                "conversation",
                _embedding_param(cluster_embedding),
            ),
        )
        candidate_count += 1

    _update_extraction_run_counts(
        connection,
        run_id,
        member_count=len(members),
        memory_count=memory_count,
        cluster_count=cluster_count,
        candidate_count=candidate_count,
    )
    return TeamExtractionResult(
        team_owner_id=team_owner_id,
        member_count=len(members),
        memory_count=memory_count,
        cluster_count=cluster_count,
        candidate_count=candidate_count,
        completed_at=effective_at,
    )


def _cluster_mode_str(cluster: list[dict], field: str) -> str:
    """取簇内某字符串字段的众数（频次优先 + 字典序兜底），跨进程可复现。

    替换原 ``max(set(...), key=count)`` 的非确定性平局兜底。
    """

    values = [str(m[field]) for m in cluster]
    if not values:
        return ""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return min(counts, key=lambda v: (-counts[v], v))


def _embedding_param(embedding: object) -> object:
    """把 average_embedding 的 tuple 结果适配为 pgvector 文本格式的 SQL 参数。

    embedding 簇候选用 average_embedding 返回的簇中心 tuple，需转 pgvector 文本格式
    绑定。None 时返回 None（memory_reviews.embedding 允许 NULL）。
    """

    if embedding is None:
        return None
    if isinstance(embedding, tuple):
        if not embedding:
            return None
        return "[" + ",".join(repr(float(v)) for v in embedding) + "]"
    return embedding


def _greedy_cluster(
    memories: list[dict],
    threshold: float,
) -> list[list[dict]]:
    """贪心聚类：按 embedding 余弦相似度归簇。"""

    assigned = [False] * len(memories)
    clusters: list[list[dict]] = []
    for i, m in enumerate(memories):
        if assigned[i]:
            continue
        cluster = [m]
        assigned[i] = True
        for j in range(i + 1, len(memories)):
            if assigned[j]:
                continue
            sim = _cosine_similarity(m["embedding"], memories[j]["embedding"])
            if sim >= threshold:
                cluster.append(memories[j])
                assigned[j] = True
        clusters.append(cluster)
    return clusters


def _cosine_similarity(a, b) -> float:
    """计算两个 pgvector 返回值的余弦相似度。"""

    vec_a = _parse_vector(a)
    vec_b = _parse_vector(b)
    if not vec_a or not vec_b:
        return 0.0
    dot = sum(x * y for x, y in zip(vec_a, vec_b, strict=False))
    norm_a = sum(x * x for x in vec_a) ** 0.5
    norm_b = sum(x * x for x in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _parse_vector(value) -> list[float]:
    """将 pgvector 返回的字符串或列表转为 float 列表。"""

    if value is None:
        return []
    if isinstance(value, str):
        parts = value.strip("[]").split(",")
        return [float(p) for p in parts if p.strip()]
    if isinstance(value, (list, tuple)):
        return [float(v) for v in value]
    return []


def _update_extraction_run_counts(
    connection,
    run_id,
    *,
    member_count: int,
    memory_count: int,
    cluster_count: int,
    candidate_count: int,
) -> None:
    """把已插入的 run 行补齐计数（run 行在函数开始时已占位）。"""

    connection.execute(
        """
        UPDATE memory_team_extractions
        SET member_count = %s,
            memory_count = %s,
            cluster_count = %s,
            candidate_count = %s
        WHERE run_id = %s
        """,
        (
            member_count,
            memory_count,
            cluster_count,
            candidate_count,
            run_id,
        ),
    )
