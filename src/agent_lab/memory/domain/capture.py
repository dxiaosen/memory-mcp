"""阶段二候选捕获、准入和待确认领域对象。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from agent_lab.memory.domain.models import AssertionKind


def _require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _require_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_aware_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class AdmissionDecision(StrEnum):
    """候选进入长期记忆前的四种互斥结果。"""

    AUTO_SAVE = "auto_save"
    PENDING = "pending"
    DISCARD = "discard"
    BLOCKED = "blocked"


class CandidateDurability(StrEnum):
    """模型对候选持续价值的结构化建议。"""

    DURABLE = "durable"
    UNCERTAIN = "uncertain"
    TEMPORARY = "temporary"


class ExpressionBasis(StrEnum):
    """候选是明确表达、弱推断还是含糊表达。"""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    AMBIGUOUS = "ambiguous"


class CaptureStatus(StrEnum):
    """一次 source turn 捕获的持久化处理状态。"""

    COMPLETED = "completed"
    FAILED = "failed"
    REPROCESS_REQUIRED = "reprocess_required"


class ReviewStatus(StrEnum):
    """待确认候选的用户处理状态。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class TurnEnvelope:
    """完成的一轮会话；owner 只能由独立的可信 PrincipalContext 提供。"""

    scenario: str
    conversation_id: str
    source_turn_id: str
    content: str
    observed_at: datetime
    event_id: str | None = None
    contract_version: str | None = None
    payload_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "scenario",
            "conversation_id",
            "source_turn_id",
            "content",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        _require_aware_datetime(self.observed_at, "observed_at")
        for field_name in (
            "event_id",
            "contract_version",
            "payload_fingerprint",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_optional_text(getattr(self, field_name), field_name),
            )
        event_fields = (
            self.event_id,
            self.contract_version,
            self.payload_fingerprint,
        )
        if any(value is not None for value in event_fields) and not all(
            value is not None for value in event_fields
        ):
            raise ValueError(
                "event_id, contract_version, and payload_fingerprint "
                "must be supplied together"
            )


@dataclass(frozen=True, slots=True)
class CandidateProposal:
    """模型适配器返回的未受信候选建议。"""

    subject: str
    memory_type: str
    content: str
    assertion_kind: AssertionKind
    source_expression: str
    save_rationale: str
    confidence: float
    durability: CandidateDurability
    expression_basis: ExpressionBasis
    business_progress: str | None = None
    original_time_expression: str | None = None
    normalized_time: datetime | None = None
    proposed_owner_id: str | None = None
    proposed_conversation_id: str | None = None
    proposed_source_turn_id: str | None = None
    proposed_observed_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "subject",
            "memory_type",
            "content",
            "source_expression",
            "save_rationale",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.assertion_kind, AssertionKind):
            raise ValueError("assertion_kind must be an AssertionKind")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not isinstance(self.durability, CandidateDurability):
            raise ValueError("durability must be a CandidateDurability")
        if not isinstance(self.expression_basis, ExpressionBasis):
            raise ValueError("expression_basis must be an ExpressionBasis")
        for field_name in (
            "business_progress",
            "original_time_expression",
            "proposed_owner_id",
            "proposed_conversation_id",
            "proposed_source_turn_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_optional_text(getattr(self, field_name), field_name),
            )
        if self.normalized_time is not None:
            _require_aware_datetime(self.normalized_time, "normalized_time")
        if self.proposed_observed_at is not None:
            _require_aware_datetime(
                self.proposed_observed_at,
                "proposed_observed_at",
            )


@dataclass(frozen=True, slots=True)
class Candidate:
    """用可信身份和 source turn 覆盖模型建议后的原子候选。"""

    candidate_id: UUID
    owner_id: str
    scenario: str
    subject: str
    memory_type: str
    content: str
    assertion_kind: AssertionKind
    conversation_id: str
    source_turn_id: str
    source_expression: str
    save_rationale: str
    confidence: float
    durability: CandidateDurability
    expression_basis: ExpressionBasis
    observed_at: datetime
    created_at: datetime
    business_progress: str | None = None
    original_time_expression: str | None = None
    normalized_time: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "owner_id",
            "scenario",
            "subject",
            "memory_type",
            "content",
            "conversation_id",
            "source_turn_id",
            "source_expression",
            "save_rationale",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.assertion_kind, AssertionKind):
            raise ValueError("assertion_kind must be an AssertionKind")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not isinstance(self.durability, CandidateDurability):
            raise ValueError("durability must be a CandidateDurability")
        if not isinstance(self.expression_basis, ExpressionBasis):
            raise ValueError("expression_basis must be an ExpressionBasis")
        object.__setattr__(
            self,
            "business_progress",
            _require_optional_text(self.business_progress, "business_progress"),
        )
        object.__setattr__(
            self,
            "original_time_expression",
            _require_optional_text(
                self.original_time_expression,
                "original_time_expression",
            ),
        )
        _require_aware_datetime(self.observed_at, "observed_at")
        _require_aware_datetime(self.created_at, "created_at")
        if self.normalized_time is not None:
            _require_aware_datetime(self.normalized_time, "normalized_time")


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """与活动记忆隔离、等待当前用户确认的候选。"""

    review_id: UUID
    candidate: Candidate
    status: ReviewStatus
    created_at: datetime
    decided_at: datetime | None = None
    resolved_memory_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ReviewStatus):
            raise ValueError("status must be a ReviewStatus")
        _require_aware_datetime(self.created_at, "created_at")
        if self.status is ReviewStatus.PENDING:
            if self.decided_at is not None or self.resolved_memory_id is not None:
                raise ValueError("pending review must not contain a resolution")
        elif self.decided_at is None:
            raise ValueError("resolved review must have decided_at")
        elif self.status is ReviewStatus.CONFIRMED and self.resolved_memory_id is None:
            raise ValueError("confirmed review must identify its memory")
        elif (
            self.status is ReviewStatus.REJECTED and self.resolved_memory_id is not None
        ):
            raise ValueError("rejected review cannot identify a memory")
        if self.decided_at is not None:
            _require_aware_datetime(self.decided_at, "decided_at")

    @property
    def owner_id(self) -> str:
        return self.candidate.owner_id


@dataclass(frozen=True, slots=True)
class ExtractionMetadata:
    """可复现一次结构化抽取所需的版本信息。"""

    model_id: str
    prompt_version: str
    schema_version: str
    policy_version: str

    def __post_init__(self) -> None:
        for field_name in (
            "model_id",
            "prompt_version",
            "schema_version",
            "policy_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """不携带正文的单个候选处理结果。"""

    candidate_id: UUID
    decision: AdmissionDecision
    reason_code: str
    memory_id: UUID | None = None
    review_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, AdmissionDecision):
            raise ValueError("decision must be an AdmissionDecision")
        object.__setattr__(
            self,
            "reason_code",
            _require_text(self.reason_code, "reason_code"),
        )
        if self.decision is AdmissionDecision.AUTO_SAVE:
            if self.memory_id is None or self.review_id is not None:
                raise ValueError("auto-save outcome requires only memory_id")
        elif self.decision is AdmissionDecision.PENDING:
            if self.review_id is None or self.memory_id is not None:
                raise ValueError("pending outcome requires only review_id")
        elif self.memory_id is not None or self.review_id is not None:
            raise ValueError("discarded or blocked outcome cannot reference content")


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """一次幂等捕获的可持久化、无正文结果。"""

    capture_id: UUID
    owner_id: str
    scenario: str
    conversation_id: str
    source_turn_id: str
    metadata: ExtractionMetadata
    status: CaptureStatus
    outcomes: tuple[CaptureOutcome, ...]
    created_at: datetime
    completed_at: datetime
    failure_code: str | None = None
    replayed: bool = False
    was_reprocessed: bool = False
    event_id: str | None = None
    contract_version: str | None = None
    payload_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "owner_id",
            "scenario",
            "conversation_id",
            "source_turn_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.status, CaptureStatus):
            raise ValueError("status must be a CaptureStatus")
        _require_aware_datetime(self.created_at, "created_at")
        _require_aware_datetime(self.completed_at, "completed_at")
        object.__setattr__(
            self,
            "failure_code",
            _require_optional_text(self.failure_code, "failure_code"),
        )
        for field_name in (
            "event_id",
            "contract_version",
            "payload_fingerprint",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_optional_text(getattr(self, field_name), field_name),
            )
        event_fields = (
            self.event_id,
            self.contract_version,
            self.payload_fingerprint,
        )
        if any(value is not None for value in event_fields) and not all(
            value is not None for value in event_fields
        ):
            raise ValueError(
                "event_id, contract_version, and payload_fingerprint "
                "must be supplied together"
            )
        if self.status is CaptureStatus.COMPLETED:
            if self.failure_code is not None:
                raise ValueError("completed capture cannot have failure_code")
        else:
            if self.failure_code is None:
                raise ValueError("failed capture requires failure_code")
            if self.outcomes:
                raise ValueError("failed capture cannot contain outcomes")
