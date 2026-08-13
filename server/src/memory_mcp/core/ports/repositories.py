"""通用记忆持久化端口。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from memory_mcp.core.domain import (
    CaptureResult,
    Evidence,
    ExtractionMetadata,
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
    TeamExtractionResult,
)
from memory_mcp.core.ports.profiles import MemoryProfile


@dataclass(frozen=True, slots=True)
class CaptureWrite:
    """需要在一次 Repository 事务中提交的捕获结果。

    content/subject_hint 用于落库 memory_captures.content 列（脱敏后原文），
    供 worker 异步抽取路径读取；同步路径也写入以保证 schema NOT NULL 约束。
    """

    result: CaptureResult
    content: str = ""
    subject_hint: str | None = None
    memories: tuple[MemoryRecord, ...] = ()
    reviews: tuple[ReviewItem, ...] = ()
    duplicate_evidence: tuple[DuplicateEvidenceWrite, ...] = ()
    replacements: tuple[ReplacementWrite, ...] = ()
    relations: tuple[MemoryRelation, ...] = ()


@dataclass(frozen=True, slots=True)
class PendingCapture:
    """worker 从队列捞取的一条待抽取 capture，含重建 envelope 所需的最小字段。

    content 已是脱敏后原文（入队时由 sensitive_guard 处理），worker 直接用于
    候选抽取，不需要再次脱敏。
    """

    capture_id: UUID
    owner_id: str
    profile_id: str
    conversation_id: str
    source_turn_id: str
    content: str
    subject_hint: str | None
    observed_at: datetime
    created_at: datetime
    metadata: ExtractionMetadata
    event_id: str | None
    contract_version: str | None
    payload_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class CaptureEnqueueWrite:
    """入队专用写入：PENDING capture 行 + 脱敏后 content/subject_hint。

    content/subject_hint 已过 sensitive_guard，worker 读出后无需再次脱敏。
    """

    result: CaptureResult
    content: str
    subject_hint: str | None = None


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

    def find_semantically_similar(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        memory_type: str,
        embedding: Sequence[float],
        threshold: float,
        effective_at: datetime,
    ) -> MemoryRecord | None:
        """按嵌入余弦相似度查找同 Profile+类型下最接近的一条活动记忆。

        用于准入阶段语义去重：当字面 subject 不匹配但内容语义近似的候选
        即将 auto_save 时，先查同 owner + profile + memory_type 的现有活动记忆，
        余弦相似度 >= ``threshold`` 时返回首条命中，交由调用方决定合并为替换
        或重复证据，避免记忆碎片化。无嵌入或无命中返回 None。
        """

        ...

    def find_assistant_echo(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        embedding: Sequence[float],
        threshold: float,
        effective_at: datetime,
    ) -> MemoryRecord | None:
        """跨 memory_type 查 assistant 回声：同 owner+profile 的活动记忆里
        （不限 memory_type）找余弦相似度最高且 >= threshold 的一条。

        用于 assistant 源候选的跨类型回声检测：assistant 复述已有判断时，模型
        可能把它抽成不同 memory_type 的新候选（如已有 risk，新抽 thesis），
        同类型语义去重查不到。这里不限 memory_type，命中即视为回声，供调用方
        discard。无嵌入或无命中返回 None。
        """

        ...

    def find_semantically_similar_top2(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        memory_type: str,
        embedding: Sequence[float],
        threshold: float,
        effective_at: datetime,
    ) -> tuple[
        tuple[float, MemoryRecord] | None,
        tuple[float, MemoryRecord] | None,
    ]:
        """返回同 Profile+类型下相似度最高的两条活动记忆及其相似度。

        用于 replacement fallback margin 判定：top1 和 top2 相似度差距不足时
        视为歧义，避免语义 fallback 误伤独立 thesis（宁可 Pending 不替错）。
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

    def find_recall_candidates_by_ids(
        self,
        principal: PrincipalContext,
        *,
        memory_ids: Sequence[UUID],
        effective_at: datetime,
    ) -> Sequence[MemoryRecallCandidate]:
        """按 memory_id 集合加载可见的当前活动候选（用于关系感知召回补漏）。

        仅返回 owner 在 visible_owner_ids 内、is_current 且 active/effective
        的记忆；retrieval_score 为 0（由调用方按关系加成提升）。
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

    def extract_team_common_memories(
        self,
        *,
        team_owner_id: str,
        member_owner_ids: tuple[str, ...],
        profile_id: str,
        effective_at: datetime,
        similarity_threshold: float,
        min_cluster_size: int,
    ) -> TeamExtractionResult:
        """扫描团队成员个人记忆，聚类提取共性候选并写入团队 pending review。"""

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

    def commit_capture_enqueue(
        self,
        principal: PrincipalContext,
        write: CaptureEnqueueWrite,
    ) -> CaptureResult:
        """入队专用：插入 PENDING 行（含 content/subject_hint），或对已存在行 replay。

        与 ``commit_capture`` 的区别：只写 capture 行本身（status=pending，
        含 content/subject_hint），不写 outcome/memory/review/relation。content
        和 subject_hint 从 ``write`` 直接读取——入队前由 caller 已过
        sensitive_guard 脱敏。幂等语义与 commit_capture 对齐：同 event_id +
        同 payload_fingerprint 的已存在行直接 replay 返回。
        """

        ...

    def list_pending_captures(
        self,
        *,
        limit: int,
    ) -> tuple[PendingCapture, ...]:
        """捞取待抽取的 PENDING capture（跨 owner，worker 用）。

        用 ``FOR UPDATE SKIP LOCKED``（PG）或等价锁保证并发安全；返回的
        ``PendingCapture.content`` 已是脱敏后原文，worker 直接用于抽取。
        每条捞取的 capture 在同一事务内被标记为处理中（worker 完成后
        调 commit_capture 写终态）。
        """

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
