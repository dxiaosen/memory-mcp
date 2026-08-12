"""捕获流程编排：协调候选抽取、敏感内容校验、准入与原子写入，并暴露待确认门面。"""

import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
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
    CandidateProposal,
    CaptureOutcome,
    CaptureReprocessResult,
    CaptureResult,
    CaptureStatus,
    EvidenceSourceType,
    ExtractionMetadata,
    MemoryRecord,
    MessageRole,
    PrincipalContext,
    ReviewItem,
    TurnEnvelope,
    TurnMessage,
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
    CaptureEnqueueWrite,
    CaptureWrite,
    EmbeddingProvider,
    ExtractionRequest,
    MemoryProfile,
    MemoryRepository,
    PendingCapture,
    ProfileRegistry,
    RelationExtractor,
    SensitiveContentGuard,
    SensitiveInspection,
    profile_fingerprint,
)
from memory_mcp.core.support import log_content_event, log_event, stable_reference

_LOGGER = logging.getLogger(__name__)
_REDACTION_MARKER = re.compile(r"\[REDACTED:[^\]]+\]")
# 结构化抽取失败时的有界重试上限。仅对可恢复的模型结构错误
# （null/parse/schema/validation）重试，不对业务校验（invalid_source_expression）重试。
_EXTRACTION_MAX_ATTEMPTS = 3


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

    def enqueue_capture(
        self,
        principal: PrincipalContext,
        turn: TurnEnvelope,
    ) -> CaptureResult:
        """入队路径（同步、毫秒级）：幂等检查 + 敏感校验 + 写 PENDING 行，不做模型抽取。

        content/subject_hint 在入队前经 ``sensitive_guard.inspect`` 脱敏后入库，
        worker 读到的就是已脱敏原文，与现有抽取路径一致。返回 ``status=PENDING``
        的 ``CaptureResult`` 供 capture 工具立即回执。
        """

        guard = self._sensitive_guard
        if guard is None:
            raise CaptureNotConfiguredError("sensitive guard is required")
        extractor = self._candidate_extractor
        profile = self._profile_registry.get(turn.profile_id)
        metadata = ExtractionMetadata(
            model_id=extractor.model_id if extractor is not None else "",
            prompt_version=extractor.prompt_version if extractor is not None else "",
            schema_version=extractor.schema_version if extractor is not None else "",
            profile_version=profile.profile_version,
            profile_fingerprint=profile_fingerprint(profile),
        )
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
                raise IdempotencyConflictError(
                    "event identifier was reused with a different payload"
                )
            if existing is not None:
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
            capture_id = self._id_factory()
            now = self._clock()
            inspection = guard.inspect(turn.content)
            subject_hint_redacted: str | None = None
            if turn.subject_hint is not None:
                subject_hint_redacted = guard.inspect(
                    turn.subject_hint
                ).redacted_text
            result = CaptureResult(
                capture_id=capture_id,
                owner_id=principal.owner_id,
                profile_id=turn.profile_id,
                conversation_id=turn.conversation_id,
                source_turn_id=turn.source_turn_id,
                metadata=metadata,
                status=CaptureStatus.PENDING,
                outcomes=(),
                created_at=now,
                completed_at=now,
                event_id=turn.event_id,
                contract_version=turn.contract_version,
                payload_fingerprint=turn.payload_fingerprint,
            )
            committed = self._repository.commit_capture_enqueue(
                principal,
                CaptureEnqueueWrite(
                    result=result,
                    content=inspection.redacted_text,
                    subject_hint=subject_hint_redacted,
                ),
            )
            log_event(
                _LOGGER,
                logging.INFO,
                "memory.capture.enqueued",
                capture_id=committed.capture_id,
                owner_ref=stable_reference(principal.owner_id),
                event_id=turn.event_id,
                status=committed.status.value,
            )
            return committed

    def run_capture_reprocess(
        self,
        *,
        batch_limit: int = 20,
    ) -> CaptureReprocessResult:
        """worker 入口：捞一批 PENDING capture，逐条异步抽取并提交终态。

        每条 PendingCapture 在同进程锁内重建 ``TurnEnvelope`` 后走与同步路径
        相同的 ``_capture_turn_locked``；``commit_capture`` 把 PENDING 行覆盖为
        COMPLETED/REPROCESS_REQUIRED。返回 ``has_more`` 供后台循环续批。
        """

        if self._candidate_extractor is None or self._sensitive_guard is None:
            raise CaptureNotConfiguredError(
                "candidate extractor and sensitive guard are required"
            )
        pending = self._repository.list_pending_captures(limit=batch_limit)
        processed = completed = reprocess_required = failed = 0
        for item in pending:
            principal = PrincipalContext(owner_id=item.owner_id)
            turn = self._pending_to_turn(item)
            try:
                result = self._capture_turn_locked(principal, turn)
            except (
                InvalidMemoryTypeError,
                InvalidModelOutputError,
                InvalidProfileProgressError,
                ValueError,
            ):
                reprocess_required += 1
                processed += 1
                continue
            except Exception:
                failed += 1
                processed += 1
                continue
            if result.status is CaptureStatus.COMPLETED:
                completed += 1
            elif result.status is CaptureStatus.REPROCESS_REQUIRED:
                reprocess_required += 1
            else:
                failed += 1
            processed += 1
        has_more = len(pending) >= batch_limit
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.capture.reprocess.completed",
            processed_count=processed,
            completed_count=completed,
            reprocess_required_count=reprocess_required,
            failed_count=failed,
            has_more=has_more,
        )
        return CaptureReprocessResult(
            processed_count=processed,
            completed_count=completed,
            reprocess_required_count=reprocess_required,
            failed_count=failed,
            has_more=has_more,
        )

    def _pending_to_turn(self, item: PendingCapture) -> TurnEnvelope:
        """把 PENDING 行的已脱敏 content 重建为 ``TurnEnvelope``。

        content 由 ``CompletedTurnInputV1.to_turn_envelope`` 确定性拼成
        ``[user]\\n{user_input}\\n\\n[assistant]\\n{final_output}``（其后可附
        ``[document:<i>]`` 段），此处反解回 ``[user, assistant, ...document]``
        ``TurnMessage``，使 worker 抽取路径与同步路径一样拥有可信 messages——
        ``_source_metadata`` 才能据此给候选标注精确的 ``source_role`` /
        ``source_message_id`` / document provenance，否则全部退化为 None。
        """

        return TurnEnvelope(
            profile_id=item.profile_id,
            conversation_id=item.conversation_id,
            source_turn_id=item.source_turn_id,
            content=item.content,
            observed_at=item.observed_at,
            subject_hint=item.subject_hint,
            event_id=item.event_id,
            contract_version=item.contract_version,
            payload_fingerprint=item.payload_fingerprint,
            messages=_split_capture_content(item.content, item.source_turn_id),
        )

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
        if existing is not None and existing.status not in (
            CaptureStatus.REPROCESS_REQUIRED,
            CaptureStatus.PENDING,
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
            proposals: tuple[CandidateProposal, ...] = ()
            _extraction_duration = 0.0
            if _has_processable_content(inspection.redacted_text):
                _extraction_started_at = perf_counter()
                proposals = self._extract_candidates(
                    capture_id,
                    extractor,
                    profile=profile,
                    turn=turn,
                    inspection=inspection,
                    subject_hint_inspection=subject_hint_inspection,
                )
                _extraction_duration = perf_counter() - _extraction_started_at
            processed = processor.process(
                principal,
                turn,
                proposals,
                redacted_source=inspection.redacted_text,
                initial_outcomes=initial_outcomes,
            )
            relation_plan = AutomaticRelationPlan()
            _relation_duration = 0.0
            if self._relation_planner is not None and _has_processable_content(
                inspection.redacted_text
            ):
                _relation_started_at = perf_counter()
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
                _relation_duration = perf_counter() - _relation_started_at
            log_content_event(
                "memory.capture.candidates",
                capture_id=capture_id,
                candidates=tuple(
                    asdict(candidate) for candidate in processed.candidates
                ),
            )
            log_content_event(
                "memory.capture.validation",
                capture_id=capture_id,
                extracted_candidate_count=len(proposals),
                validated_candidate_count=len(processed.candidates),
                rejected=tuple(asdict(r) for r in processed.rejected_proposals),
            )
            log_content_event(
                "memory.capture.admission",
                capture_id=capture_id,
                outcomes=tuple(asdict(outcome) for outcome in processed.outcomes),
            )
            if self._relation_planner is not None and _relation_duration > 0:
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
        _persistence_started_at = perf_counter()
        committed_relations = relation_plan.relations
        try:
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
        except Exception as exc:
            if not relation_plan.relations:
                raise
            # Relation 写入失败（端点失效 / 约束等）-> best-effort：放弃 relation，
            # 仅提交 Candidate 主链（Relation 不参与主 Capture 原子边界）。
            log_event(
                _LOGGER,
                logging.WARNING,
                "memory.capture.relation_commit_failed",
                capture_id=capture_id,
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
                candidate_persistence_preserved=True,
            )
            committed = self._repository.commit_capture(
                principal,
                CaptureWrite(
                    result=result,
                    memories=processed.memories,
                    reviews=processed.reviews,
                    duplicate_evidence=processed.duplicate_evidence,
                    replacements=processed.replacements,
                    relations=(),
                ),
            )
            committed_relations = ()
        _persistence_duration = perf_counter() - _persistence_started_at
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
                tuple(asdict(relation) for relation in committed_relations)
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
            extracted_candidate_count=len(proposals),
            outcome_count=len(committed.outcomes),
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
            relation_accepted_count=len(committed_relations),
            relation_skipped_count=relation_plan.skipped_count,
            failure_code=committed.failure_code,
            candidate_extraction_duration_ms=round(_extraction_duration * 1000, 3),
            candidate_validation_duration_ms=round(
                (processed.timing or {}).get(
                    "candidate_validation_duration_ms", 0.0
                ),
                3,
            ),
            admission_duration_ms=round(
                (processed.timing or {}).get("admission_duration_ms", 0.0), 3
            ),
            lifecycle_duration_ms=round(
                (processed.timing or {}).get("lifecycle_duration_ms", 0.0), 3
            ),
            relation_duration_ms=round(_relation_duration * 1000, 3),
            persistence_duration_ms=round(_persistence_duration * 1000, 3),
        )
        return committed

    def _extract_candidates(
        self,
        capture_id: UUID,
        extractor: CandidateExtractor,
        *,
        profile: MemoryProfile,
        turn: TurnEnvelope,
        inspection: SensitiveInspection,
        subject_hint_inspection: SensitiveInspection,
    ) -> tuple[CandidateProposal, ...]:
        """对结构化抽取做有界重试。

        仅对 ``InvalidModelOutputError``（null/parse/schema/validation 等可恢复结构错误）
        重试，最多 ``_EXTRACTION_MAX_ATTEMPTS`` 次。每次 attempt 记 started/failed/completed
        事件。所有 attempt 失败则向上抛出，由调用方既有 except 写
        ``memory.capture.incomplete`` + ``invalid_candidate_output``。重试在同一 Capture 内，
        不产生重复 Capture/Memory。业务校验（invalid_source_expression）在后续 candidate_processing
        产生 discard，不在此重试。
        """

        request = ExtractionRequest(
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
        for attempt in range(1, _EXTRACTION_MAX_ATTEMPTS + 1):
            _attempt_started_at = perf_counter()
            log_event(
                _LOGGER,
                logging.INFO,
                "memory.capture.extraction_attempt.started",
                capture_id=capture_id,
                attempt=attempt,
                max_attempts=_EXTRACTION_MAX_ATTEMPTS,
            )
            try:
                proposals = extractor.extract(request)
            except InvalidModelOutputError as exc:
                retryable = attempt < _EXTRACTION_MAX_ATTEMPTS
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    "memory.capture.extraction_attempt.failed",
                    capture_id=capture_id,
                    attempt=attempt,
                    max_attempts=_EXTRACTION_MAX_ATTEMPTS,
                    duration_ms=round(
                        (perf_counter() - _attempt_started_at) * 1000, 3
                    ),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    retryable=retryable,
                )
                if retryable:
                    continue
                raise
            log_event(
                _LOGGER,
                logging.INFO,
                "memory.capture.extraction_attempt.completed",
                capture_id=capture_id,
                attempt=attempt,
                max_attempts=_EXTRACTION_MAX_ATTEMPTS,
                duration_ms=round((perf_counter() - _attempt_started_at) * 1000, 3),
            )
            return proposals
        raise InvalidModelOutputError("structured candidate output is invalid")

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


@dataclass(frozen=True, slots=True)
class DocumentSegment:
    """``[document:<i>]`` 段反解结果：meta + 正文。"""

    meta: dict[str, Any]
    content: str


def _split_capture_content(
    content: str,
    source_turn_id: str,
) -> tuple[TurnMessage, ...]:
    """反解 ``[user]/[assistant]/[document:*]`` 段为 ``TurnMessage`` 序列。

    与 ``CompletedTurnInputV1.to_turn_envelope`` 的拼接格式对偶。content 是
    入队前 ``sensitive_guard.inspect`` 脱敏后的原文，``[user]``/``[assistant]``/
    ``[document]`` 标记由服务器加在脱敏之前，不受脱敏影响，反解可靠。匹配不到
    ``[user]`` 标记时返回空元组——降级为无 messages 的旧行为，不阻断抽取。

    ``[document:<i>]`` 段格式为 ``\\n\\n[document:<i>]\\n{meta_json}\\n{content}``，
    其中 ``meta_json`` 单行携带 tool_name/source_type/source_uri/source_title/
    source_publisher/published_at/retrieved_at/content_hash/citation_locator/
    message_id。无 document 段时仅返回 user/assistant 两条（旧行为）。
    """

    user_text, assistant_text, doc_segments = _parse_capture_roles(content)
    messages: list[TurnMessage] = []
    if user_text is not None:
        messages.append(
            TurnMessage(
                role=MessageRole.USER,
                content=user_text,
                message_id=f"{source_turn_id}:user",
            )
        )
    if assistant_text is not None:
        messages.append(
            TurnMessage(
                role=MessageRole.ASSISTANT,
                content=assistant_text,
                message_id=f"{source_turn_id}:assistant",
            )
        )
    for doc in doc_segments:
        messages.append(_document_message(doc, source_turn_id))
    return tuple(messages)


_CAPTURE_USER_PREFIX = "[user]\n"
_CAPTURE_ASSISTANT_PREFIX = "\n\n[assistant]\n"
_CAPTURE_DOCUMENT_PREFIX = "\n\n[document:"


def _parse_capture_roles(
    content: str,
) -> tuple[str | None, str | None, list[DocumentSegment]]:
    """拆出 user/assistant 正文与 document 段；任一缺失返回对应 None/空。"""

    if not content.startswith(_CAPTURE_USER_PREFIX):
        return None, None, []
    rest = content[len(_CAPTURE_USER_PREFIX) :]
    assistant_boundary = rest.find(_CAPTURE_ASSISTANT_PREFIX)
    if assistant_boundary == -1:
        return (rest or None), None, []
    user_text = rest[:assistant_boundary]
    after_assistant = rest[assistant_boundary + len(_CAPTURE_ASSISTANT_PREFIX) :]
    # assistant 正文到第一个 [document: 段为止（如有）。
    doc_boundary = after_assistant.find(_CAPTURE_DOCUMENT_PREFIX)
    if doc_boundary == -1:
        return (user_text or None), (after_assistant or None), []
    assistant_text = after_assistant[:doc_boundary]
    doc_tail = after_assistant[doc_boundary:]
    return (user_text or None), (assistant_text or None), _parse_document_segments(
        doc_tail
    )


def _parse_document_segments(tail: str) -> list[DocumentSegment]:
    """从 ``\\n\\n[document:<i>]\\n{meta_json}\\n{content}`` 序列解析出段。

    每段以 ``\\n\\n[document:`` 起始，头部 ``[document:<i>]`` 后紧跟一个 ``\\n``，
    再接单行 meta_json，再接 ``\\n`` 与正文。正文延续到下一个 ``\\n\\n[document:``
    或字符串结尾。坏结构（缺 ``]`` / 缺换行 / 坏 JSON）只跳过该段，不阻断其余段。
    """

    segments: list[DocumentSegment] = []
    cursor = 0
    while True:
        idx = tail.find(_CAPTURE_DOCUMENT_PREFIX, cursor)
        if idx == -1:
            break
        # idx 指向 "\n\n[document:" 的第一个 "\n"；定位头部 "]"。
        bracket_close = tail.find("]", idx)
        if bracket_close == -1:
            break
        # meta 行："]" 后第一个 "\n" 之后到下一个 "\n"。
        meta_line_start = tail.find("\n", bracket_close)
        if meta_line_start == -1:
            break
        meta_line_end = tail.find("\n", meta_line_start + 1)
        if meta_line_end == -1:
            # 只有 meta 行、无正文：视为空内容。
            meta_json = tail[meta_line_start + 1 :]
            segments.append(DocumentSegment(meta=_safe_meta(meta_json), content=""))
            break
        meta_json = tail[meta_line_start + 1 : meta_line_end]
        # 正文到下一个 [document: 段（或字符串结尾）。
        next_seg = tail.find(_CAPTURE_DOCUMENT_PREFIX, meta_line_end + 1)
        if next_seg == -1:
            doc_content = tail[meta_line_end + 1 :]
            segments.append(
                DocumentSegment(meta=_safe_meta(meta_json), content=doc_content)
            )
            break
        doc_content = tail[meta_line_end + 1 : next_seg]
        # 去掉段尾的 "\n\n"（与下一段前缀的分隔）。
        doc_content = doc_content.removesuffix("\n\n")
        segments.append(
            DocumentSegment(meta=_safe_meta(meta_json), content=doc_content)
        )
        cursor = next_seg
    return segments


def _safe_meta(meta_json: str) -> dict[str, Any]:
    """容忍解析失败：坏 meta 行视为空字典，不阻断反解。"""

    try:
        parsed = json.loads(meta_json)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _document_message(doc: DocumentSegment, source_turn_id: str) -> TurnMessage:
    """把 document 段构造为带 provenance 的 ``TurnMessage``。"""

    meta = doc.meta
    return TurnMessage(
        role=MessageRole.TOOL,
        content=doc.content,
        message_id=(
            meta.get("message_id")
            or f"{source_turn_id}:document"
        ),
        tool_name=meta.get("tool_name"),
        source_type=_meta_source_type(meta.get("source_type")),
        source_uri=meta.get("source_uri"),
        source_title=meta.get("source_title"),
        source_publisher=meta.get("source_publisher"),
        published_at=_meta_datetime(meta.get("published_at")),
        retrieved_at=_meta_datetime(meta.get("retrieved_at")),
        content_hash=meta.get("content_hash"),
        citation_locator=meta.get("citation_locator"),
    )


def _meta_source_type(value: Any) -> EvidenceSourceType | None:
    if isinstance(value, str):
        try:
            return EvidenceSourceType(value)
        except ValueError:
            return None
    return None


def _meta_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _validation_errors(exc: BaseException) -> str | None:
    """从异常及其 ``__cause__`` 链提取校验摘要（field: reason）。

    优先级：``InvalidModelOutputError.context``（结构化违规信息，如
    ``{"field": "confidence", "value": 1.5}``）→ pydantic ``ValidationError.errors()``
    （经 ``raise ... from`` 包装时位于 ``__cause__``）→ 异常消息兜底。

    开发阶段需暴露具体失败字段（开发阶段已放开完整内容日志），
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
