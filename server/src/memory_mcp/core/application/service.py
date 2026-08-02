"""通用记忆应用门面。"""

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
    PrincipalContext,
    RecallQuery,
    RecallResult,
    RelationOrigin,
    RelationScope,
    RelationStatus,
    ReviewItem,
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
    MemoryProfile,
    MemoryRepository,
    ProfileRegistry,
    RelationExtractor,
    SensitiveContentGuard,
)
from memory_mcp.logging import log_content_event, log_event, stable_reference

_LOGGER = logging.getLogger(__name__)


class MemoryService:
    """提供手动记忆操作，并将阶段二用例委托给 CaptureService。"""

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
        )
        self._recall_service = RecallService(
            repository,
            profile_registry,
            sensitive_guard,
            clock=self._clock,
            candidate_limit=recall_candidate_limit,
        )
        self._maintenance_service = MemoryMaintenanceService(
            repository,
            clock=self._clock,
        )

    def register_profile(self, profile: MemoryProfile) -> None:
        """同时登记运行时记忆配置和持久化约束。"""

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
        """只供 Server 内部 runner 调用，不注册为公共 MCP 工具。"""

        return self._maintenance_service.run_once()

    def create_memory(
        self,
        principal: PrincipalContext,
        command: CreateMemoryCommand,
    ) -> MemoryRecord:
        """在可信当前用户范围内手动创建一张记忆卡片。"""

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
                last_verified_at=None,
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
        """读取当前用户的记忆，不区分不存在和越权。"""

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
        """显式返回一项记忆的 current 与 superseded revision。"""

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
        """列出当前用户的活动记忆，或显式包含非活动历史。"""

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

    def revoke_memory(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord:
        """幂等撤销当前 owner 的活动记忆并保留可追溯历史。"""

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
        """在两个 owned、有效的稳定记忆身份之间建立有向关系。"""

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
        self._profile_registry.validate_relation(
            source.item.profile_id,
            relation_type,
            source.item.memory_type,
            target.item.memory_type,
        )
        now = self._clock()
        relation = MemoryRelation(
            relation_id=self._id_factory(),
            owner_id=principal.owner_id,
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
        """幂等撤销一条 owned 关系并保留审计时间。"""

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
        """读取一项 owned 记忆的一跳关系。"""

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
        return self._capture_service.capture_turn(principal, turn)

    def recall_memory(
        self,
        principal: PrincipalContext,
        query: RecallQuery,
    ) -> RecallResult:
        """只从当前 owner 的 active/current 集合生成召回上下文。"""

        started_at = perf_counter()
        result = self._recall_service.recall(principal, query)
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.recall.completed",
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            owner_ref=stable_reference(principal.owner_id),
            result_count=len(result.items),
            profile_id=query.profile_id,
            truncated=result.truncated,
        )
        return result

    def list_pending_reviews(
        self,
        principal: PrincipalContext,
    ) -> Sequence[ReviewItem]:
        return self._capture_service.list_pending_reviews(principal)

    def get_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem:
        return self._capture_service.get_review(principal, review_id)

    def confirm_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> MemoryRecord:
        return self._capture_service.confirm_review(principal, review_id)

    def reject_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem:
        return self._capture_service.reject_review(principal, review_id)
