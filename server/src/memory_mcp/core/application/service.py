"""记忆应用层主门面：对外暴露手动记忆操作，对内委托捕获、召回和维护用例。"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

from memory_mcp.core.application.admission import ConservativeAdmissionPolicy
from memory_mcp.core.application.capture_service import CaptureService
from memory_mcp.core.application.commands import CreateMemoryCommand
from memory_mcp.core.application.maintenance_service import MemoryMaintenanceService
from memory_mcp.core.application.recall_service import RecallService
from memory_mcp.core.domain import (
    CaptureResult,
    Evidence,
    MaintenanceResult,
    MemoryHistoryEntry,
    MemoryItem,
    MemoryRecord,
    MemoryRelation,
    MemoryRelationSummary,
    MemoryRevision,
    MemoryTokenizer,
    PrincipalContext,
    RecallQuery,
    RecallResult,
    RelationOrigin,
    RelationScope,
    RelationStatus,
    ReviewItem,
    TimelineQuery,
    TimelineResult,
    TurnEnvelope,
    VerificationStatus,
)
from memory_mcp.core.exceptions import (
    InvalidMemoryRelationError,
    MemoryNotFoundError,
    MemoryRelationNotFoundError,
    SensitiveContentBlockedError,
)
from memory_mcp.core.ports import (
    CandidateExtractor,
    EmbeddingProvider,
    MemoryProfile,
    MemoryRepository,
    ProfileRegistry,
    RelationExtractor,
    SensitiveContentGuard,
)
from memory_mcp.core.support import log_content_event, log_event, stable_reference

_LOGGER = logging.getLogger(__name__)


class MemoryService:
    """手动记忆操作的主入口，并将捕获、召回、维护等用例委托给对应子服务。"""

    def __init__(
        self,
        repository: MemoryRepository,
        profile_registry: ProfileRegistry,
        *,
        candidate_extractor: CandidateExtractor | None = None,
        relation_extractor: RelationExtractor | None = None,
        sensitive_guard: SensitiveContentGuard,
        admission_policy: ConservativeAdmissionPolicy | None = None,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] | None = None,
        recall_candidate_limit: int = 500,
        tokenizer: MemoryTokenizer | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._repository = repository
        self._profile_registry = profile_registry
        self._sensitive_guard = sensitive_guard
        self._id_factory = id_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._capture_service = CaptureService(
            repository,
            profile_registry,
            candidate_extractor=candidate_extractor,
            relation_extractor=relation_extractor,
            sensitive_guard=sensitive_guard,
            admission_policy=(admission_policy or ConservativeAdmissionPolicy()),
            id_factory=id_factory,
            clock=self._clock,
            embedding_provider=embedding_provider,
        )
        self._recall_service = RecallService(
            repository,
            profile_registry,
            sensitive_guard,
            clock=self._clock,
            candidate_limit=recall_candidate_limit,
            tokenizer=tokenizer,
            embedding_provider=embedding_provider,
        )
        self._maintenance_service = MemoryMaintenanceService(
            repository,
            profile_registry,
            clock=self._clock,
            id_factory=id_factory,
        )

    def register_profile(self, profile: MemoryProfile) -> None:
        """登记一个记忆配置：先校验，再写入持久化约束并注册到运行时。"""

        self._profile_registry.validate_registration(profile)
        self._repository.register_profile(profile)
        self._profile_registry.register(profile)
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.profile.registered",
            memory_type_count=len(profile.memory_types),
            profile_id=profile.profile_id,
        )

    def run_maintenance(self) -> MaintenanceResult:
        """运行一次记忆维护批次（过期清理等），仅供 Server 内部调用，不对外暴露为 MCP 工具。"""

        return self._maintenance_service.run_once()

    def create_memory(
        self,
        principal: PrincipalContext,
        command: CreateMemoryCommand,
    ) -> MemoryRecord:
        """在当前用户范围内手动创建一张记忆卡片，含敏感内容校验与证据记录。"""

        started_at = perf_counter()
        owner_reference = stable_reference(principal.owner_id)
        log_event(
            _LOGGER,
            logging.DEBUG,
            "memory.create.started",
            memory_type=command.memory_type,
            owner_ref=owner_reference,
            profile_id=command.profile_id,
        )
        self._profile_registry.validate_memory_type(
            command.profile_id,
            command.memory_type,
        )
        self._profile_registry.validate_business_progress(
            command.profile_id,
            command.business_progress,
        )
        metadata_policy = self._profile_registry.metadata_policy(
            command.profile_id,
            command.memory_type,
        )
        persisted_text = "\n".join(
            value
            for value in (
                command.subject,
                command.content,
                command.source_expression,
                command.save_rationale,
                command.business_progress,
                command.original_time_expression,
            )
            if value is not None
        )
        sensitive = self._sensitive_guard.inspect(persisted_text)
        if sensitive.was_redacted:
            log_event(
                _LOGGER,
                logging.WARNING,
                "memory.create.blocked",
                blocked_categories=sensitive.categories,
                owner_ref=owner_reference,
                profile_id=command.profile_id,
            )
            raise SensitiveContentBlockedError("memory content is prohibited")
        log_content_event(
            "memory.create.input",
            command=asdict(command),
        )

        created_at = self._clock()
        memory_id = self._id_factory()
        revision_id = self._id_factory()
        record = MemoryRecord(
            item=MemoryItem(
                memory_id=memory_id,
                owner_id=principal.owner_id,
                profile_id=command.profile_id,
                subject=command.subject,
                memory_type=command.memory_type,
                created_at=created_at,
            ),
            current_revision=MemoryRevision(
                revision_id=revision_id,
                memory_id=memory_id,
                owner_id=principal.owner_id,
                revision_number=1,
                content=command.content,
                assertion_kind=command.assertion_kind,
                lifecycle_status=command.lifecycle_status,
                business_progress=command.business_progress,
                save_rationale=command.save_rationale,
                observed_at=command.observed_at,
                created_at=created_at,
                extraction_confidence=None,
                verification_status=VerificationStatus.USER_ASSERTED,
                sensitivity_level=metadata_policy.sensitivity_level,
                valid_from=command.observed_at,
                valid_until=(
                    command.observed_at + timedelta(days=metadata_policy.validity_days)
                    if metadata_policy.validity_days is not None
                    else None
                ),
                original_time_expression=command.original_time_expression,
                normalized_time=command.normalized_time,
            ),
            evidence=(
                Evidence(
                    evidence_id=self._id_factory(),
                    memory_id=memory_id,
                    revision_id=revision_id,
                    owner_id=principal.owner_id,
                    conversation_id=command.conversation_id,
                    source_turn_id=command.source_turn_id,
                    source_expression=command.source_expression,
                    observed_at=command.observed_at,
                    created_at=created_at,
                ),
            ),
        )
        self._repository.add(principal, record)
        log_content_event(
            "memory.create.persisted",
            memory=asdict(record),
        )
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.create.completed",
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            evidence_count=len(record.evidence),
            lifecycle_status=record.current_revision.lifecycle_status.value,
            memory_id=record.item.memory_id,
            owner_ref=owner_reference,
            revision_id=record.current_revision.revision_id,
            profile_id=record.item.profile_id,
        )
        return record

    def get_memory(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord:
        """读取当前用户的一条记忆，不存在与越权统一返回不可用。"""

        record = self._repository.get(principal, memory_id)
        if record is None:
            log_event(
                _LOGGER,
                logging.INFO,
                "memory.get.unavailable",
                memory_id=memory_id,
                owner_ref=stable_reference(principal.owner_id),
            )
            raise MemoryNotFoundError("memory is unavailable")
        log_event(
            _LOGGER,
            logging.DEBUG,
            "memory.get.completed",
            memory_id=memory_id,
            owner_ref=stable_reference(principal.owner_id),
        )
        log_content_event(
            "memory.read.get",
            memory=asdict(record),
        )
        return record

    def get_memory_history(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> Sequence[MemoryHistoryEntry]:
        """返回一项记忆的全部修订历史（当前版与被取代版）。"""

        if self._repository.get(principal, memory_id) is None:
            raise MemoryNotFoundError("memory is unavailable")
        history = self._repository.get_history(principal, memory_id)
        log_content_event(
            "memory.read.history",
            history=tuple(asdict(entry) for entry in history),
            memory_id=memory_id,
        )
        return history

    def list_memories(
        self,
        principal: PrincipalContext,
        *,
        include_inactive: bool = False,
    ) -> Sequence[MemoryRecord]:
        """列出当前用户的记忆，默认只返回活动记忆，可显式包含非活动项。"""

        records = self._repository.list(
            principal,
            active_only=not include_inactive,
            effective_at=self._clock(),
        )
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.list.completed",
            include_inactive=include_inactive,
            owner_ref=stable_reference(principal.owner_id),
            result_count=len(records),
        )
        log_content_event(
            "memory.read.list",
            memories=tuple(asdict(record) for record in records),
        )
        return records

    def search_memories(
        self,
        principal: PrincipalContext,
        *,
        query: str,
        profile_id: str | None = None,
        memory_type: str | None = None,
        limit: int = 20,
    ) -> Sequence[MemoryRecord]:
        """按关键词搜索当前用户的活动记忆，返回完整记录列表（不裁剪 token 预算）。

        与 recall_memory 的区别：recall 做相关性排序并裁剪到 token 预算生成
        rendered context；search 只按 pg_trgm 相似度排序返回完整记录列表，
        供研究员精准检索特定主题的历史证据和判断。
        """

        if not query.strip():
            raise ValueError("query must not be empty")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if profile_id is None:
            raise ValueError("profile_id is required for search")
        resolved_profile_id = profile_id
        log_event(
            _LOGGER,
            logging.DEBUG,
            "memory.search.started",
            owner_ref=stable_reference(principal.owner_id),
            profile_id=resolved_profile_id,
            memory_type=memory_type,
            limit=limit,
        )
        candidates = self._repository.find_recall_candidates(
            principal,
            profile_id=resolved_profile_id,
            search_text=query,
            subject=None,
            effective_at=self._clock(),
            limit=limit,
        )
        # search 只返回词法/向量匹配的候选，排除近期补齐
        match_count = candidates.lexical_count + candidates.vector_count
        matched = candidates.candidates[:match_count]
        records: list[MemoryRecord] = []
        for candidate in matched:
            record = self._repository.get(
                principal, candidate.item.memory_id
            )
            if record is not None and (
                memory_type is None
                or record.item.memory_type == memory_type
            ):
                records.append(record)
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.search.completed",
            owner_ref=stable_reference(principal.owner_id),
            result_count=len(records),
        )
        log_content_event(
            "memory.read.search",
            memories=tuple(asdict(record) for record in records),
        )
        return records

    def batch_confirm_reviews(
        self,
        principal: PrincipalContext,
        review_ids: Sequence[UUID],
        *,
        team_id: str | None = None,
        team_owner_ids: frozenset[str] = frozenset(),
    ) -> tuple[tuple[MemoryRecord, ...], tuple[UUID, ...]]:
        """批量确认待审候选，返回成功和失败的 review_id。

        每条独立调用 confirm_review，单条失败不影响其他条。
        """

        confirmed: list[MemoryRecord] = []
        failed: list[UUID] = []
        for review_id in review_ids:
            try:
                record = self._capture_service.confirm_review(
                    principal,
                    review_id,
                    team_id=team_id,
                    team_owner_ids=team_owner_ids,
                )
                confirmed.append(record)
            except Exception as exc:
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    "memory.review.confirm_failed",
                    owner_ref=stable_reference(principal.owner_id),
                    review_id=str(review_id),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                failed.append(review_id)
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.review.batch_confirmed",
            owner_ref=stable_reference(principal.owner_id),
            confirmed_count=len(confirmed),
            failed_count=len(failed),
        )
        return tuple(confirmed), tuple(failed)

    def get_memory_stats(
        self,
        principal: PrincipalContext,
    ) -> dict[str, object]:
        """返回当前用户的记忆统计概览。"""

        records = self._repository.list(
            principal,
            active_only=True,
            effective_at=self._clock(),
        )
        from collections import Counter

        type_counts = Counter(record.item.memory_type for record in records)
        profile_counts = Counter(record.item.profile_id for record in records)
        pending_reviews = self._capture_service.list_pending_reviews(principal)
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.stats.completed",
            owner_ref=stable_reference(principal.owner_id),
            total_memories=len(records),
            pending_count=len(pending_reviews),
        )
        return {
            "total_active_memories": len(records),
            "by_memory_type": dict(type_counts),
            "by_profile": dict(profile_counts),
            "pending_review_count": len(pending_reviews),
        }

    def revoke_memory(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord:
        """幂等撤销当前用户的一条活动记忆，保留可追溯历史。"""

        record = self._repository.revoke(principal, memory_id)
        if record is None:
            raise MemoryNotFoundError("memory is unavailable")
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.revoke.completed",
            lifecycle_status=record.current_revision.lifecycle_status.value,
            memory_id=memory_id,
            owner_ref=stable_reference(principal.owner_id),
        )
        return record

    def link_memories(
        self,
        principal: PrincipalContext,
        source_memory_id: UUID,
        target_memory_id: UUID,
        relation_type: str,
    ) -> MemoryRelation:
        """在两条同 owner、同 profile 且有效的记忆之间建立有向关系。"""

        if source_memory_id == target_memory_id:
            raise InvalidMemoryRelationError("memory relation cannot be a self loop")
        source = self._repository.get(principal, source_memory_id)
        target = self._repository.get(principal, target_memory_id)
        if source is None or target is None:
            raise MemoryNotFoundError("memory is unavailable")
        if source.item.profile_id != target.item.profile_id:
            raise InvalidMemoryRelationError(
                "memory relation endpoints must share a profile"
            )
        if source.item.owner_id != target.item.owner_id:
            raise InvalidMemoryRelationError(
                "memory relation endpoints must share an owner"
            )
        self._profile_registry.validate_relation(
            source.item.profile_id,
            relation_type,
            source.item.memory_type,
            target.item.memory_type,
        )
        now = self._clock()
        # 关系的 owner 跟随端点记忆：个人记忆间的关系归个人，团队记忆间的关系归团队。
        relation_owner = source.item.owner_id
        relation = MemoryRelation(
            relation_id=self._id_factory(),
            owner_id=relation_owner,
            profile_id=source.item.profile_id,
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            relation_type=relation_type,
            status=RelationStatus.ACTIVE,
            created_at=now,
            origin=RelationOrigin.MANUAL,
            scope=RelationScope.ITEM,
            source_revision_id=source.current_revision.revision_id,
            target_revision_id=target.current_revision.revision_id,
        )
        try:
            committed = self._repository.link_relation(
                principal,
                relation,
                effective_at=now,
            )
        except ValueError as exc:
            raise InvalidMemoryRelationError(
                "memory relation could not be created"
            ) from exc
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.relation.linked",
            relation_id=committed.relation_id,
            relation_origin=committed.origin.value,
            relation_scope=committed.scope.value,
            relation_type=committed.relation_type,
            source_memory_id=committed.source_memory_id,
            target_memory_id=committed.target_memory_id,
        )
        return committed

    def revoke_memory_relation(
        self,
        principal: PrincipalContext,
        relation_id: UUID,
    ) -> MemoryRelation:
        """幂等撤销一条当前用户拥有的关系，保留审计时间。"""

        relation = self._repository.revoke_relation(
            principal,
            relation_id,
            revoked_at=self._clock(),
        )
        if relation is None:
            raise MemoryRelationNotFoundError("memory relation is unavailable")
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.relation.revoked",
            relation_id=relation.relation_id,
            relation_origin=relation.origin.value,
            relation_scope=relation.scope.value,
            relation_type=relation.relation_type,
        )
        return relation

    def list_memory_relations(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> Sequence[MemoryRelationSummary]:
        """读取一条用户拥有的记忆的一跳关系。"""

        if self._repository.get(principal, memory_id) is None:
            raise MemoryNotFoundError("memory is unavailable")
        return self._repository.list_relations(
            principal,
            memory_ids=(memory_id,),
            active_only=not include_inactive,
            effective_at=self._clock(),
        )

    def capture_turn(
        self,
        principal: PrincipalContext,
        turn: TurnEnvelope,
    ) -> CaptureResult:
        """从一轮对话中抽取并写入候选记忆，委托给捕获子服务。"""
        return self._capture_service.capture_turn(principal, turn)

    def recall_memory(
        self,
        principal: PrincipalContext,
        query: RecallQuery,
    ) -> RecallResult:
        """从当前用户的活动/当前记忆集合生成召回上下文，委托给召回子服务。"""

        return self._recall_service.recall(principal, query)

    def recall_timeline(
        self,
        principal: PrincipalContext,
        query: TimelineQuery,
    ) -> TimelineResult:
        """以焦点记忆为起点沿演进关系展开时间线，委托给召回子服务。"""

        return self._recall_service.recall_timeline(principal, query)

    def list_pending_reviews(
        self,
        principal: PrincipalContext,
    ) -> Sequence[ReviewItem]:
        """列出当前用户待确认的候选记忆。"""
        return self._capture_service.list_pending_reviews(principal)

    def get_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem:
        """读取一条待确认候选的详情。"""
        return self._capture_service.get_review(principal, review_id)

    def confirm_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
        *,
        team_id: str | None = None,
        team_owner_ids: frozenset[str] = frozenset(),
    ) -> MemoryRecord:
        """确认一条候选并写入记忆，可选提升到指定团队。"""
        return self._capture_service.confirm_review(
            principal,
            review_id,
            team_id=team_id,
            team_owner_ids=team_owner_ids,
        )

    def reject_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem:
        """拒绝一条候选，标记为已驳回。"""
        return self._capture_service.reject_review(principal, review_id)
