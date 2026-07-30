"""Owner-scoped 召回请求和结果。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from memory_mcp.core.domain.models import AssertionKind, MessageRole


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class RecallQuery:
    """应用层召回条件；owner 由独立 PrincipalContext 提供。"""

    scenario: str
    query: str
    subject: str | None = None
    task_intent: str | None = None
    max_items: int = 5
    token_budget: int = 600

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scenario",
            _required_text(self.scenario, "scenario"),
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

    conversation_id: str
    source_turn_id: str
    source_expression: str
    observed_at: datetime
    source_role: MessageRole | None


@dataclass(frozen=True, slots=True)
class RecalledMemory:
    """召回命中的精确 current revision。"""

    memory_id: UUID
    revision_id: UUID
    scenario: str
    subject: str
    memory_type: str
    content: str
    assertion_kind: AssertionKind
    observed_at: datetime
    sources: tuple[RecallSourceSummary, ...]
    relevance_score: float


@dataclass(frozen=True, slots=True)
class RecallResult:
    """结构化命中和可安全注入 Agent 的服务端渲染块。"""

    items: tuple[RecalledMemory, ...]
    rendered_context: str
    estimated_tokens: int
    token_budget: int
    truncated: bool
