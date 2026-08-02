"""PostgreSQL owner-first 混合召回查询。"""

from datetime import datetime
from math import ceil

from memory_mcp.core.adapters.postgresql.mapping import to_record
from memory_mcp.core.domain import PrincipalContext
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
    r.valid_from, r.valid_until, r.last_verified_at
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
        "i.owner_id = %s",
        "i.profile_id = %s",
        "r.lifecycle_status = 'active'",
        "r.valid_from <= %s",
        "(r.valid_until IS NULL OR r.valid_until > %s)",
    ]
    base_parameters: list[object] = [
        principal.owner_id,
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
    records = tuple(to_record(connection, row, principal.owner_id) for row in rows)
    lexical_count = sum(1 for row in rows if row["retrieval_source"] == "lexical")
    return RecallCandidateSet(
        records=records,
        lexical_count=lexical_count,
        recent_count=len(records) - lexical_count,
    )
