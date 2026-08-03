"""PostgreSQL owner-first 混合召回查询。"""

from datetime import datetime
from math import ceil
from uuid import UUID

from memory_mcp.core.adapters.postgresql.mapping import (
    as_uuid,
    to_evidence,
    to_recall_candidate,
)
from memory_mcp.core.domain import Evidence, PrincipalContext
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
) -> RecallCandidateSet:
    """用索引词法候选优先，并从近期记录补满剩余容量。"""

    if limit < 1:
        raise ValueError("limit must be positive")
    normalized_search = search_text.strip()
    if not normalized_search:
        raise ValueError("search_text must not be empty")
    lexical_limit = 1 if limit == 1 else min(limit - 1, max(1, ceil(limit * 0.7)))
    base_conditions = [
        "i.owner_id = ANY(%s)",
        "i.profile_id = %s",
        "r.lifecycle_status = 'active'",
        "r.valid_from <= %s",
        "(r.valid_until IS NULL OR r.valid_until > %s)",
    ]
    base_parameters: list[object] = [
        list(principal.visible_owner_ids),
        profile_id,
        effective_at,
        effective_at,
    ]
    if subject is not None:
        base_conditions.append(
            "lower(regexp_replace(btrim(i.subject), '\\s+', ' ', 'g')) = "
            "lower(regexp_replace(btrim(%s), '\\s+', ' ', 'g'))"
        )
        base_parameters.append(subject)
    conditions = " AND ".join(base_conditions)
    query = f"""
        WITH lexical AS (
            SELECT {_RECORD_FIELDS},
                   GREATEST(
                       similarity(lower(i.subject), lower(%s)),
                       similarity(lower(r.content), lower(%s))
                   ) AS retrieval_score,
                   'lexical'::text AS retrieval_source
            {_CURRENT_JOIN}
            WHERE {conditions}
              AND (
                  lower(i.subject) %% lower(%s)
                  OR lower(r.content) %% lower(%s)
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
        normalized_search,
        normalized_search,
        *base_parameters,
        normalized_search,
        normalized_search,
        lexical_limit,
        *base_parameters,
        limit,
    ]
    connection.execute("SET LOCAL pg_trgm.similarity_threshold = 0.08")
    rows = connection.execute(query, parameters).fetchall()
    candidates = tuple(to_recall_candidate(row) for row in rows)
    lexical_count = sum(1 for row in rows if row["retrieval_source"] == "lexical")
    return RecallCandidateSet(
        candidates=candidates,
        lexical_count=lexical_count,
        recent_count=len(candidates) - lexical_count,
    )


def load_recall_evidence(
    connection,
    principal: PrincipalContext,
    *,
    revision_ids: tuple[UUID, ...],
    per_revision_limit: int,
) -> dict[UUID, tuple[Evidence, ...]]:
    """一次查询每个 selected revision 最近的有限 Evidence。"""

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
