"""通用记忆持久化端口。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from memory_mcp.core.domain import (
    CaptureResult,
    Evidence,
    MemoryHistoryEntry,
    MemoryRecord,
    MemoryRevision,
    PrincipalContext,
    ReviewItem,
    ReviewStatus,
)
from memory_mcp.core.ports.profiles import MemoryProfile


@dataclass(frozen=True, slots=True)
class CaptureWrite:
    """需要在一次 Repository 事务中提交的捕获结果。"""

    result: CaptureResult
    memories: tuple[MemoryRecord, ...] = ()
    reviews: tuple[ReviewItem, ...] = ()
    duplicate_evidence: tuple[DuplicateEvidenceWrite, ...] = ()
    replacements: tuple[ReplacementWrite, ...] = ()


@dataclass(frozen=True, slots=True)
class DuplicateEvidenceWrite:
    """为现有 current revision 增加一条独立来源。"""

    memory_id: UUID
    expected_revision_id: UUID
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class ReplacementWrite:
    """在同一 MemoryItem 内原子替换 current revision。"""

    memory_id: UUID
    expected_revision_id: UUID
    revision: MemoryRevision
    evidence: tuple[Evidence, ...]


class MemoryRepository(Protocol):
    """所有业务读写都必须显式携带可信 owner 上下文。"""

    def register_profile(self, profile: MemoryProfile) -> None:
        """将记忆配置及合法类型登记到持久化约束中。"""

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

    def find_current(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        subject: str | None = None,
        memory_type: str | None = None,
    ) -> Sequence[MemoryRecord]:
        """在 Repository 内先完成 owner/current/active/profile_id/subject 缩小。"""

        ...

    def get_history(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> Sequence[MemoryHistoryEntry]:
        """返回当前 owner 显式请求的一项完整 revision 历史。"""

        ...

    def get_capture(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        conversation_id: str,
        source_turn_id: str,
        profile_version: str,
        event_id: str | None = None,
    ) -> CaptureResult | None:
        """读取同一 owner、source turn 和 profile 版本的捕获结果。"""

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
        duplicate_evidence: DuplicateEvidenceWrite | None = None,
        replacement: ReplacementWrite | None = None,
    ) -> ReviewItem | None:
        """原子确认新记忆/duplicate/replacement，或拒绝 pending 候选。"""

        ...
