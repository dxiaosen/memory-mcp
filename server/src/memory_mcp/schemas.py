"""Memory MCP 工具输入与结果使用的严格版本化 DTO。"""

import base64
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal, Self
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
    EvidenceSourceType,
    MemoryHistoryEntry,
    MemoryRecord,
    MemoryRelation,
    MemoryRelationSummary,
    MessageRole,
    RecalledMemory,
    RecallResult,
    ReviewItem,
    TimelineResult,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.errors import ErrorCode


class StrictDto(BaseModel):
    """禁止额外字段的 DTO 基类，用于对外契约的严格校验。"""

    model_config = ConfigDict(extra="forbid")


NonEmptyText = Annotated[str, Field(min_length=1)]


class RoleMessageV1(StrictDto):
    """completed-turn 事件中单条消息的输入契约。"""

    role: Literal["user", "assistant", "tool"]
    content: NonEmptyText
    message_id: str | None = Field(default=None, min_length=1)
    tool_name: str | None = Field(default=None, min_length=1)
    source_type: Literal["conversation", "tool", "document", "web"] | None = None
    source_uri: str | None = Field(default=None, min_length=1)
    source_title: str | None = Field(default=None, min_length=1)
    source_publisher: str | None = Field(default=None, min_length=1)
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    content_hash: str | None = Field(default=None, min_length=1)
    citation_locator: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_tool_name(self) -> Self:
        if self.tool_name is not None and self.role != "tool":
            raise ValueError("tool_name is only valid for tool messages")
        return self

    @field_validator("published_at", "retrieved_at")
    @classmethod
    def require_aware_source_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source time must be timezone-aware")
        return value


class CompletedTurnInputV1(StrictDto):
    """一次已完成对话轮次的简化捕获输入契约。

    模型自主调用 ``capture_completed_turn`` 时只传对话内容与对话/轮次标识；
    身份与幂等字段（``event_id`` / ``contract_version`` / ``observed_at`` /
    ``payload_fingerprint``）由服务器在 :meth:`to_turn_envelope` 组装，
    模型不可控，避免 event_id 碰撞或漂移破坏幂等。
    """

    profile_id: NonEmptyText
    conversation_id: NonEmptyText
    turn_id: NonEmptyText
    user_input: NonEmptyText
    final_output: NonEmptyText
    subject_hint: str | None = Field(default=None, min_length=1)

    def input_fingerprint(self) -> str:
        """基于简化输入计算指纹，用于检测同一 event_id 是否被不同内容重用。

        不含 ``profile_id``：同一轮次跨 profile 重投无意义；含身份无关的
        对话内容与标识即可稳定检测冲突。
        """

        canonical = json.dumps(
            {
                "conversation_id": self.conversation_id,
                "turn_id": self.turn_id,
                "user_input": self.user_input,
                "final_output": self.final_output,
                "subject_hint": self.subject_hint,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_turn_envelope(
        self,
        *,
        owner_id: str,
        max_characters: int,
        clock: Callable[[], datetime],
    ) -> TurnEnvelope:
        """组装可信 ``TurnEnvelope``：服务器派生 event_id/observed_at/contract_version。

        ``event_id`` 由 ``(owner_id, conversation_id, turn_id)`` 确定性派生，
        保证同一 owner+对话+轮次重复调用 → 同一 event_id → 服务器幂等 replay。
        ``observed_at`` 取服务器时钟作为单一时间权威。``messages`` 由
        ``user_input`` + ``final_output`` 组装为 ``[user, assistant]`` 两条。
        """

        content = f"[user]\n{self.user_input}\n\n[assistant]\n{self.final_output}"
        if len(content) > max_characters:
            raise ValueError("completed turn exceeds configured capture size")
        event_id = _derive_event_id(owner_id, self.conversation_id, self.turn_id)
        return TurnEnvelope(
            profile_id=self.profile_id,
            conversation_id=self.conversation_id,
            source_turn_id=self.turn_id,
            content=content,
            observed_at=clock(),
            subject_hint=self.subject_hint,
            event_id=event_id,
            contract_version=_CONTRACT_VERSION,
            payload_fingerprint=self.input_fingerprint(),
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=self.user_input,
                    message_id=f"{self.turn_id}:user",
                ),
                TurnMessage(
                    role=MessageRole.ASSISTANT,
                    content=self.final_output,
                    message_id=f"{self.turn_id}:assistant",
                ),
            ),
        )


# 捕获契约版本，由服务器在组装 TurnEnvelope 时硬编码，模型不可传。
_CONTRACT_VERSION = "1"


def _derive_event_id(owner_id: str, conversation_id: str, turn_id: str) -> str:
    """由 (owner_id, conversation_id, turn_id) 确定性派生 event_id。

    同一 owner+对话+轮次重复调用产生同一 event_id，使服务器幂等 replay。
    用 SHA256 而非 uuid5，避免跨进程 NAMESPACE 差异。
    """

    identity = "\x1f".join((owner_id, conversation_id, turn_id))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"memory-agent:{digest}"


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
    """捕获操作的返回凭证，包含统计摘要与创建/待审项 ID。"""

    ok: Literal[True] = True
    request_id: str
    capture_id: UUID
    status: str
    replayed: bool
    profile_version: str
    profile_fingerprint: str
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
            profile_version=result.metadata.profile_version,
            profile_fingerprint=result.metadata.profile_fingerprint,
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


class EvidenceDocumentView(StrictDto):
    """证据引用的外部文档元数据视图。"""

    source_uri: str | None = None
    source_title: str | None = None
    source_publisher: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    content_hash: str | None = None
    citation_locator: str | None = None


class EvidenceView(StrictDto):
    conversation_id: str | None = None
    source_turn_id: str
    source_expression: str
    observed_at: datetime
    source_role: MessageRole | None = None
    source_message_id: str | None = None
    source_tool_name: str | None = None
    source_type: EvidenceSourceType
    document: EvidenceDocumentView | None = None


class MemorySummaryView(StrictDto):
    memory_id: UUID
    revision_id: UUID
    owner_id: str
    profile_id: str
    subject: str
    memory_type: str
    content: str
    assertion_kind: str
    lifecycle_status: str
    business_progress: str | None
    observed_at: datetime
    extraction_confidence: float | None
    verification_status: str
    sensitivity_level: str
    valid_from: datetime
    valid_until: datetime | None

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
                    source_type=evidence.source_type,
                    document=(
                        EvidenceDocumentView(
                            source_uri=evidence.document.source_uri,
                            source_title=evidence.document.source_title,
                            source_publisher=evidence.document.source_publisher,
                            published_at=evidence.document.published_at,
                            retrieved_at=evidence.document.retrieved_at,
                            content_hash=evidence.document.content_hash,
                            citation_locator=evidence.document.citation_locator,
                        )
                        if evidence.document is not None
                        else None
                    ),
                )
                for evidence in record.evidence
            ),
        )


def _memory_summary_values(record: MemoryRecord) -> dict[str, Any]:
    revision = record.current_revision
    return {
        "memory_id": record.item.memory_id,
        "revision_id": revision.revision_id,
        "owner_id": record.item.owner_id,
        "profile_id": record.item.profile_id,
        "subject": record.item.subject,
        "memory_type": record.item.memory_type,
        "content": revision.content,
        "assertion_kind": revision.assertion_kind.value,
        "lifecycle_status": revision.lifecycle_status.value,
        "business_progress": revision.business_progress,
        "observed_at": revision.observed_at,
        "extraction_confidence": revision.extraction_confidence,
        "verification_status": revision.verification_status.value,
        "sensitivity_level": revision.sensitivity_level.value,
        "valid_from": revision.valid_from,
        "valid_until": revision.valid_until,
    }


class RelationProvenanceView(StrictDto):
    capture_id: UUID
    conversation_id: str
    source_turn_id: str
    source_expression: str
    confidence: float
    expression_basis: str
    model_id: str
    prompt_version: str
    schema_version: str


class MemoryRelationView(StrictDto):
    relation_id: UUID
    profile_id: str
    source_memory_id: UUID
    target_memory_id: UUID
    relation_type: str
    origin: str
    scope: str
    source_revision_id: UUID | None
    target_revision_id: UUID | None
    status: str
    created_at: datetime
    revoked_at: datetime | None
    stale_at: datetime | None
    stale_reason: str | None
    provenance: RelationProvenanceView | None = None

    @classmethod
    def from_relation(
        cls,
        relation: MemoryRelation,
        *,
        include_provenance: bool = True,
    ) -> Self:
        provenance = relation.provenance
        return cls(
            relation_id=relation.relation_id,
            profile_id=relation.profile_id,
            source_memory_id=relation.source_memory_id,
            target_memory_id=relation.target_memory_id,
            relation_type=relation.relation_type,
            origin=relation.origin.value,
            scope=relation.scope.value,
            source_revision_id=relation.source_revision_id,
            target_revision_id=relation.target_revision_id,
            status=relation.status.value,
            created_at=relation.created_at,
            revoked_at=relation.revoked_at,
            stale_at=relation.stale_at,
            stale_reason=relation.stale_reason,
            provenance=(
                RelationProvenanceView(
                    capture_id=provenance.capture_id,
                    conversation_id=provenance.conversation_id,
                    source_turn_id=provenance.source_turn_id,
                    source_expression=provenance.source_expression,
                    confidence=provenance.confidence,
                    expression_basis=provenance.expression_basis.value,
                    model_id=provenance.model_id,
                    prompt_version=provenance.prompt_version,
                    schema_version=provenance.schema_version,
                )
                if include_provenance and provenance is not None
                else None
            ),
        )


class MemoryRelationSummaryView(MemoryRelationView):
    direction: str
    related_memory_id: UUID
    related_subject: str
    related_memory_type: str

    @classmethod
    def from_summary(
        cls,
        summary: MemoryRelationSummary,
        *,
        include_provenance: bool = False,
    ) -> Self:
        relation = summary.relation
        return cls(
            **MemoryRelationView.from_relation(
                relation,
                include_provenance=include_provenance,
            ).model_dump(),
            direction=summary.direction.value,
            related_memory_id=summary.related_memory_id,
            related_subject=summary.related_subject,
            related_memory_type=summary.related_memory_type,
        )


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
    relations: tuple[MemoryRelationSummaryView, ...] = ()


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
    extraction_confidence: float | None
    verification_status: str
    sensitivity_level: str
    valid_from: datetime
    valid_until: datetime | None
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
            extraction_confidence=revision.extraction_confidence,
            verification_status=revision.verification_status.value,
            sensitivity_level=revision.sensitivity_level.value,
            valid_from=revision.valid_from,
            valid_until=revision.valid_until,
            evidence=tuple(
                EvidenceView(
                    conversation_id=source.conversation_id,
                    source_turn_id=source.source_turn_id,
                    source_expression=source.source_expression,
                    observed_at=source.observed_at,
                    source_role=source.source_role,
                    source_message_id=source.source_message_id,
                    source_tool_name=source.source_tool_name,
                    source_type=source.source_type,
                    document=(
                        EvidenceDocumentView(
                            source_uri=source.document.source_uri,
                            source_title=source.document.source_title,
                            source_publisher=source.document.source_publisher,
                            published_at=source.document.published_at,
                            retrieved_at=source.document.retrieved_at,
                            content_hash=source.document.content_hash,
                            citation_locator=source.document.citation_locator,
                        )
                        if source.document is not None
                        else None
                    ),
                )
                for source in entry.evidence
            ),
        )


class RecallSourceView(StrictDto):
    conversation_id: str | None
    source_turn_id: str
    source_expression: str
    observed_at: datetime
    source_role: MessageRole | None
    source_type: EvidenceSourceType
    document: EvidenceDocumentView | None = None


class RecalledMemoryView(StrictDto):
    """召回结果中的单条记忆视图。"""

    memory_id: UUID
    revision_id: UUID
    # 记忆归属的 owner key：个人记忆为 tenant:subject，团队公共记忆为
    # tenant:team:team_id。不与请求主体的 owner_id 混淆，仅用于标识来源。
    owner_id: str
    profile_id: str
    subject: str
    memory_type: str
    content: str
    assertion_kind: str
    observed_at: datetime
    extraction_confidence: float | None
    verification_status: str
    sensitivity_level: str
    valid_from: datetime
    valid_until: datetime | None
    sources: tuple[RecallSourceView, ...]
    relations: tuple[MemoryRelationSummaryView, ...]
    relevance_score: float


class RecallReceipt(StrictDto):
    """召回操作的返回凭证，包含结果列表、渲染上下文与 token 预算。"""

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
                _to_recalled_memory_view(item) for item in result.items
            ),
            rendered_context=result.rendered_context,
            estimated_tokens=result.estimated_tokens,
            token_budget=result.token_budget,
            truncated=result.truncated,
        )


class TimelineHopView(StrictDto):
    """时间线演进链中的一跳：端点记忆 + 连接关系信息。"""

    memory: RecalledMemoryView
    relation_type: str
    direction: str
    depth: int


class TimelineReceipt(StrictDto):
    """时间线召回的返回凭证：焦点记忆 + 按 observed_at 升序的演进跳。"""

    ok: Literal[True] = True
    request_id: str
    focus: RecalledMemoryView | None
    hops: tuple[TimelineHopView, ...]
    rendered_context: str
    estimated_tokens: int
    token_budget: int
    truncated: bool

    @classmethod
    def from_result(cls, request_id: str, result: TimelineResult) -> Self:
        focus_view = (
            _to_recalled_memory_view(result.focus) if result.focus is not None else None
        )
        hops = tuple(
            TimelineHopView(
                memory=_to_recalled_memory_view(hop.memory),
                relation_type=hop.relation_type,
                direction=hop.direction.value,
                depth=hop.depth,
            )
            for hop in result.hops
        )
        return cls(
            request_id=request_id,
            focus=focus_view,
            hops=hops,
            rendered_context=result.rendered_context,
            estimated_tokens=result.estimated_tokens,
            token_budget=result.token_budget,
            truncated=result.truncated,
        )


def _to_recalled_memory_view(item: RecalledMemory) -> RecalledMemoryView:
    """把 RecalledMemory 领域对象转换为对外暴露的视图（复用于召回与时间线）。"""

    return RecalledMemoryView(
        memory_id=item.memory_id,
        revision_id=item.revision_id,
        owner_id=item.owner_id,
        profile_id=item.profile_id,
        subject=item.subject,
        memory_type=item.memory_type,
        content=item.content,
        assertion_kind=item.assertion_kind.value,
        observed_at=item.observed_at,
        extraction_confidence=item.extraction_confidence,
        verification_status=item.verification_status.value,
        sensitivity_level=item.sensitivity_level.value,
        valid_from=item.valid_from,
        valid_until=item.valid_until,
        sources=tuple(
            RecallSourceView(
                conversation_id=source.conversation_id,
                source_turn_id=source.source_turn_id,
                source_expression=source.source_expression,
                observed_at=source.observed_at,
                source_role=source.source_role,
                source_type=source.source_type,
                document=(
                    EvidenceDocumentView(
                        source_uri=source.document.source_uri,
                        source_title=source.document.source_title,
                        source_publisher=source.document.source_publisher,
                        published_at=source.document.published_at,
                        retrieved_at=source.document.retrieved_at,
                        content_hash=source.document.content_hash,
                        citation_locator=source.document.citation_locator,
                    )
                    if source.document is not None
                    else None
                ),
            )
            for source in item.sources
        ),
        relations=tuple(
            MemoryRelationSummaryView.from_summary(summary)
            for summary in item.relations
        ),
        relevance_score=item.relevance_score,
    )


class PendingReviewView(StrictDto):
    review_id: UUID
    owner_id: str
    profile_id: str
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
            owner_id=candidate.owner_id,
            profile_id=candidate.profile_id,
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


class MemoryRevocationReceipt(StrictDto):
    ok: Literal[True] = True
    request_id: str
    memory: MemoryView


class MemoryRelationReceipt(StrictDto):
    ok: Literal[True] = True
    request_id: str
    relation: MemoryRelationView


class MemorySearchReceipt(StrictDto):
    """搜索记忆工具的返回凭证。"""

    ok: Literal[True] = True
    request_id: str
    items: tuple[MemorySummaryView, ...]


class BatchReviewResolutionReceipt(StrictDto):
    """批量确认待审工具的返回凭证。"""

    ok: Literal[True] = True
    request_id: str
    confirmed: tuple[ReviewResolutionReceipt, ...] = ()
    failed_review_ids: tuple[UUID, ...] = ()


class MemoryStatsReceipt(StrictDto):
    """记忆统计概览工具的返回凭证。"""

    ok: Literal[True] = True
    request_id: str
    total_active_memories: int
    by_memory_type: dict[str, int] = {}
    by_profile: dict[str, int] = {}
    pending_review_count: int = 0


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
