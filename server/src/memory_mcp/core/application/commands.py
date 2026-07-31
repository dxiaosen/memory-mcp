"""阶段一手动记忆操作的输入契约。"""

from dataclasses import dataclass
from datetime import datetime

from memory_mcp.core.domain import AssertionKind, LifecycleStatus


def _require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class CreateMemoryCommand:
    """手动创建一张原子记忆卡片；owner 只能来自 ``PrincipalContext``。"""

    profile_id: str
    subject: str
    memory_type: str
    content: str
    assertion_kind: AssertionKind
    lifecycle_status: LifecycleStatus
    conversation_id: str
    source_turn_id: str
    source_expression: str
    save_rationale: str
    observed_at: datetime
    business_progress: str | None = None
    original_time_expression: str | None = None
    normalized_time: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "profile_id",
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
        if not isinstance(self.lifecycle_status, LifecycleStatus):
            raise ValueError("lifecycle_status must be a LifecycleStatus")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.business_progress is not None:
            object.__setattr__(
                self,
                "business_progress",
                _require_text(self.business_progress, "business_progress"),
            )
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
            if (
                self.normalized_time.tzinfo is None
                or self.normalized_time.utcoffset() is None
            ):
                raise ValueError("normalized_time must be timezone-aware")
