"""PostgreSQL 有界生命周期维护事务。"""

from datetime import datetime
from math import ceil

from memory_mcp.core.domain import MaintenanceResult


def run_maintenance(
    connection,
    *,
    effective_at: datetime,
    review_cutoff: datetime,
    limit: int,
) -> MaintenanceResult:
    """物化一批到期 revision/review 和依赖关系终态。

    revision 和 review 各占 ``limit`` 的一半配额，用
    ``FOR UPDATE SKIP LOCKED`` 避免并发维护事务争抢同一行。
    revision 过期后，引用它的 active relation 同步置为 stale。
    """

    if limit < 1:
        raise ValueError("limit must be positive")
    memory_limit = ceil(limit / 2)
    review_limit = limit - memory_limit
    row = connection.execute(
        """
        WITH revision_targets AS (
            SELECT revision_id, memory_id, owner_id
            FROM memory_revisions
            WHERE is_current
              AND lifecycle_status = 'active'
              AND valid_until IS NOT NULL
              AND valid_until <= %s
            ORDER BY valid_until, revision_id
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        ),
        expired_revisions AS (
            UPDATE memory_revisions AS revision
            SET lifecycle_status = 'expired'
            FROM revision_targets AS target
            WHERE revision.revision_id = target.revision_id
              AND revision.memory_id = target.memory_id
              AND revision.owner_id = target.owner_id
              AND revision.is_current
              AND revision.lifecycle_status = 'active'
            RETURNING revision.revision_id,
                      revision.memory_id,
                      revision.owner_id
        ),
        expired_items AS (
            UPDATE memory_items AS item
            SET lifecycle_status = 'expired'
            FROM expired_revisions AS expired
            WHERE item.memory_id = expired.memory_id
              AND item.owner_id = expired.owner_id
              AND item.lifecycle_status = 'active'
        ),
        stale_relations AS (
            UPDATE memory_relations AS relation
            SET status = 'stale',
                stale_at = %s,
                stale_reason = 'endpoint_expired'
            WHERE relation.status = 'active'
              AND relation.created_at <= %s
              AND EXISTS (
                  SELECT 1
                  FROM expired_revisions AS expired
                  WHERE expired.owner_id = relation.owner_id
                    AND expired.memory_id IN (
                        relation.source_memory_id,
                        relation.target_memory_id
                    )
              )
            RETURNING relation.relation_id
        ),
        review_targets AS (
            SELECT review_id, owner_id
            FROM memory_reviews
            WHERE status = 'pending'
              AND (
                  (valid_until IS NOT NULL AND valid_until <= %s)
                  OR created_at <= %s
              )
            ORDER BY COALESCE(valid_until, created_at), review_id
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        ),
        expired_reviews AS (
            UPDATE memory_reviews AS review
            SET status = 'expired', decided_at = %s
            FROM review_targets AS target
            WHERE review.review_id = target.review_id
              AND review.owner_id = target.owner_id
              AND review.status = 'pending'
            RETURNING review.review_id
        )
        SELECT
            (SELECT count(*) FROM expired_revisions) AS expired_memory_count,
            (SELECT count(*) FROM expired_reviews) AS expired_review_count,
            (SELECT count(*) FROM stale_relations) AS stale_relation_count
        """,
        (
            effective_at,
            memory_limit,
            effective_at,
            effective_at,
            effective_at,
            review_cutoff,
            review_limit,
            effective_at,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("maintenance query did not return counts")
    expired_memory_count = int(row["expired_memory_count"])
    expired_review_count = int(row["expired_review_count"])
    return MaintenanceResult(
        effective_at=effective_at,
        expired_memory_count=expired_memory_count,
        expired_review_count=expired_review_count,
        stale_relation_count=int(row["stale_relation_count"]),
        has_more=(
            expired_memory_count == memory_limit
            or (review_limit > 0 and expired_review_count == review_limit)
        ),
    )
