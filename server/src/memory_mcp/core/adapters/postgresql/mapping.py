"""PostgreSQL 数据行到领域对象的纯映射函数。"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
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
    EvidenceSourceType,
    ExpressionBasis,
    ExtractionMetadata,
    LifecycleStatus,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    MessageRole,
    ReviewItem,
    ReviewStatus,
    SensitivityLevel,
    VerificationStatus,
)


def to_capture_result(connection, row: Mapping[str, Any]) -> CaptureResult:
    outcomes = tuple(
        CaptureOutcome(
            candidate_id=as_uuid(outcome["candidate_id"]),
            decision=AdmissionDecision(outcome["decision"]),
            reason_code=outcome["reason_code"],
            memory_id=optional_uuid(outcome["memory_id"]),
            review_id=optional_uuid(outcome["review_id"]),
        )
        for outcome in connection.execute(
            """
            SELECT candidate_id, decision, reason_code, memory_id, review_id
            FROM memory_capture_outcomes
            WHERE capture_id = %s AND owner_id = %s
            ORDER BY outcome_order
            """,
            (row["capture_id"], row["owner_id"]),
        ).fetchall()
    )
    return CaptureResult(
        capture_id=as_uuid(row["capture_id"]),
        owner_id=row["owner_id"],
        profile_id=row["profile_id"],
        conversation_id=row["conversation_id"],
        source_turn_id=row["source_turn_id"],
        metadata=ExtractionMetadata(
            model_id=row["model_id"],
            prompt_version=row["prompt_version"],
            schema_version=row["schema_version"],
            profile_version=row["profile_version"],
            profile_fingerprint=row["profile_fingerprint"],
        ),
        status=CaptureStatus(row["status"]),
        outcomes=outcomes,
        failure_code=row["failure_code"],
        created_at=as_datetime(row["created_at"]),
        completed_at=as_datetime(row["completed_at"]),
        event_id=row["event_id"],
        contract_version=row["contract_version"],
        payload_fingerprint=row["payload_fingerprint"],
    )


def to_review(row: Mapping[str, Any]) -> ReviewItem:
    candidate = Candidate(
        candidate_id=as_uuid(row["candidate_id"]),
        owner_id=row["owner_id"],
        profile_id=row["profile_id"],
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
        observed_at=as_datetime(row["observed_at"]),
        created_at=as_datetime(row["candidate_created_at"]),
        verification_status=VerificationStatus(row["verification_status"]),
        sensitivity_level=SensitivityLevel(row["sensitivity_level"]),
        valid_from=as_datetime(row["valid_from"]),
        valid_until=optional_datetime(row["valid_until"]),
        last_verified_at=optional_datetime(row["last_verified_at"]),
        business_progress=row["business_progress"],
        original_time_expression=row["original_time_expression"],
        normalized_time=optional_datetime(row["normalized_time"]),
        source_role=(
            MessageRole(row["source_role"]) if row["source_role"] is not None else None
        ),
        source_message_id=row["source_message_id"],
        source_tool_name=row["source_tool_name"],
        source_type=EvidenceSourceType(row["source_type"]),
        source_uri=row["source_uri"],
        source_title=row["source_title"],
        source_publisher=row["source_publisher"],
        published_at=optional_datetime(row["published_at"]),
        retrieved_at=optional_datetime(row["retrieved_at"]),
        content_hash=row["content_hash"],
        citation_locator=row["citation_locator"],
    )
    return ReviewItem(
        review_id=as_uuid(row["review_id"]),
        candidate=candidate,
        status=ReviewStatus(row["status"]),
        created_at=as_datetime(row["created_at"]),
        decided_at=optional_datetime(row["decided_at"]),
        resolved_memory_id=optional_uuid(row["resolved_memory_id"]),
    )


def to_record(
    connection,
    row: Mapping[str, Any],
    owner_id: str,
) -> MemoryRecord:
    item = MemoryItem(
        memory_id=as_uuid(row["memory_id"]),
        owner_id=row["owner_id"],
        profile_id=row["profile_id"],
        subject=row["subject"],
        memory_type=row["memory_type"],
        created_at=as_datetime(row["item_created_at"]),
    )
    revision = to_revision(row)
    return MemoryRecord(
        item=item,
        current_revision=revision,
        evidence=load_evidence(connection, owner_id, revision.revision_id),
    )


def to_revision(row: Mapping[str, Any]) -> MemoryRevision:
    observed_at = row.get("revision_observed_at", row.get("observed_at"))
    created_at = row.get("revision_created_at", row.get("created_at"))
    return MemoryRevision(
        revision_id=as_uuid(row["revision_id"]),
        memory_id=as_uuid(row["memory_id"]),
        owner_id=row["owner_id"],
        revision_number=row["revision_number"],
        content=row["content"],
        assertion_kind=AssertionKind(row["assertion_kind"]),
        lifecycle_status=LifecycleStatus(row["lifecycle_status"]),
        business_progress=row["business_progress"],
        save_rationale=row["save_rationale"],
        observed_at=as_datetime(observed_at),
        created_at=as_datetime(created_at),
        extraction_confidence=row["extraction_confidence"],
        verification_status=VerificationStatus(row["verification_status"]),
        sensitivity_level=SensitivityLevel(row["sensitivity_level"]),
        valid_from=as_datetime(row["valid_from"]),
        valid_until=optional_datetime(row["valid_until"]),
        last_verified_at=optional_datetime(row["last_verified_at"]),
        is_current=row["is_current"],
        original_time_expression=row["original_time_expression"],
        normalized_time=optional_datetime(row["normalized_time"]),
    )


def load_evidence(
    connection,
    owner_id: str,
    revision_id: UUID,
) -> tuple[Evidence, ...]:
    return tuple(
        Evidence(
            evidence_id=as_uuid(source["evidence_id"]),
            memory_id=as_uuid(source["memory_id"]),
            revision_id=as_uuid(source["revision_id"]),
            owner_id=source["owner_id"],
            conversation_id=source["conversation_id"],
            source_turn_id=source["source_turn_id"],
            source_expression=source["source_expression"],
            observed_at=as_datetime(source["observed_at"]),
            created_at=as_datetime(source["created_at"]),
            source_role=(
                MessageRole(source["source_role"])
                if source["source_role"] is not None
                else None
            ),
            source_message_id=source["source_message_id"],
            source_tool_name=source["source_tool_name"],
            source_type=EvidenceSourceType(source["source_type"]),
            source_uri=source["source_uri"],
            source_title=source["source_title"],
            source_publisher=source["source_publisher"],
            published_at=optional_datetime(source["published_at"]),
            retrieved_at=optional_datetime(source["retrieved_at"]),
            content_hash=source["content_hash"],
            citation_locator=source["citation_locator"],
        )
        for source in connection.execute(
            """
            SELECT evidence_id, memory_id, revision_id, owner_id,
                   conversation_id, source_turn_id, source_expression,
                   observed_at, created_at, source_role,
                   source_message_id, source_tool_name, source_type,
                   source_uri, source_title, source_publisher, published_at,
                   retrieved_at, content_hash, citation_locator
            FROM memory_evidence
            WHERE owner_id = %s AND revision_id = %s
            ORDER BY created_at, evidence_id
            """,
            (owner_id, revision_id),
        ).fetchall()
    )


def as_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(value)


def optional_uuid(value: UUID | str | None) -> UUID | None:
    return None if value is None else as_uuid(value)


def as_datetime(value: datetime | str) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def optional_datetime(value: datetime | str | None) -> datetime | None:
    return None if value is None else as_datetime(value)
