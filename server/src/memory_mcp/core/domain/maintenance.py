"""系统后台维护结果，不含记忆正文，可安全持久化。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """一次有界维护事务完成后的状态转换计数，包括过期记忆、过期审核与失效关系。"""

    effective_at: datetime
    expired_memory_count: int
    expired_review_count: int
    stale_relation_count: int
    has_more: bool

    def __post_init__(self) -> None:
        if self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None:
            raise ValueError("effective_at must be timezone-aware")
        for field_name in (
            "expired_memory_count",
            "expired_review_count",
            "stale_relation_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if not isinstance(self.has_more, bool):
            raise ValueError("has_more must be a boolean")
