"""Owner-scoped 召回请求和结果。"""

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
from memory_mcp.core.domain.relations import MemoryRelationSummary


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class MemoryRecallCandidate:
    """召回排序使用的 Item/Revision 快照，不提前携带来源正文。"""

    item: MemoryItem
    current_revision: MemoryRevision

    def __post_init__(self) -> None:
        if self.item.memory_id != self.current_revision.memory_id:
            raise ValueError("revision must belong to memory item")
        if self.item.owner_id != self.current_revision.owner_id:
            raise ValueError("revision owner must match memory item owner")
        if not self.current_revision.is_current:
            raise ValueError("current_revision must be current")


@dataclass(frozen=True, slots=True)
class RecallQuery:
    """应用层召回条件；owner 由独立 PrincipalContext 提供。"""

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
    """最小可追溯来源摘要。"""

    conversation_id: str | None
    source_turn_id: str
    source_expression: str
    observed_at: datetime
    source_role: MessageRole | None
    source_type: EvidenceSourceType
    document: EvidenceDocument | None = None


@dataclass(frozen=True, slots=True)
class RecalledMemory:
    """召回命中的精确 current revision。"""

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
    """结构化命中和可安全注入 Agent 的服务端渲染块。"""

    items: tuple[RecalledMemory, ...]
    rendered_context: str
    estimated_tokens: int
    token_budget: int
    truncated: bool
