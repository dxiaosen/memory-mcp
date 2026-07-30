"""通用记忆应用门面。"""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

from agent_lab.memory.application.admission import ConservativeAdmissionPolicy
from agent_lab.memory.application.capture_service import CaptureService
from agent_lab.memory.application.commands import CreateMemoryCommand
from agent_lab.memory.domain import (
    CaptureResult,
    Evidence,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    PrincipalContext,
    ReviewItem,
    TurnEnvelope,
)
from agent_lab.memory.exceptions import (
    MemoryNotFoundError,
    SensitiveContentBlockedError,
)
from agent_lab.memory.ports import (
    CandidateExtractor,
    MemoryRepository,
    ScenarioPolicy,
    ScenarioRegistry,
    SensitiveContentGuard,
)
from agent_lab.observability import log_event, stable_reference

_LOGGER = logging.getLogger(__name__)


class MemoryService:
    """提供手动记忆操作，并将阶段二用例委托给 CaptureService。"""

    def __init__(
        self,
        repository: MemoryRepository,
        scenario_registry: ScenarioRegistry,
        *,
        candidate_extractor: CandidateExtractor | None = None,
        sensitive_guard: SensitiveContentGuard,
        admission_policy: ConservativeAdmissionPolicy | None = None,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._scenario_registry = scenario_registry
        self._sensitive_guard = sensitive_guard
        self._id_factory = id_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._capture_service = CaptureService(
            repository,
            scenario_registry,
            candidate_extractor=candidate_extractor,
            sensitive_guard=sensitive_guard,
            admission_policy=(admission_policy or ConservativeAdmissionPolicy()),
            id_factory=id_factory,
            clock=self._clock,
        )

    def register_scenario(self, policy: ScenarioPolicy) -> None:
        """同时登记运行时策略和持久化约束。"""

        self._scenario_registry.validate_registration(policy)
        self._repository.register_scenario(policy)
        self._scenario_registry.register(policy)
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.scenario.registered",
            memory_type_count=len(policy.memory_types),
            scenario_id=policy.scenario_id,
        )

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
            scenario=command.scenario,
        )
        self._scenario_registry.validate_memory_type(
            command.scenario,
            command.memory_type,
        )
        self._scenario_registry.validate_business_progress(
            command.scenario,
            command.business_progress,
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
                scenario=command.scenario,
            )
            raise SensitiveContentBlockedError("memory content is prohibited")

        created_at = self._clock()
        memory_id = self._id_factory()
        revision_id = self._id_factory()
        record = MemoryRecord(
            item=MemoryItem(
                memory_id=memory_id,
                owner_id=principal.owner_id,
                scenario=command.scenario,
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
            scenario=record.item.scenario,
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
        return record

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
        )
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.list.completed",
            include_inactive=include_inactive,
            owner_ref=stable_reference(principal.owner_id),
            result_count=len(records),
        )
        return records

    def capture_turn(
        self,
        principal: PrincipalContext,
        turn: TurnEnvelope,
    ) -> CaptureResult:
        return self._capture_service.capture_turn(principal, turn)

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
