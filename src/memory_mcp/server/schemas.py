"""Memory MCP 工具输入与结果使用的严格版本化 DTO。"""

import base64
import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from memory_mcp.core import (
    AdmissionDecision,
    CaptureResult,
    MemoryHistoryEntry,
    MemoryRecord,
    MessageRole,
    RecallResult,
    ReviewItem,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.server.errors import ErrorCode


class StrictDto(BaseModel):
    model_config = ConfigDict(extra="forbid")


NonEmptyText = Annotated[str, Field(min_length=1)]


class RoleMessageV1(StrictDto):
    role: Literal["user", "assistant", "tool"]
    content: NonEmptyText
    message_id: str | None = Field(default=None, min_length=1)
    tool_name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_tool_name(self) -> Self:
        if self.tool_name is not None and self.role != "tool":
            raise ValueError("tool_name is only valid for tool messages")
        return self


class CompletedTurnEventV1(StrictDto):
    contract_version: NonEmptyText
    event_id: NonEmptyText
    scenario: NonEmptyText
    conversation_id: NonEmptyText
    turn_id: NonEmptyText
    observed_at: datetime
    messages: Annotated[tuple[RoleMessageV1, ...], Field(min_length=1, max_length=64)]
    subject_hint: str | None = Field(default=None, min_length=1)

    @field_validator("observed_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    def payload_fingerprint(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_turn_envelope(self, *, max_characters: int) -> TurnEnvelope:
        content = "\n\n".join(
            f"[{message.role}]\n{message.content}" for message in self.messages
        )
        if len(content) > max_characters:
            raise ValueError("completed turn exceeds configured capture size")
        return TurnEnvelope(
            scenario=self.scenario,
            conversation_id=self.conversation_id,
            source_turn_id=self.turn_id,
            content=content,
            observed_at=self.observed_at,
            subject_hint=self.subject_hint,
            event_id=self.event_id,
            contract_version=self.contract_version,
            payload_fingerprint=self.payload_fingerprint(),
            messages=tuple(
                TurnMessage(
                    role=MessageRole(message.role),
                    content=message.content,
                    message_id=message.message_id,
                    tool_name=message.tool_name,
                )
                for message in self.messages
            ),
        )


class ErrorResponse(StrictDto):
    ok: Literal[False] = False
    request_id: str
    error_code: ErrorCode
    message: str
    retryable: bool = False


class CaptureSummary(StrictDto):
    auto_saved_count: int = 0
    pending_count: int = 0
    discarded_count: int = 0
    blocked_count: int = 0


class CaptureReceipt(StrictDto):
    ok: Literal[True] = True
    request_id: str
    capture_id: UUID
    status: str
    replayed: bool
    policy_version: str
    summary: CaptureSummary
    created_memory_ids: tuple[UUID, ...] = ()
    pending_review_ids: tuple[UUID, ...] = ()
    failure_code: str | None = None

    @classmethod
    def from_result(cls, request_id: str, result: CaptureResult) -> Self:
        decisions = [outcome.decision for outcome in result.outcomes]
        return cls(
            request_id=request_id,
            capture_id=result.capture_id,
            status=result.status.value,
            replayed=result.replayed,
            policy_version=result.metadata.policy_version,
            summary=CaptureSummary(
                auto_saved_count=decisions.count(AdmissionDecision.AUTO_SAVE),
                pending_count=decisions.count(AdmissionDecision.PENDING),
                discarded_count=decisions.count(AdmissionDecision.DISCARD),
                blocked_count=decisions.count(AdmissionDecision.BLOCKED),
            ),
            created_memory_ids=tuple(
                outcome.memory_id
                for outcome in result.outcomes
                if outcome.memory_id is not None
            ),
            pending_review_ids=tuple(
                outcome.review_id
                for outcome in result.outcomes
                if outcome.review_id is not None
            ),
            failure_code=result.failure_code,
        )


class EvidenceView(StrictDto):
    conversation_id: str
    source_turn_id: str
    source_expression: str
    observed_at: datetime
    source_role: MessageRole | None = None
    source_message_id: str | None = None
    source_tool_name: str | None = None


class MemorySummaryView(StrictDto):
    memory_id: UUID
    revision_id: UUID
    scenario: str
    subject: str
    memory_type: str
    content: str
    assertion_kind: str
    lifecycle_status: str
    business_progress: str | None
    observed_at: datetime

    @classmethod
    def from_record(cls, record: MemoryRecord) -> Self:
        return cls(**_memory_summary_values(record))


class MemoryView(MemorySummaryView):
    save_rationale: str
    original_time_expression: str | None
    normalized_time: datetime | None
    evidence: tuple[EvidenceView, ...]

    @classmethod
    def from_record(cls, record: MemoryRecord) -> Self:
        revision = record.current_revision
        return cls(
            **_memory_summary_values(record),
            save_rationale=revision.save_rationale,
            original_time_expression=revision.original_time_expression,
            normalized_time=revision.normalized_time,
            evidence=tuple(
                EvidenceView(
                    conversation_id=evidence.conversation_id,
                    source_turn_id=evidence.source_turn_id,
                    source_expression=evidence.source_expression,
                    observed_at=evidence.observed_at,
                    source_role=evidence.source_role,
                    source_message_id=evidence.source_message_id,
                    source_tool_name=evidence.source_tool_name,
                )
                for evidence in record.evidence
            ),
        )


def _memory_summary_values(record: MemoryRecord) -> dict[str, object]:
    revision = record.current_revision
    return {
        "memory_id": record.item.memory_id,
        "revision_id": revision.revision_id,
        "scenario": record.item.scenario,
        "subject": record.item.subject,
        "memory_type": record.item.memory_type,
        "content": revision.content,
        "assertion_kind": revision.assertion_kind.value,
        "lifecycle_status": revision.lifecycle_status.value,
        "business_progress": revision.business_progress,
        "observed_at": revision.observed_at,
    }


class MemoryListReceipt(StrictDto):
    ok: Literal[True] = True
    request_id: str
    items: tuple[MemorySummaryView, ...]
    next_cursor: str | None = None


class MemoryDetailReceipt(StrictDto):
    ok: Literal[True] = True
    request_id: str
    item: MemoryView
    history_included: bool = False
    history: tuple[MemoryRevisionView, ...] = ()


class MemoryRevisionView(StrictDto):
    revision_id: UUID
    revision_number: int
    content: str
    assertion_kind: str
    lifecycle_status: str
    is_current: bool
    business_progress: str | None
    save_rationale: str
    observed_at: datetime
    created_at: datetime
    evidence: tuple[EvidenceView, ...]

    @classmethod
    def from_entry(cls, entry: MemoryHistoryEntry) -> Self:
        revision = entry.revision
        return cls(
            revision_id=revision.revision_id,
            revision_number=revision.revision_number,
            content=revision.content,
            assertion_kind=revision.assertion_kind.value,
            lifecycle_status=revision.lifecycle_status.value,
            is_current=revision.is_current,
            business_progress=revision.business_progress,
            save_rationale=revision.save_rationale,
            observed_at=revision.observed_at,
            created_at=revision.created_at,
            evidence=tuple(
                EvidenceView(
                    conversation_id=source.conversation_id,
                    source_turn_id=source.source_turn_id,
                    source_expression=source.source_expression,
                    observed_at=source.observed_at,
                    source_role=source.source_role,
                    source_message_id=source.source_message_id,
                    source_tool_name=source.source_tool_name,
                )
                for source in entry.evidence
            ),
        )


class RecallSourceView(StrictDto):
    conversation_id: str
    source_turn_id: str
    source_expression: str
    observed_at: datetime
    source_role: MessageRole | None


class RecalledMemoryView(StrictDto):
    memory_id: UUID
    revision_id: UUID
    scenario: str
    subject: str
    memory_type: str
    content: str
    assertion_kind: str
    observed_at: datetime
    sources: tuple[RecallSourceView, ...]
    relevance_score: float


class RecallReceipt(StrictDto):
    ok: Literal[True] = True
    request_id: str
    items: tuple[RecalledMemoryView, ...]
    rendered_context: str
    estimated_tokens: int
    token_budget: int
    truncated: bool

    @classmethod
    def from_result(cls, request_id: str, result: RecallResult) -> Self:
        return cls(
            request_id=request_id,
            items=tuple(
                RecalledMemoryView(
                    memory_id=item.memory_id,
                    revision_id=item.revision_id,
                    scenario=item.scenario,
                    subject=item.subject,
                    memory_type=item.memory_type,
                    content=item.content,
                    assertion_kind=item.assertion_kind.value,
                    observed_at=item.observed_at,
                    sources=tuple(
                        RecallSourceView(
                            conversation_id=source.conversation_id,
                            source_turn_id=source.source_turn_id,
                            source_expression=source.source_expression,
                            observed_at=source.observed_at,
                            source_role=source.source_role,
                        )
                        for source in item.sources
                    ),
                    relevance_score=item.relevance_score,
                )
                for item in result.items
            ),
            rendered_context=result.rendered_context,
            estimated_tokens=result.estimated_tokens,
            token_budget=result.token_budget,
            truncated=result.truncated,
        )


class PendingReviewView(StrictDto):
    review_id: UUID
    scenario: str
    subject: str
    memory_type: str
    proposed_content: str
    assertion_kind: str
    reason_code: str
    source_expression: str
    observed_at: datetime
    created_at: datetime
    source_role: MessageRole | None = None
    source_message_id: str | None = None
    source_tool_name: str | None = None

    @classmethod
    def from_review(cls, review: ReviewItem) -> Self:
        candidate = review.candidate
        return cls(
            review_id=review.review_id,
            scenario=candidate.scenario,
            subject=candidate.subject,
            memory_type=candidate.memory_type,
            proposed_content=candidate.content,
            assertion_kind=candidate.assertion_kind.value,
            reason_code="user_confirmation_required",
            source_expression=candidate.source_expression,
            observed_at=candidate.observed_at,
            created_at=review.created_at,
            source_role=candidate.source_role,
            source_message_id=candidate.source_message_id,
            source_tool_name=candidate.source_tool_name,
        )


class PendingReviewListReceipt(StrictDto):
    ok: Literal[True] = True
    request_id: str
    items: tuple[PendingReviewView, ...]


class ReviewResolutionReceipt(StrictDto):
    ok: Literal[True] = True
    request_id: str
    review_id: UUID
    status: Literal["confirmed", "rejected"]
    memory: MemoryView | None = None


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        offset = int(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if offset < 0:
        raise ValueError("cursor is invalid")
    return offset
