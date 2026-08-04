"""通用记忆持久化端口。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from memory_mcp.core.domain import (
    CaptureResult,
    Evidence,
    MaintenanceResult,
    MemoryHistoryEntry,
    MemoryRecallCandidate,
    MemoryRecord,
    MemoryRelation,
    MemoryRelationSummary,
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
    relations: tuple[MemoryRelation, ...] = ()


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


@dataclass(frozen=True, slots=True)
class RecallCandidateSet:
    """Repository 已隔离、去重并限制数量的混合召回候选。"""

    candidates: tuple[MemoryRecallCandidate, ...]
    lexical_count: int
    vector_count: int = 0
    recent_count: int = 0

    def __post_init__(self) -> None:
        for field_name in ("lexical_count", "vector_count", "recent_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        total = self.lexical_count + self.vector_count + self.recent_count
        if total != len(self.candidates):
            raise ValueError("candidate source counts must match records")


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
        effective_at: datetime | None = None,
    ) -> Sequence[MemoryRecord]:
        """列出 owner 名下的当前版本记忆，可选排除非活动记忆。"""

        ...

    def find_current(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        subject: str | None = None,
        memory_type: str | None = None,
        effective_at: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[MemoryRecord]:
        """在 Repository 内按 owner/current/active 再按 profile_id/subject 缩小范围。

        结果自动限定为 principal.owner_key 名下、当前版本且活动状态的记忆；
        可选的 subject 与 memory_type 进一步收窄命中集合。
        """

        ...

    def find_recall_candidates(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        search_text: str,
        subject: str | None,
        effective_at: datetime,
        limit: int,
        query_embedding: Sequence[float] | None = None,
    ) -> RecallCandidateSet:
        """返回 owner/Profile 范围内词法优先、近期补齐的有界候选。

        principal.owner_key 决定可见集合，profile_id 决定可参与的类型与提示；
        subject 可选地收窄到同一主题。返回的候选已去重、按来源计数且总量有界。
        """

        ...

    def load_recall_evidence(
        self,
        principal: PrincipalContext,
        *,
        revision_ids: Sequence[UUID],
        per_revision_limit: int,
    ) -> Mapping[UUID, tuple[Evidence, ...]]:
        """一次性加载 owned revision 的有限最近来源。"""

        ...

    def maintain(
        self,
        *,
        effective_at: datetime,
        review_cutoff: datetime,
        limit: int,
    ) -> MaintenanceResult:
        """执行一次不经公共 Principal 暴露的系统级有界维护事务。"""

        ...

    def revoke(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        """幂等撤销 owned current revision；越权与不存在都返回 ``None``。"""

        ...

    def link_relation(
        self,
        principal: PrincipalContext,
        relation: MemoryRelation,
        *,
        effective_at: datetime,
    ) -> MemoryRelation:
        """幂等建立一条已通过 Profile 校验的活动关系。"""

        ...

    def revoke_relation(
        self,
        principal: PrincipalContext,
        relation_id: UUID,
        *,
        revoked_at: datetime,
    ) -> MemoryRelation | None:
        """幂等撤销 owned 关系；越权与不存在都返回 ``None``。"""

        ...

    def list_relations(
        self,
        principal: PrincipalContext,
        *,
        memory_ids: Sequence[UUID],
        active_only: bool,
        effective_at: datetime | None = None,
    ) -> Sequence[MemoryRelationSummary]:
        """批量返回 owner 名下指定记忆的一跳关系摘要。"""

        ...

    def get_history(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> Sequence[MemoryHistoryEntry]:
        """返回 owner 显式请求的一项记忆的完整 revision 历史。"""

        ...

    def get_capture(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        conversation_id: str,
        source_turn_id: str,
        event_id: str | None = None,
    ) -> CaptureResult | None:
        """按与策略版本无关的逻辑事件身份读取捕获结果。"""

        ...

    def commit_capture(
        self,
        principal: PrincipalContext,
        write: CaptureWrite,
    ) -> CaptureResult:
        """原子提交并返回数据库中的权威捕获结果。"""

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
