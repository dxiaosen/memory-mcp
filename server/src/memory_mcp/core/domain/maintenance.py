"""系统后台维护结果，不含记忆正文，可安全持久化。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ExpiredRelationContext:
    """一条因端点过期而失效的关系的上下文，用于派生过期证据提醒。

    ``expired_memory_id`` / ``expired_subject`` / ``expired_memory_type`` 描述
    已过期的一端（通常是 evidence_claim / risk / catalyst）；``focus_memory_id``
    / ``focus_subject`` / ``focus_memory_type`` 描述另一端（通常是 thesis）。
    维护服务据此查 Profile 的 ``expiry_derivations`` 渲染提醒记忆。
    """

    owner_id: str
    profile_id: str
    relation_type: str
    expired_memory_id: UUID
    expired_subject: str
    expired_memory_type: str
    focus_memory_id: UUID
    focus_subject: str
    focus_memory_type: str

    def __post_init__(self) -> None:
        for field_name in ("owner_id", "profile_id", "relation_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in ("expired_subject", "focus_subject"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in ("expired_memory_type", "focus_memory_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in ("expired_memory_id", "focus_memory_id"):
            value = getattr(self, field_name)
            if not isinstance(value, UUID):
                raise ValueError(f"{field_name} must be a UUID")


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """一次有界维护事务完成后的状态转换计数，包括过期记忆、过期审核与失效关系。

    ``expired_relation_contexts`` 携带因端点过期而失效的关系上下文，供维护服务
    按 Profile 的 ``expiry_derivations`` 派生提醒记忆；默认空，不破坏现有调用方。
    """

    effective_at: datetime
    expired_memory_count: int
    expired_review_count: int
    stale_relation_count: int
    has_more: bool
    expired_relation_contexts: tuple[ExpiredRelationContext, ...] = ()

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
        if not isinstance(self.expired_relation_contexts, tuple):
            raise ValueError("expired_relation_contexts must be a tuple")
        for context in self.expired_relation_contexts:
            if not isinstance(context, ExpiredRelationContext):
                raise ValueError(
                    "expired_relation_contexts must contain ExpiredRelationContext"
                )
