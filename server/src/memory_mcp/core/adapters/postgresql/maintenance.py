"""PostgreSQL 有界生命周期维护事务。"""

from datetime import datetime
from math import ceil
from uuid import UUID

from memory_mcp.core.domain import ExpiredRelationContext, MaintenanceResult


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
    revision 过期后，引用它的 active relation 同步置为 stale，并返回失效关系
    的双端上下文（``expired_relation_contexts``），供维护服务派生提醒记忆。
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
            RETURNING relation.relation_id,
                      relation.owner_id,
                      relation.profile_id,
                      relation.relation_type,
                      relation.source_memory_id,
                      relation.target_memory_id
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
            (SELECT count(*) FROM stale_relations) AS stale_relation_count,
            COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'relation_id', sr.relation_id,
                        'owner_id', sr.owner_id,
                        'profile_id', sr.profile_id,
                        'relation_type', sr.relation_type,
                        'source_memory_id', sr.source_memory_id,
                        'target_memory_id', sr.target_memory_id,
                        'source_subject', si.subject,
                        'source_memory_type', si.memory_type,
                        'target_subject', ti.subject,
                        'target_memory_type', ti.memory_type,
                        'expired_memory_id',
                            CASE
                                WHEN EXISTS (
                                    SELECT 1 FROM expired_revisions er
                                    WHERE er.owner_id = sr.owner_id
                                      AND er.memory_id = sr.source_memory_id
                                ) THEN sr.source_memory_id
                                ELSE sr.target_memory_id
                            END
                    )
                ),
                '[]'::jsonb
            ) AS stale_relation_contexts
        FROM stale_relations AS sr
        LEFT JOIN memory_items AS si
          ON si.memory_id = sr.source_memory_id
         AND si.owner_id = sr.owner_id
        LEFT JOIN memory_items AS ti
          ON ti.memory_id = sr.target_memory_id
         AND ti.owner_id = sr.owner_id
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
    contexts = _build_relation_contexts(row)
    return MaintenanceResult(
        effective_at=effective_at,
        expired_memory_count=expired_memory_count,
        expired_review_count=expired_review_count,
        stale_relation_count=int(row["stale_relation_count"]),
        has_more=(
            expired_memory_count == memory_limit
            or (review_limit > 0 and expired_review_count == review_limit)
        ),
        expired_relation_contexts=contexts,
    )


def _build_relation_contexts(row) -> tuple[ExpiredRelationContext, ...]:
    """把 stale_relations 的 jsonb 聚合结果转为 ExpiredRelationContext 元组。

    每条失效关系的 ``expired_memory_id`` 由 SQL 侧依据 ``expired_revisions``
    确定（source 过期则取 source，否则取 target），因此此处不依赖任何记忆
    类型名称字面量来判定哪端过期；另一端即为 focus（需复核的论点等）。
    """

    import json

    raw = row["stale_relation_contexts"]
    if raw is None:
        return ()
    entries = raw if isinstance(raw, list) else json.loads(raw)
    contexts: list[ExpiredRelationContext] = []
    for entry in entries:
        relation_type = entry["relation_type"]
        owner_id = entry["owner_id"]
        profile_id = entry["profile_id"]
        source_id = UUID(entry["source_memory_id"])
        target_id = UUID(entry["target_memory_id"])
        source_subject = entry["source_subject"]
        source_memory_type = entry["source_memory_type"]
        target_subject = entry["target_subject"]
        target_memory_type = entry["target_memory_type"]
        expired_id = UUID(entry["expired_memory_id"])
        if expired_id == source_id:
            expired_subject, expired_memory_type = source_subject, source_memory_type
            focus_id, focus_subject, focus_memory_type = (
                target_id,
                target_subject,
                target_memory_type,
            )
        else:
            expired_subject, expired_memory_type = target_subject, target_memory_type
            focus_id, focus_subject, focus_memory_type = (
                source_id,
                source_subject,
                source_memory_type,
            )
        contexts.append(
            ExpiredRelationContext(
                owner_id=owner_id,
                profile_id=profile_id,
                relation_type=relation_type,
                expired_memory_id=expired_id,
                expired_subject=expired_subject,
                expired_memory_type=expired_memory_type,
                focus_memory_id=focus_id,
                focus_subject=focus_subject,
                focus_memory_type=focus_memory_type,
            )
        )
    return tuple(contexts)
