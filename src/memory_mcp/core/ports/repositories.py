"""通用记忆持久化端口。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from memory_mcp.core.domain import (
    CaptureResult,
    MemoryRecord,
    PrincipalContext,
    ReviewItem,
    ReviewStatus,
)
from memory_mcp.core.ports.scenarios import ScenarioPolicy


@dataclass(frozen=True, slots=True)
class CaptureWrite:
    """需要在一次 Repository 事务中提交的捕获结果。"""

    result: CaptureResult
    memories: tuple[MemoryRecord, ...] = ()
    reviews: tuple[ReviewItem, ...] = ()


class MemoryRepository(Protocol):
    """所有业务读写都必须显式携带可信 owner 上下文。"""

    def register_scenario(self, policy: ScenarioPolicy) -> None:
        """将场景及合法类型登记到持久化约束中。"""

        ...

    def add(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        """原子保存一张包含当前 revision 和来源的记忆卡片。"""

        ...

    def get(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        """读取当前用户拥有的指定记忆；越权与不存在都返回 ``None``。"""

        ...

    def list(
        self,
        principal: PrincipalContext,
        *,
        active_only: bool,
    ) -> Sequence[MemoryRecord]:
        """列出当前用户的当前版本，并可排除非活动记忆。"""

        ...

    def get_capture(
        self,
        principal: PrincipalContext,
        *,
        scenario: str,
        conversation_id: str,
        source_turn_id: str,
        policy_version: str,
        event_id: str | None = None,
    ) -> CaptureResult | None:
        """读取同一 owner、source turn 和 policy 版本的捕获结果。"""

        ...

    def commit_capture(
        self,
        principal: PrincipalContext,
        write: CaptureWrite,
    ) -> None:
        """原子提交捕获状态、活动记忆、待确认项和无正文结果。"""

        ...

    def list_reviews(
        self,
        principal: PrincipalContext,
        *,
        status: ReviewStatus,
    ) -> Sequence[ReviewItem]:
        """列出当前用户指定状态的候选确认项。"""

        ...

    def get_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem | None:
        """读取当前用户拥有的确认项。"""

        ...

    def resolve_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
        *,
        status: ReviewStatus,
        decided_at: datetime,
        memory: MemoryRecord | None = None,
    ) -> ReviewItem | None:
        """原子确认并保存记忆，或拒绝一项 pending 候选。"""

        ...
