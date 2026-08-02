"""不包含业务正文的系统维护结果。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """一次有界维护事务完成的状态转换计数。"""

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
