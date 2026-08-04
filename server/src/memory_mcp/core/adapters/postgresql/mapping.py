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
    EvidenceDocument,
    EvidenceSourceType,
    ExpressionBasis,
    ExtractionMetadata,
    LifecycleStatus,
    MemoryItem,
    MemoryRecallCandidate,
    MemoryRecord,
    MemoryRevision,
    MessageRole,
    ReviewItem,
    ReviewStatus,
    SensitivityLevel,
    VerificationStatus,
)


def to_capture_result(connection, row: Mapping[str, Any]) -> CaptureResult:
    """从 capture 行和关联 outcome 行组装完整 CaptureResult。"""

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
    """把 review 行映射为 ReviewItem，含 candidate 和可选文档来源。"""

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
        business_progress=row["business_progress"],
        original_time_expression=row["original_time_expression"],
        normalized_time=optional_datetime(row["normalized_time"]),
        source_role=(
            MessageRole(row["source_role"]) if row["source_role"] is not None else None
        ),
        source_message_id=row["source_message_id"],
        source_tool_name=row["source_tool_name"],
        source_type=EvidenceSourceType(row["source_type"]),
        # doc_* 来自 LEFT JOIN 子表；FOR UPDATE 查询（_SELECT_REVIEW_FOR_UPDATE）
        # 不含 JOIN，此时这些 key 缺失，.get() 返回 None——对话来源本来就没有文档字段。
        source_uri=row.get("doc_source_uri"),
        source_title=row.get("doc_source_title"),
        source_publisher=row.get("doc_source_publisher"),
        published_at=optional_datetime(row.get("doc_published_at")),
        retrieved_at=optional_datetime(row.get("doc_retrieved_at")),
        content_hash=row.get("doc_content_hash"),
        citation_locator=row.get("doc_citation_locator"),
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
    """从当前 revision 行组装 MemoryRecord，并加载其 Evidence。"""

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


def to_recall_candidate(row: Mapping[str, Any]) -> MemoryRecallCandidate:
    """将无 Evidence 的召回行映射为轻量排序候选。"""

    return MemoryRecallCandidate(
        item=MemoryItem(
            memory_id=as_uuid(row["memory_id"]),
            owner_id=row["owner_id"],
            profile_id=row["profile_id"],
            subject=row["subject"],
            memory_type=row["memory_type"],
            created_at=as_datetime(row["item_created_at"]),
        ),
        current_revision=to_revision(row),
        retrieval_score=float(row.get("retrieval_score", 0.0) or 0.0),
    )


def to_revision(row: Mapping[str, Any]) -> MemoryRevision:
    """映射 revision 行，兼容带前缀和不带前缀的列别名。"""

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
        is_current=row["is_current"],
        original_time_expression=row["original_time_expression"],
        normalized_time=optional_datetime(row["normalized_time"]),
        embedding=_to_embedding(row.get("embedding")),
    )


def load_evidence(
    connection,
    owner_id: str,
    revision_id: UUID,
) -> tuple[Evidence, ...]:
    """按 owner + revision 查询并映射全部 Evidence（含可选文档子表）。"""

    return tuple(
        to_evidence(source)
        for source in connection.execute(
            """
            SELECT e.evidence_id, e.memory_id, e.revision_id, e.owner_id,
                   e.conversation_id, e.source_turn_id, e.source_expression,
                   e.observed_at, e.created_at, e.source_role,
                   e.source_message_id, e.source_tool_name, e.source_type,
                   d.source_uri, d.source_title, d.source_publisher,
                   d.published_at, d.retrieved_at, d.content_hash,
                   d.citation_locator
            FROM memory_evidence AS e
            LEFT JOIN memory_evidence_documents AS d
              ON d.evidence_id = e.evidence_id
            WHERE e.owner_id = %s AND e.revision_id = %s
            ORDER BY e.created_at, e.evidence_id
            """,
            (owner_id, revision_id),
        ).fetchall()
    )


def to_evidence(source: Mapping[str, Any]) -> Evidence:
    """映射一条已经过 owner 条件过滤的 Evidence。"""

    document = _to_evidence_document(source)
    return Evidence(
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
        document=document,
    )


def _to_evidence_document(source: Mapping[str, Any]) -> EvidenceDocument | None:
    """从 JOIN 结果构造文档元数据；无文档字段时返回 None。"""

    has_document = any(
        source.get(key) is not None
        for key in (
            "source_uri",
            "source_title",
            "source_publisher",
            "published_at",
            "retrieved_at",
            "content_hash",
            "citation_locator",
        )
    )
    if not has_document:
        return None
    return EvidenceDocument(
        source_uri=source.get("source_uri"),
        source_title=source.get("source_title"),
        source_publisher=source.get("source_publisher"),
        published_at=optional_datetime(source.get("published_at")),
        retrieved_at=optional_datetime(source.get("retrieved_at")),
        content_hash=source.get("content_hash"),
        citation_locator=source.get("citation_locator"),
    )


def as_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(value)


def optional_uuid(value: UUID | str | None) -> UUID | None:
    return None if value is None else as_uuid(value)


def as_datetime(value: datetime | str) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def optional_datetime(value: datetime | str | None) -> datetime | None:
    return None if value is None else as_datetime(value)


def _to_embedding(value: object) -> tuple[float, ...] | None:
    """将 pgvector 返回的字符串或列表转为 float 元组。"""

    if value is None:
        return None
    if isinstance(value, str):
        parts = value.strip("[]").split(",")
        return tuple(float(p) for p in parts) if parts else None
    if isinstance(value, (list, tuple)):
        return tuple(float(v) for v in value)
    return None
