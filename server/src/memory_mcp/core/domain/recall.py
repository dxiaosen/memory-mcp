"""Owner 作用域的召回请求与结果。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from memory_mcp.core.domain.models import (
    AssertionKind,
    EvidenceDocument,
    EvidenceSourceType,
    MemoryItem,
    MemoryRevision,
    MessageRole,
    SensitivityLevel,
    VerificationStatus,
)
from memory_mcp.core.domain.relations import MemoryRelationSummary, RelationDirection


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class MemoryRecallCandidate:
    """召回排序使用的记忆身份与当前版本快照，不携带来源正文以推迟加载。"""

    item: MemoryItem
    current_revision: MemoryRevision
    retrieval_score: float = 0.0

    def __post_init__(self) -> None:
        if self.item.memory_id != self.current_revision.memory_id:
            raise ValueError("revision must belong to memory item")
        if self.item.owner_id != self.current_revision.owner_id:
            raise ValueError("revision owner must match memory item owner")
        if not self.current_revision.is_current:
            raise ValueError("current_revision must be current")


@dataclass(frozen=True, slots=True)
class RecallQuery:
    """应用层召回查询条件，owner 由独立的可信 PrincipalContext 提供。"""

    profile_id: str
    query: str
    subject: str | None = None
    task_intent: str | None = None
    max_items: int = 5
    token_budget: int = 600

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "profile_id",
            _required_text(self.profile_id, "profile_id"),
        )
        object.__setattr__(self, "query", _required_text(self.query, "query"))
        for field_name in ("subject", "task_intent"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _required_text(value, field_name),
                )
        if not 1 <= self.max_items <= 10:
            raise ValueError("max_items must be between 1 and 10")
        if self.token_budget < 1:
            raise ValueError("token_budget must be positive")


@dataclass(frozen=True, slots=True)
class RecallSourceSummary:
    """召回结果中可追溯的来源摘要，只保留追溯所需的最小信息。"""

    conversation_id: str | None
    source_turn_id: str
    source_expression: str
    observed_at: datetime
    source_role: MessageRole | None
    source_type: EvidenceSourceType
    document: EvidenceDocument | None = None


@dataclass(frozen=True, slots=True)
class RecalledMemory:
    """召回命中的当前版本记忆，包含正文、来源摘要、关系摘要和相关度评分。"""

    memory_id: UUID
    revision_id: UUID
    owner_id: str
    profile_id: str
    subject: str
    memory_type: str
    content: str
    assertion_kind: AssertionKind
    observed_at: datetime
    extraction_confidence: float | None
    verification_status: VerificationStatus
    sensitivity_level: SensitivityLevel
    valid_from: datetime
    valid_until: datetime | None
    sources: tuple[RecallSourceSummary, ...]
    relations: tuple[MemoryRelationSummary, ...]
    relevance_score: float


@dataclass(frozen=True, slots=True)
class RecallResult:
    """召回结果集合与服务端预渲染的安全上下文块，可直接注入 Agent。"""

    items: tuple[RecalledMemory, ...]
    rendered_context: str
    estimated_tokens: int
    token_budget: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class TimelineQuery:
    """时间线召回请求：以一条记忆为焦点，沿关系展开演进链。

    owner 由独立的可信 PrincipalContext 提供。``focus_memory_id`` 是演进链的
    起点（通常为一条 thesis）；``max_hops`` 限制一跳关系端点的数量上限；
    ``token_budget`` 裁剪渲染上下文的 token 占用。
    """

    profile_id: str
    focus_memory_id: UUID
    max_hops: int = 20
    token_budget: int = 600

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "profile_id",
            _required_text(self.profile_id, "profile_id"),
        )
        if not isinstance(self.focus_memory_id, UUID):
            raise ValueError("focus_memory_id must be a UUID")
        if not 1 <= self.max_hops <= 50:
            raise ValueError("max_hops must be between 1 and 50")
        if self.token_budget < 1:
            raise ValueError("token_budget must be positive")


@dataclass(frozen=True, slots=True)
class TimelineHop:
    """时间线演进链中的一跳：端点记忆 + 连接它的关系信息。"""

    memory: RecalledMemory
    relation_type: str
    direction: RelationDirection
    depth: int


@dataclass(frozen=True, slots=True)
class TimelineResult:
    """时间线召回结果：焦点记忆 + 按 observed_at 升序排列的演进跳。"""

    focus: RecalledMemory | None
    hops: tuple[TimelineHop, ...]
    rendered_context: str
    estimated_tokens: int
    token_budget: int
    truncated: bool
