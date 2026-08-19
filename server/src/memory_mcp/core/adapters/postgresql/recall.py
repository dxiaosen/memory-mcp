"""PostgreSQL owner-first 混合召回查询：词法、向量和近期三路。

词法路用 pg_jieba 中文分词全文检索（ts_rank + @@），替代原 pg_trgm 三元组方案——
trgm 对中文短词区分度弱，pg_jieba 词典与项目 jieba 依赖同源，DB 侧分词与服务端
_text_relevance 的 jieba 分词一致。向量路用 pgvector 余弦，近期路按 observed_at 补齐。
"""

from collections.abc import Sequence
from datetime import datetime
from math import ceil
from uuid import UUID

from memory_mcp.core.adapters.postgresql.mapping import (
    as_uuid,
    to_evidence,
    to_recall_candidate,
)
from memory_mcp.core.domain import Evidence, MemoryRecallCandidate, PrincipalContext
from memory_mcp.core.ports import RecallCandidateSet

_RECORD_FIELDS = """
    i.memory_id, i.owner_id, i.profile_id, i.subject, i.memory_type,
    i.created_at AS item_created_at,
    r.revision_id, r.revision_number, r.content, r.assertion_kind,
    r.lifecycle_status, r.business_progress, r.save_rationale,
    r.observed_at AS revision_observed_at,
    r.created_at AS revision_created_at, r.is_current,
    r.original_time_expression, r.normalized_time,
    r.extraction_confidence, r.verification_status, r.sensitivity_level,
    r.valid_from, r.valid_until
"""
_CURRENT_JOIN = """
    FROM memory_items AS i
    JOIN memory_revisions AS r
      ON r.memory_id = i.memory_id
     AND r.owner_id = i.owner_id
     AND r.is_current
"""


def find_recall_candidates(
    connection,
    principal: PrincipalContext,
    *,
    profile_id: str,
    search_text: str,
    subject: str | None,
    effective_at: datetime,
    limit: int,
    query_embedding: Sequence[float] | None = None,
) -> RecallCandidateSet:
    """三路混合召回：词法（40%）、向量（30%）、近期（30%）。

    - 词法路：``pg_jieba`` 中文分词全文检索（ts_rank + @@），抓字面匹配。
    - 向量路：embedding 余弦相似度，抓语义匹配；需要 ``query_embedding``
      非空且记忆有 embedding 列。
    - 近期路：按 ``observed_at`` 补齐剩余配额。

    owner 范围用 ``visible_owner_ids`` 集合过滤，支持团队可见记忆。
    向量路不可用（无 query_embedding 或无 embedding 列）时降级为两路。
    """

    if limit < 1:
        raise ValueError("limit must be positive")
    normalized_search = search_text.strip()
    if not normalized_search:
        raise ValueError("search_text must not be empty")

    has_vector = query_embedding is not None and len(query_embedding) > 0

    if has_vector:
        return _three_way_query(
            connection,
            principal,
            profile_id=profile_id,
            search_text=normalized_search,
            subject=subject,
            effective_at=effective_at,
            limit=limit,
            query_embedding=query_embedding,  # type: ignore[arg-type]
        )
    return _two_way_query(
        connection,
        principal,
        profile_id=profile_id,
        search_text=normalized_search,
        subject=subject,
        effective_at=effective_at,
        limit=limit,
    )


def _three_way_query(
    connection,
    principal: PrincipalContext,
    *,
    profile_id: str,
    search_text: str,
    subject: str | None,
    effective_at: datetime,
    limit: int,
    query_embedding: Sequence[float],
) -> RecallCandidateSet:
    """词法 40% + 向量 30% + 近期 30% 三路查询。"""

    lexical_limit = max(1, ceil(limit * 0.4))
    vector_limit = max(1, ceil(limit * 0.3))
    base_conditions, base_parameters = _base_conditions(
        principal, profile_id, effective_at, subject
    )
    conditions = " AND ".join(base_conditions)

    query = f"""
        WITH lexical AS (
            SELECT {_RECORD_FIELDS},
                   -- ts_rank 无上界，LEAST(*2,1) 归一化到 0-1，保持与原 trgm
                   -- similarity 相同的语义，供服务端 _score_record ×0.15 加成。
                   LEAST(GREATEST(
                       ts_rank(to_tsvector('jiebacfg', i.subject), plainto_tsquery('jiebacfg', %s)),
                       ts_rank(to_tsvector('jiebacfg', r.content), plainto_tsquery('jiebacfg', %s))
                   ) * 2.0, 1.0) AS retrieval_score,
                   'lexical'::text AS retrieval_source
            {_CURRENT_JOIN}
            WHERE {conditions}
              AND (
                  to_tsvector('jiebacfg', i.subject) @@ plainto_tsquery('jiebacfg', %s)
                  OR to_tsvector('jiebacfg', r.content) @@ plainto_tsquery('jiebacfg', %s)
              )
            ORDER BY retrieval_score DESC,
                     r.observed_at DESC,
                     i.memory_id DESC
            LIMIT %s
        ),
        vector AS (
            SELECT {_RECORD_FIELDS},
                   (1 - (r.embedding <=> %s::vector)) AS retrieval_score,
                   'vector'::text AS retrieval_source
            {_CURRENT_JOIN}
            WHERE {conditions}
              AND r.embedding IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM lexical WHERE lexical.memory_id = i.memory_id
              )
            ORDER BY r.embedding <=> %s::vector
            LIMIT %s
        ),
        recent AS (
            SELECT {_RECORD_FIELDS},
                   0.0::real AS retrieval_score,
                   'recent'::text AS retrieval_source
            {_CURRENT_JOIN}
            WHERE {conditions}
              AND NOT EXISTS (
                  SELECT 1 FROM lexical WHERE lexical.memory_id = i.memory_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM vector WHERE vector.memory_id = i.memory_id
              )
            ORDER BY r.observed_at DESC, i.memory_id DESC
            LIMIT GREATEST(%s - (SELECT count(*) FROM lexical) - (SELECT count(*) FROM vector), 0)
        )
        SELECT * FROM lexical
        UNION ALL
        SELECT * FROM vector
        UNION ALL
        SELECT * FROM recent
        ORDER BY retrieval_source ASC,
                 retrieval_score DESC,
                 revision_observed_at DESC,
                 memory_id DESC
    """
    embedding_param = list(query_embedding)
    parameters = [
        search_text,
        search_text,
        *base_parameters,
        search_text,
        search_text,
        lexical_limit,
        # vector 路参数
        embedding_param,
        *base_parameters,
        embedding_param,
        vector_limit,
        # recent 路参数
        *base_parameters,
        limit,
    ]
    rows = connection.execute(query, parameters).fetchall()
    candidates = tuple(to_recall_candidate(row) for row in rows)
    lexical_count = sum(1 for r in rows if r["retrieval_source"] == "lexical")
    vector_count = sum(1 for r in rows if r["retrieval_source"] == "vector")
    return RecallCandidateSet(
        candidates=candidates,
        lexical_count=lexical_count,
        vector_count=vector_count,
        recent_count=len(candidates) - lexical_count - vector_count,
    )


def _two_way_query(
    connection,
    principal: PrincipalContext,
    *,
    profile_id: str,
    search_text: str,
    subject: str | None,
    effective_at: datetime,
    limit: int,
) -> RecallCandidateSet:
    """降级两路查询：词法 + 近期（向量路不可用时使用）。"""

    lexical_limit = 1 if limit == 1 else min(limit - 1, max(1, ceil(limit * 0.7)))
    base_conditions, base_parameters = _base_conditions(
        principal, profile_id, effective_at, subject
    )
    conditions = " AND ".join(base_conditions)
    query = f"""
        WITH lexical AS (
            SELECT {_RECORD_FIELDS},
                   LEAST(GREATEST(
                       ts_rank(to_tsvector('jiebacfg', i.subject), plainto_tsquery('jiebacfg', %s)),
                       ts_rank(to_tsvector('jiebacfg', r.content), plainto_tsquery('jiebacfg', %s))
                   ) * 2.0, 1.0) AS retrieval_score,
                   'lexical'::text AS retrieval_source
            {_CURRENT_JOIN}
            WHERE {conditions}
              AND (
                  to_tsvector('jiebacfg', i.subject) @@ plainto_tsquery('jiebacfg', %s)
                  OR to_tsvector('jiebacfg', r.content) @@ plainto_tsquery('jiebacfg', %s)
              )
            ORDER BY retrieval_score DESC,
                     r.observed_at DESC,
                     i.memory_id DESC
            LIMIT %s
        ),
        recent AS (
            SELECT {_RECORD_FIELDS},
                   0.0::real AS retrieval_score,
                   'recent'::text AS retrieval_source
            {_CURRENT_JOIN}
            WHERE {conditions}
              AND NOT EXISTS (
                  SELECT 1
                  FROM lexical
                  WHERE lexical.memory_id = i.memory_id
              )
            ORDER BY r.observed_at DESC, i.memory_id DESC
            LIMIT GREATEST(%s - (SELECT count(*) FROM lexical), 0)
        )
        SELECT * FROM lexical
        UNION ALL
        SELECT * FROM recent
        ORDER BY retrieval_source ASC,
                 retrieval_score DESC,
                 revision_observed_at DESC,
                 memory_id DESC
    """
    parameters = [
        search_text,
        search_text,
        *base_parameters,
        search_text,
        search_text,
        lexical_limit,
        *base_parameters,
        limit,
    ]
    rows = connection.execute(query, parameters).fetchall()
    candidates = tuple(to_recall_candidate(row) for row in rows)
    lexical_count = sum(1 for r in rows if r["retrieval_source"] == "lexical")
    return RecallCandidateSet(
        candidates=candidates,
        lexical_count=lexical_count,
        vector_count=0,
        recent_count=len(candidates) - lexical_count,
    )


def _base_conditions(
    principal: PrincipalContext,
    profile_id: str,
    effective_at: datetime,
    subject: str | None,
) -> tuple[list[str], list[object]]:
    """构造 owner/profile/active/effective 基础过滤条件。"""

    conditions = [
        "i.owner_id = ANY(%s)",
        "i.profile_id = %s",
        "r.lifecycle_status = 'active'",
        "r.valid_from <= %s",
        "(r.valid_until IS NULL OR r.valid_until > %s)",
    ]
    parameters: list[object] = [
        list(principal.visible_owner_ids),
        profile_id,
        effective_at,
        effective_at,
    ]
    if subject is not None:
        conditions.append(
            "lower(regexp_replace(btrim(i.subject), '\\s+', ' ', 'g')) = "
            "lower(regexp_replace(btrim(%s), '\\s+', ' ', 'g'))"
        )
        parameters.append(subject)
    return conditions, parameters


def load_recall_evidence(
    connection,
    principal: PrincipalContext,
    *,
    revision_ids: tuple[UUID, ...],
    per_revision_limit: int,
) -> dict[UUID, tuple[Evidence, ...]]:
    """一次查询每个 selected revision 最近的有限 Evidence。

    读取用 ``owner_id = ANY(%s)`` 配合 ``visible_owner_ids`` 集合过滤，
    与写入路径用单值 ``owner_id = %s`` 的约定区分。
    """

    if per_revision_limit < 1:
        raise ValueError("per_revision_limit must be positive")
    unique_ids = tuple(dict.fromkeys(revision_ids))
    if not unique_ids:
        return {}
    rows = connection.execute(
        """
        WITH ranked AS (
            SELECT e.evidence_id, e.memory_id, e.revision_id, e.owner_id,
                   e.conversation_id, e.source_turn_id, e.source_expression,
                   e.observed_at, e.created_at, e.source_role,
                   e.source_message_id, e.source_tool_name, e.source_type,
                   d.source_uri, d.source_title, d.source_publisher,
                   d.published_at, d.retrieved_at, d.content_hash,
                   d.citation_locator,
                   row_number() OVER (
                       PARTITION BY e.revision_id
                       ORDER BY e.created_at DESC, e.evidence_id DESC
                   ) AS source_rank
            FROM memory_evidence AS e
            LEFT JOIN memory_evidence_documents AS d
              ON d.evidence_id = e.evidence_id
            WHERE e.owner_id = ANY(%s)
              AND e.revision_id = ANY(%s)
        )
        SELECT evidence_id, memory_id, revision_id, owner_id,
               conversation_id, source_turn_id, source_expression,
               observed_at, created_at, source_role,
               source_message_id, source_tool_name, source_type,
               source_uri, source_title, source_publisher, published_at,
               retrieved_at, content_hash, citation_locator
        FROM ranked
        WHERE source_rank <= %s
        ORDER BY revision_id, created_at, evidence_id
        """,
        (list(principal.visible_owner_ids), list(unique_ids), per_revision_limit),
    ).fetchall()
    grouped: dict[UUID, list[Evidence]] = {}
    for row in rows:
        grouped.setdefault(as_uuid(row["revision_id"]), []).append(to_evidence(row))
    return {revision_id: tuple(sources) for revision_id, sources in grouped.items()}


def find_recall_candidates_by_ids(
    connection,
    principal: PrincipalContext,
    *,
    memory_ids: Sequence[UUID],
    effective_at: datetime,
) -> tuple[MemoryRecallCandidate, ...]:
    """按 memory_id 集合加载可见的当前活动候选（关系感知召回补漏用）。

    retrieval_score 固定为 0，由 RecallService 按关系加成提升。
    """

    if not memory_ids:
        return ()
    owner_ids = list(principal.visible_owner_ids)
    rows = connection.execute(
        f"""
        SELECT {_RECORD_FIELDS}
        {_CURRENT_JOIN}
        WHERE i.owner_id = ANY(%s)
          AND i.memory_id = ANY(%s)
          AND r.lifecycle_status = 'active'
          AND r.valid_from <= %s
          AND (r.valid_until IS NULL OR r.valid_until > %s)
        """,
        (owner_ids, [str(mid) for mid in memory_ids], effective_at, effective_at),
    ).fetchall()
    return tuple(to_recall_candidate(row) for row in rows)
