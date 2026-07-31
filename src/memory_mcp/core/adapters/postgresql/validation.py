"""PostgreSQL 持久化边界的领域写入校验。"""

from memory_mcp.core.domain import (
    CaptureStatus,
    LifecycleStatus,
    MemoryRecord,
    PrincipalContext,
    ReviewItem,
    ReviewStatus,
)
from memory_mcp.core.ports import CaptureWrite


def validate_capture_write(
    principal: PrincipalContext,
    write: CaptureWrite,
) -> None:
    result = write.result
    if result.owner_id != principal.owner_id:
        raise ValueError("capture owner must match trusted principal")
    if result.status is not CaptureStatus.COMPLETED and (
        write.memories
        or write.reviews
        or write.duplicate_evidence
        or write.replacements
    ):
        raise ValueError("failed capture cannot persist candidate content")
    memory_ids = {record.item.memory_id for record in write.memories}
    lifecycle_ids = {
        operation.memory_id
        for operation in (*write.duplicate_evidence, *write.replacements)
    }
    review_ids = {review.review_id for review in write.reviews}
    if len(memory_ids) != len(write.memories):
        raise ValueError("capture contains duplicate memory ids")
    if len(review_ids) != len(write.reviews):
        raise ValueError("capture contains duplicate review ids")
    if len(lifecycle_ids) != (len(write.duplicate_evidence) + len(write.replacements)):
        raise ValueError("capture contains conflicting lifecycle writes")
    if memory_ids & lifecycle_ids:
        raise ValueError("new memories cannot also be lifecycle targets")
    for record in write.memories:
        if record.item.owner_id != principal.owner_id:
            raise ValueError("record owner must match trusted principal")
    for review in write.reviews:
        if review.owner_id != principal.owner_id:
            raise ValueError("review owner must match trusted principal")
        if review.status is not ReviewStatus.PENDING:
            raise ValueError("new review must be pending")
    for duplicate in write.duplicate_evidence:
        if (
            duplicate.evidence.owner_id != principal.owner_id
            or duplicate.evidence.memory_id != duplicate.memory_id
            or duplicate.evidence.revision_id != duplicate.expected_revision_id
        ):
            raise ValueError("duplicate evidence must match lifecycle target")
    for replacement in write.replacements:
        revision = replacement.revision
        if (
            revision.owner_id != principal.owner_id
            or revision.memory_id != replacement.memory_id
            or not revision.is_current
            or revision.lifecycle_status is not LifecycleStatus.ACTIVE
            or not replacement.evidence
            or any(
                source.owner_id != principal.owner_id
                or source.memory_id != replacement.memory_id
                or source.revision_id != revision.revision_id
                for source in replacement.evidence
            )
        ):
            raise ValueError("replacement write is invalid")
    for outcome in result.outcomes:
        if (
            outcome.memory_id is not None
            and outcome.memory_id not in memory_ids | lifecycle_ids
        ):
            raise ValueError("capture outcome references unknown memory")
        if outcome.review_id is not None and outcome.review_id not in review_ids:
            raise ValueError("capture outcome references unknown review")


def validate_review_memory(
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
