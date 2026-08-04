"""团队公共记忆提取结果，不含正文，可安全持久化。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TeamExtractionResult:
    """一次团队共性提取运行的结果计数。"""

    team_owner_id: str
    member_count: int
    memory_count: int
    cluster_count: int
    candidate_count: int
    completed_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "member_count",
            "memory_count",
            "cluster_count",
            "candidate_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
