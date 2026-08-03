"""PostgreSQL 持久化边界的领域写入校验。"""

from memory_mcp.core.domain import (
    Candidate,
    CaptureStatus,
    EvidenceDocument,
    LifecycleStatus,
    MemoryRecord,
    PrincipalContext,
    RelationOrigin,
    RelationScope,
    RelationStatus,
    ReviewItem,
    ReviewStatus,
)
from memory_mcp.core.ports import CaptureWrite


def _evidence_document_mismatch(
    document: EvidenceDocument | None,
    candidate: Candidate,
) -> bool:
    """比较 evidence 的 document 子对象与 candidate 的内联文档字段。"""

    if document is None:
        return any(
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
    return (
        document.source_uri != candidate.source_uri
        or document.source_title != candidate.source_title
        or document.source_publisher != candidate.source_publisher
        or document.published_at != candidate.published_at
        or document.retrieved_at != candidate.retrieved_at
        or document.content_hash != candidate.content_hash
        or document.citation_locator != candidate.citation_locator
    )


def validate_capture_write(
    principal: PrincipalContext,
    write: CaptureWrite,
) -> None:
    """写入前校验：owner 归属、幂等结构、引用完整性等不变量。"""

    result = write.result
    if result.owner_id != principal.owner_id:
        raise ValueError("capture owner must match trusted principal")
    if result.status is not CaptureStatus.COMPLETED and (
        write.memories
        or write.reviews
        or write.duplicate_evidence
        or write.replacements
        or write.relations
    ):
        raise ValueError("failed capture cannot persist candidate content")
    memory_ids = {record.item.memory_id for record in write.memories}
    lifecycle_ids = {
        operation.memory_id
        for operation in (*write.duplicate_evidence, *write.replacements)
    }
    review_ids = {review.review_id for review in write.reviews}
    relation_ids = {relation.relation_id for relation in write.relations}
    if len(memory_ids) != len(write.memories):
        raise ValueError("capture contains duplicate memory ids")
    if len(review_ids) != len(write.reviews):
        raise ValueError("capture contains duplicate review ids")
    if len(relation_ids) != len(write.relations):
        raise ValueError("capture contains duplicate relation ids")
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
    for relation in write.relations:
        provenance = relation.provenance
        if relation.owner_id != principal.owner_id:
            raise ValueError("relation owner must match trusted principal")
        if (
            relation.status is not RelationStatus.ACTIVE
            or relation.revoked_at is not None
            or relation.stale_at is not None
            or relation.origin is not RelationOrigin.AUTOMATIC
            or relation.scope is not RelationScope.REVISION
            or provenance is None
            or provenance.capture_id != result.capture_id
            or provenance.conversation_id != result.conversation_id
            or provenance.source_turn_id != result.source_turn_id
        ):
            raise ValueError(
                "new capture relation must be active automatic revision provenance"
            )
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
    """确认 memory 的内容、evidence 与待审 candidate 完全一致。"""

    candidate = review.candidate
    item = memory.item
    revision = memory.current_revision
    # owner_id 允许不同：团队提升时 memory 写入团队 owner，
    # candidate 仍是个人 owner。其他字段必须一致。
    if (
        item.profile_id != candidate.profile_id
        or item.subject != candidate.subject
        or item.memory_type != candidate.memory_type
        or revision.content != candidate.content
        or revision.assertion_kind is not candidate.assertion_kind
        or revision.business_progress != candidate.business_progress
        or revision.save_rationale != candidate.save_rationale
        or revision.observed_at != candidate.observed_at
        or revision.extraction_confidence != candidate.confidence
        or revision.verification_status.value != "user_confirmed"
        or revision.sensitivity_level is not candidate.sensitivity_level
        or revision.valid_from != candidate.valid_from
        or revision.valid_until != candidate.valid_until
        or revision.original_time_expression != candidate.original_time_expression
        or revision.normalized_time != candidate.normalized_time
    ):
        raise ValueError("confirmed memory must match pending candidate")
    if len(memory.evidence) != 1:
        raise ValueError("confirmed memory requires one source evidence")
    source = memory.evidence[0]
    # evidence owner 允许与 candidate owner 不同（团队提升）。
    if (
        source.conversation_id != candidate.conversation_id
        or source.source_turn_id != candidate.source_turn_id
        or source.source_expression != candidate.source_expression
        or source.observed_at != candidate.observed_at
        or source.source_role is not candidate.source_role
        or source.source_message_id != candidate.source_message_id
        or source.source_tool_name != candidate.source_tool_name
        or source.source_type is not candidate.source_type
        or _evidence_document_mismatch(source.document, candidate)
    ):
        raise ValueError("confirmed memory source must match pending candidate")
