"""捕获流程编排：协调候选抽取、敏感内容校验、准入与原子写入，并暴露待确认门面。"""

import logging
import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import UUID

from memory_mcp.core.application.admission import ConservativeAdmissionPolicy
from memory_mcp.core.application.automatic_relations import (
    AutomaticRelationPlan,
    AutomaticRelationPlanner,
)
from memory_mcp.core.application.candidate_processing import (
    CandidateMaterializer,
    CandidateProcessingResult,
    CandidateProcessor,
)
from memory_mcp.core.application.review_service import ReviewService
from memory_mcp.core.domain import (
    AdmissionDecision,
    CaptureOutcome,
    CaptureResult,
    CaptureStatus,
    ExtractionMetadata,
    MemoryRecord,
    MessageRole,
    PrincipalContext,
    ReviewItem,
    TurnEnvelope,
)
from memory_mcp.core.exceptions import (
    CaptureNotConfiguredError,
    IdempotencyConflictError,
    InvalidMemoryTypeError,
    InvalidModelOutputError,
    InvalidProfileProgressError,
)
from memory_mcp.core.ports import (
    CandidateExtractor,
    CaptureWrite,
    EmbeddingProvider,
    ExtractionRequest,
    MemoryRepository,
    ProfileRegistry,
    RelationExtractor,
    SensitiveContentGuard,
    profile_fingerprint,
)
from memory_mcp.core.support import log_content_event, log_event, stable_reference

_LOGGER = logging.getLogger(__name__)
_REDACTION_MARKER = re.compile(r"\[REDACTED:[^\]]+\]")


class CaptureService:
    """协调一轮对话的记忆捕获：抽取候选、准入决策、关系规划并原子提交结果。"""

    def __init__(
        self,
        repository: MemoryRepository,
        profile_registry: ProfileRegistry,
        *,
        candidate_extractor: CandidateExtractor | None,
        relation_extractor: RelationExtractor | None,
        sensitive_guard: SensitiveContentGuard | None,
        admission_policy: ConservativeAdmissionPolicy,
        id_factory: Callable[[], UUID],
        clock: Callable[[], datetime],
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._repository = repository
        self._profile_registry = profile_registry
        self._candidate_extractor = candidate_extractor
        self._sensitive_guard = sensitive_guard
        self._id_factory = id_factory
        self._clock = clock
        self._capture_locks = _KeyedLocks()
        self._relation_planner = (
            AutomaticRelationPlanner(
                repository,
                profile_registry,
                relation_extractor,
                id_factory=id_factory,
                clock=clock,
            )
            if relation_extractor is not None
            else None
        )
        materializer = CandidateMaterializer(
            id_factory=id_factory,
            clock=clock,
            embedding_provider=embedding_provider,
        )
        self._candidate_processor = (
            CandidateProcessor(
                repository,
                profile_registry,
                sensitive_guard,
                admission_policy,
                materializer,
                id_factory=id_factory,
                clock=clock,
            )
            if sensitive_guard is not None
            else None
        )
        self._review_service = ReviewService(
            repository,
            profile_registry,
            materializer,
            clock=clock,
        )

    def capture_turn(
        self,
        principal: PrincipalContext,
        turn: TurnEnvelope,
    ) -> CaptureResult:
        """捕获一轮对话的记忆，按事件/轮次键加锁以保证同进程内重试串行执行。"""

        if turn.event_id is not None:
            key = (principal.owner_id, "event", turn.event_id)
        else:
            key = (
                principal.owner_id,
                "legacy",
                turn.profile_id,
                turn.conversation_id,
                turn.source_turn_id,
            )
        with self._capture_locks.hold(key):
            return self._capture_turn_locked(principal, turn)

    def _capture_turn_locked(
        self,
        principal: PrincipalContext,
        turn: TurnEnvelope,
    ) -> CaptureResult:
        """在已持锁的前提下执行捕获：敏感校验、模型抽取、候选处理、关系规划并提交。"""

        extractor = self._candidate_extractor
        guard = self._sensitive_guard
        processor = self._candidate_processor
        if extractor is None or guard is None or processor is None:
            raise CaptureNotConfiguredError(
                "candidate extractor and sensitive guard are required"
            )
        profile = self._profile_registry.get(turn.profile_id)
        metadata = ExtractionMetadata(
            model_id=extractor.model_id,
            prompt_version=extractor.prompt_version,
            schema_version=extractor.schema_version,
            profile_version=profile.profile_version,
            profile_fingerprint=profile_fingerprint(profile),
        )
        existing = self._repository.get_capture(
            principal,
            profile_id=turn.profile_id,
            conversation_id=turn.conversation_id,
            source_turn_id=turn.source_turn_id,
            event_id=turn.event_id,
        )
        if (
            existing is not None
            and turn.payload_fingerprint is not None
            and existing.payload_fingerprint != turn.payload_fingerprint
        ):
            log_event(
                _LOGGER,
                logging.WARNING,
                "memory.capture.idempotency_conflict",
                capture_id=existing.capture_id,
                owner_ref=stable_reference(principal.owner_id),
                event_id=turn.event_id,
            )
            raise IdempotencyConflictError(
                "event identifier was reused with a different payload"
            )
        if existing is not None and existing.status is not (
            CaptureStatus.REPROCESS_REQUIRED
        ):
            log_event(
                _LOGGER,
                logging.INFO,
                "memory.capture.replay",
                capture_id=existing.capture_id,
                owner_ref=stable_reference(principal.owner_id),
                status=existing.status.value,
                replayed=True,
            )
            return replace(existing, replayed=True)

        capture_id = existing.capture_id if existing is not None else self._id_factory()
        created_at = existing.created_at if existing is not None else self._clock()
        was_reprocessed = existing is not None
        _capture_started_at = perf_counter()
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.capture.started",
            capture_id=capture_id,
            owner_ref=stable_reference(principal.owner_id),
            profile_id=turn.profile_id,
            profile_version=profile.profile_version,
            was_reprocessed=was_reprocessed,
            event_id=turn.event_id,
            message_count=len(turn.messages),
            input_character_count=len(turn.content),
        )

        try:
            inspection = guard.inspect(turn.content)
            subject_hint_inspection = guard.inspect(turn.subject_hint or "")
            log_content_event(
                "memory.capture.input",
                capture_id=capture_id,
                content=inspection.redacted_text,
                conversation_id=turn.conversation_id,
                event_id=turn.event_id,
                messages=tuple(
                    {
                        "role": message.role.value,
                        "content": guard.inspect(message.content).redacted_text,
                    }
                    for message in turn.messages
                ),
                profile_id=turn.profile_id,
                source_turn_id=turn.source_turn_id,
                subject_hint=(
                    subject_hint_inspection.redacted_text
                    if turn.subject_hint is not None
                    else None
                ),
            )
            initial_outcomes = tuple(
                CaptureOutcome(
                    candidate_id=self._id_factory(),
                    decision=AdmissionDecision.BLOCKED,
                    reason_code=f"sensitive_{category}",
                )
                for category in dict.fromkeys(
                    (*inspection.categories, *subject_hint_inspection.categories)
                )
            )
            proposals = ()
            if _has_processable_content(inspection.redacted_text):
                proposals = extractor.extract(
                    ExtractionRequest(
                        profile_id=turn.profile_id,
                        conversation_id=turn.conversation_id,
                        source_turn_id=turn.source_turn_id,
                        content=inspection.redacted_text,
                        observed_at=turn.observed_at,
                        allowed_memory_types=profile.memory_types,
                        capture_guidance=profile.capture_guidance,
                        profile_version=profile.profile_version,
                        business_progress_values=profile.business_progress_values,
                        subject_hint=(
                            subject_hint_inspection.redacted_text
                            if turn.subject_hint is not None
                            else None
                        ),
                    )
                )
            processed = processor.process(
                principal,
                turn,
                proposals,
                redacted_source=inspection.redacted_text,
                initial_outcomes=initial_outcomes,
            )
            relation_plan = AutomaticRelationPlan()
            if self._relation_planner is not None and _has_processable_content(
                inspection.redacted_text
            ):
                relation_plan = self._relation_planner.plan(
                    principal,
                    profile=profile,
                    capture_id=capture_id,
                    conversation_id=turn.conversation_id,
                    source_turn_id=turn.source_turn_id,
                    redacted_source=inspection.redacted_text,
                    observed_at=turn.observed_at,
                    same_capture_memories=_relation_endpoint_records(
                        self._repository,
                        principal,
                        processed,
                    ),
                    subject_hint=(
                        subject_hint_inspection.redacted_text
                        if turn.subject_hint is not None
                        else None
                    ),
                    trusted_user_sources=(
                        tuple(
                            guard.inspect(message.content).redacted_text
                            for message in turn.messages
                            if message.role is MessageRole.USER
                        )
                        if turn.messages
                        else None
                    ),
                )
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "memory.capture.relations_planned",
                    accepted_count=len(relation_plan.relations),
                    capture_id=capture_id,
                    endpoint_count=relation_plan.endpoint_count,
                    model_id=self._relation_planner.model_id,
                    prompt_version=self._relation_planner.prompt_version,
                    proposal_count=relation_plan.proposal_count,
                    schema_version=self._relation_planner.schema_version,
                    skipped_count=relation_plan.skipped_count,
                )
            log_content_event(
                "memory.capture.candidates",
                capture_id=capture_id,
                candidates=tuple(
                    asdict(candidate) for candidate in processed.candidates
                ),
            )
            log_content_event(
                "memory.capture.admission",
                capture_id=capture_id,
                outcomes=tuple(asdict(outcome) for outcome in processed.outcomes),
            )
            log_content_event(
                "memory.capture.relation_candidates",
                capture_id=capture_id,
                proposals=tuple(
                    asdict(proposal) for proposal in relation_plan.proposals
                ),
                relations=tuple(
                    asdict(relation) for relation in relation_plan.relations
                ),
            )
        except (
            InvalidMemoryTypeError,
            InvalidModelOutputError,
            InvalidProfileProgressError,
            ValueError,
        ) as exc:
            log_event(
                _LOGGER,
                logging.WARNING,
                "memory.capture.invalid_output",
                capture_id=capture_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
                error_detail=_validation_errors(exc),
                cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None,
                cause_message=str(exc.__cause__) if exc.__cause__ else None,
                owner_ref=stable_reference(principal.owner_id),
            )
            return self._commit_capture_failure(
                principal,
                turn,
                capture_id=capture_id,
                created_at=created_at,
                metadata=metadata,
                status=CaptureStatus.FAILED,
                failure_code="invalid_candidate_output",
                was_reprocessed=was_reprocessed,
                started_at=_capture_started_at,
            )
        except Exception as exc:
            log_event(
                _LOGGER,
                logging.ERROR,
                "memory.capture.processing_failed",
                capture_id=capture_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
                cause_type=(
                    type(exc.__cause__).__name__ if exc.__cause__ else None
                ),
                cause_message=str(exc.__cause__) if exc.__cause__ else None,
                owner_ref=stable_reference(principal.owner_id),
            )
            return self._commit_capture_failure(
                principal,
                turn,
                capture_id=capture_id,
                created_at=created_at,
                metadata=metadata,
                status=CaptureStatus.REPROCESS_REQUIRED,
                failure_code="processing_interrupted",
                was_reprocessed=was_reprocessed,
                started_at=_capture_started_at,
            )

        result = CaptureResult(
            capture_id=capture_id,
            owner_id=principal.owner_id,
            profile_id=turn.profile_id,
            conversation_id=turn.conversation_id,
            source_turn_id=turn.source_turn_id,
            metadata=metadata,
            status=CaptureStatus.COMPLETED,
            outcomes=processed.outcomes,
            created_at=created_at,
            completed_at=self._clock(),
            was_reprocessed=was_reprocessed,
            event_id=turn.event_id,
            contract_version=turn.contract_version,
            payload_fingerprint=turn.payload_fingerprint,
        )
        committed = self._repository.commit_capture(
            principal,
            CaptureWrite(
                result=result,
                memories=processed.memories,
                reviews=processed.reviews,
                duplicate_evidence=processed.duplicate_evidence,
                replacements=processed.replacements,
                relations=relation_plan.relations,
            ),
        )
        persisted = not committed.replayed
        log_content_event(
            "memory.capture.persisted",
            capture=asdict(committed),
            duplicate_evidence=(
                tuple(asdict(write) for write in processed.duplicate_evidence)
                if persisted
                else ()
            ),
            memories=(
                tuple(asdict(memory) for memory in processed.memories)
                if persisted
                else ()
            ),
            replacements=(
                tuple(asdict(write) for write in processed.replacements)
                if persisted
                else ()
            ),
            relations=(
                tuple(asdict(relation) for relation in relation_plan.relations)
                if persisted
                else ()
            ),
            reviews=(
                tuple(asdict(review) for review in processed.reviews)
                if persisted
                else ()
            ),
        )
        _decision_counts = Counter(
            outcome.decision.value for outcome in committed.outcomes
        )
        _reason_counts = Counter(
            outcome.reason_code for outcome in committed.outcomes
        )
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.capture.completed",
            capture_id=committed.capture_id,
            owner_ref=stable_reference(principal.owner_id),
            profile_id=turn.profile_id,
            replayed=committed.replayed,
            was_reprocessed=was_reprocessed,
            duration_ms=round((perf_counter() - _capture_started_at) * 1000, 3),
            candidate_count=len(processed.candidates),
            auto_saved_count=_decision_counts.get(
                AdmissionDecision.AUTO_SAVE.value, 0
            ),
            pending_count=_decision_counts.get(
                AdmissionDecision.PENDING.value, 0
            ),
            discarded_count=_decision_counts.get(
                AdmissionDecision.DISCARD.value, 0
            ),
            blocked_count=_decision_counts.get(
                AdmissionDecision.BLOCKED.value, 0
            ),
            reason_counts=dict(_reason_counts),
            duplicate_count=len(processed.duplicate_evidence),
            replacement_count=len(processed.replacements),
            review_count=len(processed.reviews),
            relation_proposal_count=relation_plan.proposal_count,
            relation_accepted_count=len(relation_plan.relations),
            relation_skipped_count=relation_plan.skipped_count,
            failure_code=committed.failure_code,
        )
        return committed

    def list_pending_reviews(
        self,
        principal: PrincipalContext,
    ) -> Sequence[ReviewItem]:
        """列出当前用户待确认的候选记忆。"""

        return self._review_service.list_pending(principal)

    def get_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem:
        """读取一条待确认候选的详情。"""
        return self._review_service.get(principal, review_id)

    def confirm_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
        *,
        team_id: str | None = None,
        team_owner_ids: frozenset[str] = frozenset(),
    ) -> MemoryRecord:
        """确认一条候选并写入记忆，可选提升到指定团队。"""
        return self._review_service.confirm(
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
        return self._review_service.reject(principal, review_id)

    def _commit_capture_failure(
        self,
        principal: PrincipalContext,
        turn: TurnEnvelope,
        *,
        capture_id: UUID,
        created_at: datetime,
        metadata: ExtractionMetadata,
        status: CaptureStatus,
        failure_code: str,
        was_reprocessed: bool,
        started_at: float = 0.0,
    ) -> CaptureResult:
        result = CaptureResult(
            capture_id=capture_id,
            owner_id=principal.owner_id,
            profile_id=turn.profile_id,
            conversation_id=turn.conversation_id,
            source_turn_id=turn.source_turn_id,
            metadata=metadata,
            status=status,
            outcomes=(),
            failure_code=failure_code,
            created_at=created_at,
            completed_at=self._clock(),
            was_reprocessed=was_reprocessed,
            event_id=turn.event_id,
            contract_version=turn.contract_version,
            payload_fingerprint=turn.payload_fingerprint,
        )
        committed = self._repository.commit_capture(
            principal,
            CaptureWrite(result=result),
        )
        log_event(
            _LOGGER,
            logging.WARNING,
            "memory.capture.incomplete",
            capture_id=committed.capture_id,
            owner_ref=stable_reference(principal.owner_id),
            profile_id=turn.profile_id,
            status=committed.status.value,
            failure_code=committed.failure_code,
            was_reprocessed=was_reprocessed,
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
        )
        return committed


def _has_processable_content(value: str) -> bool:
    """去掉脱敏标记后判断是否还剩可处理内容，避免对空文本调用模型抽取。"""
    without_markers = _REDACTION_MARKER.sub("", value)
    return bool(without_markers.strip(" \t\r\n,，。.!！?？;；:："))


def _validation_errors(exc: BaseException) -> str | None:
    """从异常及其 ``__cause__`` 链提取校验摘要（field: reason）。

    优先级：``InvalidModelOutputError.context``（结构化违规信息，如
    ``{"field": "confidence", "value": 1.5}``）→ pydantic ``ValidationError.errors()``
    （经 ``raise ... from`` 包装时位于 ``__cause__``）→ 异常消息兜底。

    开发阶段（recommend.md §0 已放开完整内容日志）需暴露具体失败字段，
    避免 ``memory.capture.invalid_output.error_detail`` 恒为 null。
    """

    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        # 1) InvalidModelOutputError.context（结构化）
        context = getattr(current, "context", None)
        if isinstance(context, dict) and context:
            parts = [f"{key}={value!r}" for key, value in context.items()]
            return " | ".join(parts)
        # 2) pydantic ValidationError.errors()
        errors = getattr(current, "errors", None)
        if callable(errors):
            try:
                items: list[dict[str, Any]] = list(errors())
            except Exception:  # 防御性：不阻断日志主路径
                items = []
            if items:
                summary = " | ".join(
                    f"{'.'.join(str(part) for part in item.get('loc', ()) or '<root>')}: "
                    f"{str(item.get('msg', '')).strip()}"
                    for item in items[:5]
                )
                if summary:
                    return summary
        # 3) 异常消息兜底（直接 raise 无 cause 的路径）
        message = str(current).strip()
        if message:
            return message
        current = current.__cause__
    return None


def _relation_endpoint_records(
    repository: MemoryRepository,
    principal: PrincipalContext,
    processed: CandidateProcessingResult,
) -> tuple[MemoryRecord, ...]:
    """收集本轮产生的新增/重复/替换记忆目标，供关系规划阶段优先作为端点使用。"""

    records = list(processed.memories)
    for duplicate in processed.duplicate_evidence:
        current = repository.get(principal, duplicate.memory_id)
        if current is not None:
            records.append(current)
    for replacement in processed.replacements:
        current = repository.get(principal, replacement.memory_id)
        if current is not None:
            records.append(
                MemoryRecord(
                    item=current.item,
                    current_revision=replacement.revision,
                    evidence=replacement.evidence,
                )
            )
    return tuple(records)


class _KeyedLocks:
    """按捕获键引用计数的锁池，用于串行化同进程内同一轮次的重叠捕获。"""

    def __init__(self) -> None:
        self._guard = Lock()
        self._entries: dict[tuple[str, ...], tuple[Lock, int]] = {}

    @contextmanager
    def hold(self, key: tuple[str, ...]) -> Iterator[None]:
        with self._guard:
            lock, references = self._entries.get(key, (Lock(), 0))
            self._entries[key] = (lock, references + 1)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            with self._guard:
                current_lock, references = self._entries[key]
                if references == 1:
                    del self._entries[key]
                else:
                    self._entries[key] = (current_lock, references - 1)
