"""记忆领域核心模型：身份、版本、来源证据与用户上下文。

这些模型不绑定任何具体记忆配置（Profile）的业务词义，由上层 Profile 赋予
memory_type、sensitivity 等字段的含义。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


def _require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _require_aware_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class AssertionKind(StrEnum):
    """记忆内容的知识性质（用户观点 / 用户事实 / 外部事实 / 系统推断）。"""

    USER_VIEW = "user_view"
    USER_PROVIDED_FACT = "user_provided_fact"
    EXTERNAL_FACT = "external_fact"
    SYSTEM_INFERENCE = "system_inference"


class MessageRole(StrEnum):
    """完成轮次中可追溯的消息来源角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class VerificationStatus(StrEnum):
    """记忆内容已获得的验证类别，与内容是否为真是独立维度。"""

    UNVERIFIED = "unverified"
    USER_ASSERTED = "user_asserted"
    USER_CONFIRMED = "user_confirmed"
    SOURCE_VERIFIED = "source_verified"


class SensitivityLevel(StrEnum):
    """允许持久化内容的敏感级别；被敏感守卫阻断的内容即使标为 restricted 仍不会入库。"""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class EvidenceSourceType(StrEnum):
    """Evidence 所引用来源的通用类别。"""

    CONVERSATION = "conversation"
    TOOL = "tool"
    DOCUMENT = "document"
    WEB = "web"


class LifecycleStatus(StrEnum):
    """记忆的运行时生命周期状态，由 Core 统一管理，记忆配置不得重新定义。"""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    """由应用边界提供的可信当前用户上下文。

    ``owner_id`` 是个人记忆的存储隔离键；``team_owner_ids`` 是该用户所属团队的
    公共记忆 owner key 集合。召回时用 ``visible_owner_ids`` 同时匹配个人和团队记忆。
    """

    owner_id: str
    team_owner_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _require_text(self.owner_id, "owner_id"))
        normalized_teams = tuple(
            _require_text(team_id, "team_owner_id") for team_id in self.team_owner_ids
        )
        object.__setattr__(self, "team_owner_ids", normalized_teams)

    @property
    def visible_owner_ids(self) -> tuple[str, ...]:
        """召回和关系查询使用的 owner 过滤集合（个人 + 团队）。"""

        return (self.owner_id, *self.team_owner_ids)


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """跨 revision 稳定的逻辑记忆身份（同一记忆的不同版本共享此身份）。"""

    memory_id: UUID
    owner_id: str
    profile_id: str
    subject: str
    memory_type: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _require_text(self.owner_id, "owner_id"))
        object.__setattr__(
            self, "profile_id", _require_text(self.profile_id, "profile_id")
        )
        object.__setattr__(self, "subject", _require_text(self.subject, "subject"))
        object.__setattr__(
            self,
            "memory_type",
            _require_text(self.memory_type, "memory_type"),
        )
        _require_aware_datetime(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class MemoryRevision:
    """某一时点不可变的记忆内容和状态快照，是记忆的一个版本。"""

    revision_id: UUID
    memory_id: UUID
    owner_id: str
    revision_number: int
    content: str
    assertion_kind: AssertionKind
    lifecycle_status: LifecycleStatus
    business_progress: str | None
    save_rationale: str
    observed_at: datetime
    created_at: datetime
    extraction_confidence: float | None
    verification_status: VerificationStatus
    sensitivity_level: SensitivityLevel
    valid_from: datetime
    valid_until: datetime | None
    is_current: bool = True
    original_time_expression: str | None = None
    normalized_time: datetime | None = None
    embedding: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _require_text(self.owner_id, "owner_id"))
        if self.revision_number < 1:
            raise ValueError("revision_number must be positive")
        object.__setattr__(self, "content", _require_text(self.content, "content"))
        if not isinstance(self.assertion_kind, AssertionKind):
            raise ValueError("assertion_kind must be an AssertionKind")
        if not isinstance(self.lifecycle_status, LifecycleStatus):
            raise ValueError("lifecycle_status must be a LifecycleStatus")
        if self.business_progress is not None:
            object.__setattr__(
                self,
                "business_progress",
                _require_text(self.business_progress, "business_progress"),
            )
        object.__setattr__(
            self,
            "save_rationale",
            _require_text(self.save_rationale, "save_rationale"),
        )
        _require_aware_datetime(self.observed_at, "observed_at")
        _require_aware_datetime(self.created_at, "created_at")
        if self.extraction_confidence is not None and not (
            0.0 <= self.extraction_confidence <= 1.0
        ):
            raise ValueError("extraction_confidence must be between 0 and 1")
        if not isinstance(self.verification_status, VerificationStatus):
            raise ValueError("verification_status must be a VerificationStatus")
        if not isinstance(self.sensitivity_level, SensitivityLevel):
            raise ValueError("sensitivity_level must be a SensitivityLevel")
        _require_aware_datetime(self.valid_from, "valid_from")
        if self.valid_until is not None:
            _require_aware_datetime(self.valid_until, "valid_until")
            if self.valid_until <= self.valid_from:
                raise ValueError("valid_until must be later than valid_from")
        if self.original_time_expression is not None:
            object.__setattr__(
                self,
                "original_time_expression",
                _require_text(
                    self.original_time_expression,
                    "original_time_expression",
                ),
            )
        if self.normalized_time is not None:
            _require_aware_datetime(self.normalized_time, "normalized_time")


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    """文档/网页来源的引用元数据，仅当 source_type 为 document/web 时填充。"""

    source_uri: str | None = None
    source_title: str | None = None
    source_publisher: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    content_hash: str | None = None
    citation_locator: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "source_uri",
            "source_title",
            "source_publisher",
            "content_hash",
            "citation_locator",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _require_text(value, field_name),
                )
        for field_name in ("published_at", "retrieved_at"):
            value = getattr(self, field_name)
            if value is not None:
                _require_aware_datetime(value, field_name)


@dataclass(frozen=True, slots=True)
class Evidence:
    """通过敏感守卫后可持久化的来源表达，用于追溯记忆的原始信息来源。"""

    evidence_id: UUID
    memory_id: UUID
    revision_id: UUID
    owner_id: str
    source_turn_id: str
    source_expression: str
    observed_at: datetime
    created_at: datetime
    conversation_id: str | None = None
    source_role: MessageRole | None = None
    source_message_id: str | None = None
    source_tool_name: str | None = None
    source_type: EvidenceSourceType = EvidenceSourceType.CONVERSATION
    document: EvidenceDocument | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _require_text(self.owner_id, "owner_id"))
        if self.conversation_id is not None:
            object.__setattr__(
                self,
                "conversation_id",
                _require_text(self.conversation_id, "conversation_id"),
            )
        object.__setattr__(
            self,
            "source_turn_id",
            _require_text(self.source_turn_id, "source_turn_id"),
        )
        object.__setattr__(
            self,
            "source_expression",
            _require_text(self.source_expression, "source_expression"),
        )
        _require_aware_datetime(self.observed_at, "observed_at")
        _require_aware_datetime(self.created_at, "created_at")
        if self.source_role is not None and not isinstance(
            self.source_role,
            MessageRole,
        ):
            raise ValueError("source_role must be a MessageRole")
        if not isinstance(self.source_type, EvidenceSourceType):
            raise ValueError("source_type must be an EvidenceSourceType")
        for field_name in ("source_message_id", "source_tool_name"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _require_text(value, field_name),
                )
        if (
            self.source_tool_name is not None
            and self.source_role is not MessageRole.TOOL
        ):
            raise ValueError("source_tool_name is only valid for tool sources")


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """应用层一次返回的完整记忆卡片，包含身份、当前版本和来源证据。"""

    item: MemoryItem
    current_revision: MemoryRevision
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if self.item.memory_id != self.current_revision.memory_id:
            raise ValueError("revision must belong to memory item")
        if self.item.owner_id != self.current_revision.owner_id:
            raise ValueError("revision owner must match memory item owner")
        if not self.current_revision.is_current:
            raise ValueError("current_revision must be current")
        if not self.evidence:
            raise ValueError("memory record must contain source evidence")
        for source in self.evidence:
            if source.memory_id != self.item.memory_id:
                raise ValueError("evidence must belong to memory item")
            if source.revision_id != self.current_revision.revision_id:
                raise ValueError("evidence must belong to current revision")
            if source.owner_id != self.item.owner_id:
                raise ValueError("evidence owner must match memory item owner")
