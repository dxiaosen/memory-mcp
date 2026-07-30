"""阶段二捕获、准入和待确认用例协调器。"""

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from memory_mcp.core.application.admission import (
    AdmissionOutcome,
    ConservativeAdmissionPolicy,
)
from memory_mcp.core.domain import (
    AdmissionDecision,
    Candidate,
    CaptureOutcome,
    CaptureResult,
    CaptureStatus,
    Evidence,
    ExtractionMetadata,
    LifecycleStatus,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    MessageRole,
    PrincipalContext,
    ReviewItem,
    ReviewStatus,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.core.exceptions import (
    CaptureNotConfiguredError,
    IdempotencyConflictError,
    InvalidMemoryTypeError,
    InvalidModelOutputError,
    InvalidScenarioProgressError,
    ReviewNotFoundError,
)
from memory_mcp.core.ports import (
    CandidateExtractor,
    CaptureWrite,
    ExtractionRequest,
    MemoryRepository,
    ScenarioRegistry,
    SensitiveContentGuard,
)
from memory_mcp.logging import log_event, stable_reference

_LOGGER = logging.getLogger(__name__)
_REDACTION_MARKER = re.compile(r"\[REDACTED:[^\]]+\]")


class CaptureService:
    """协调结构化抽取、准入、幂等提交和 pending 用户操作。"""

    def __init__(
        self,
        repository: MemoryRepository,
        scenario_registry: ScenarioRegistry,
        *,
        candidate_extractor: CandidateExtractor | None,
        sensitive_guard: SensitiveContentGuard | None,
        admission_policy: ConservativeAdmissionPolicy,
        id_factory: Callable[[], UUID],
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._scenario_registry = scenario_registry
        self._candidate_extractor = candidate_extractor
        self._sensitive_guard = sensitive_guard
        self._admission_policy = admission_policy
        self._id_factory = id_factory
        self._clock = clock

    def capture_turn(
        self,
        principal: PrincipalContext,
        turn: TurnEnvelope,
    ) -> CaptureResult:
        """同步捕获一轮会话，并原子提交四类互斥准入结果。"""

        extractor = self._candidate_extractor
        guard = self._sensitive_guard
        if extractor is None or guard is None:
            raise CaptureNotConfiguredError(
                "candidate extractor and sensitive guard are required"
            )
        policy = self._scenario_registry.get(turn.scenario)
        metadata = ExtractionMetadata(
            model_id=extractor.model_id,
            prompt_version=extractor.prompt_version,
            schema_version=extractor.schema_version,
            policy_version=policy.policy_version,
        )
        existing = self._repository.get_capture(
            principal,
            scenario=turn.scenario,
            conversation_id=turn.conversation_id,
            source_turn_id=turn.source_turn_id,
            policy_version=policy.policy_version,
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
        if existing is not None and existing.status is not (
            CaptureStatus.REPROCESS_REQUIRED
        ):
            return replace(existing, replayed=True)

        capture_id = existing.capture_id if existing is not None else self._id_factory()
        created_at = existing.created_at if existing is not None else self._clock()
        was_reprocessed = existing is not None
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.capture.started",
            capture_id=capture_id,
            owner_ref=stable_reference(principal.owner_id),
            policy_version=policy.policy_version,
            scenario=turn.scenario,
            was_reprocessed=was_reprocessed,
        )

        try:
            inspection = guard.inspect(turn.content)
            subject_hint_inspection = guard.inspect(turn.subject_hint or "")
            outcomes: list[CaptureOutcome] = [
                CaptureOutcome(
                    candidate_id=self._id_factory(),
                    decision=AdmissionDecision.BLOCKED,
                    reason_code=f"sensitive_{category}",
                )
                for category in dict.fromkeys(
                    (*inspection.categories, *subject_hint_inspection.categories)
                )
            ]
            memories: list[MemoryRecord] = []
            reviews: list[ReviewItem] = []
            proposals = ()
            if _has_processable_content(inspection.redacted_text):
                proposals = extractor.extract(
                    ExtractionRequest(
                        scenario=turn.scenario,
                        conversation_id=turn.conversation_id,
                        source_turn_id=turn.source_turn_id,
                        content=inspection.redacted_text,
                        observed_at=turn.observed_at,
                        allowed_memory_types=policy.memory_types,
                        capture_guidance=policy.capture_guidance,
                        policy_version=policy.policy_version,
                        subject_hint=(
                            subject_hint_inspection.redacted_text
                            if turn.subject_hint is not None
                            else None
                        ),
                    )
                )

            for proposal in proposals:
                candidate_id = self._id_factory()
                if proposal.source_expression not in inspection.redacted_text:
                    raise InvalidModelOutputError(
                        "source_expression must occur in the redacted source turn"
                    )
                self._scenario_registry.validate_memory_type(
                    turn.scenario,
                    proposal.memory_type,
                )
                self._scenario_registry.validate_business_progress(
                    turn.scenario,
                    proposal.business_progress,
                )
                source_metadata = _source_metadata(
                    turn,
                    proposal.source_expression,
                    guard,
                )
                if turn.messages and source_metadata["source_role"] is None:
                    raise InvalidModelOutputError(
                        "source_expression must occur in one submitted message"
                    )
                candidate_sensitive = guard.inspect(
                    "\n".join(
                        value
                        for value in (
                            proposal.subject,
                            proposal.memory_type,
                            proposal.content,
                            proposal.source_expression,
                            proposal.save_rationale,
                            proposal.business_progress,
                            proposal.original_time_expression,
                            source_metadata["source_message_id"],
                            source_metadata["source_tool_name"],
                        )
                        if isinstance(value, str)
                    )
                )
                if candidate_sensitive.was_redacted:
                    outcomes.append(
                        CaptureOutcome(
                            candidate_id=candidate_id,
                            decision=AdmissionDecision.BLOCKED,
                            reason_code="sensitive_candidate_text",
                        )
                    )
                    continue
                candidate = Candidate(
                    candidate_id=candidate_id,
                    owner_id=principal.owner_id,
                    scenario=turn.scenario,
                    subject=proposal.subject,
                    memory_type=proposal.memory_type,
                    content=proposal.content,
                    assertion_kind=proposal.assertion_kind,
                    conversation_id=turn.conversation_id,
                    source_turn_id=turn.source_turn_id,
                    source_expression=proposal.source_expression,
                    save_rationale=proposal.save_rationale,
                    confidence=proposal.confidence,
                    durability=proposal.durability,
                    expression_basis=proposal.expression_basis,
                    observed_at=turn.observed_at,
                    created_at=self._clock(),
                    business_progress=proposal.business_progress,
                    original_time_expression=proposal.original_time_expression,
                    normalized_time=proposal.normalized_time,
                    **source_metadata,
                )
                admission = self._admission_policy.decide(candidate)
                if (
                    admission.decision is AdmissionDecision.AUTO_SAVE
                    and turn.messages
                    and candidate.source_role is not MessageRole.USER
                ):
                    admission = AdmissionOutcome(
                        AdmissionDecision.PENDING,
                        "non_user_source",
                    )
                if admission.decision is AdmissionDecision.AUTO_SAVE:
                    memory = self._record_from_candidate(candidate)
                    memories.append(memory)
                    outcomes.append(
                        CaptureOutcome(
                            candidate_id=candidate.candidate_id,
                            decision=admission.decision,
                            reason_code=admission.reason_code,
                            memory_id=memory.item.memory_id,
                        )
                    )
                elif admission.decision is AdmissionDecision.PENDING:
                    review = ReviewItem(
                        review_id=self._id_factory(),
                        candidate=candidate,
                        status=ReviewStatus.PENDING,
                        created_at=self._clock(),
                    )
                    reviews.append(review)
                    outcomes.append(
                        CaptureOutcome(
                            candidate_id=candidate.candidate_id,
                            decision=admission.decision,
                            reason_code=admission.reason_code,
                            review_id=review.review_id,
                        )
                    )
                else:
                    outcomes.append(
                        CaptureOutcome(
                            candidate_id=candidate.candidate_id,
                            decision=admission.decision,
                            reason_code=admission.reason_code,
                        )
                    )
        except (
            InvalidMemoryTypeError,
            InvalidModelOutputError,
            InvalidScenarioProgressError,
            ValueError,
        ):
            return self._commit_capture_failure(
                principal,
                turn,
                capture_id=capture_id,
                created_at=created_at,
                metadata=metadata,
                status=CaptureStatus.FAILED,
                failure_code="invalid_candidate_output",
                was_reprocessed=was_reprocessed,
            )
        except Exception as exc:
            log_event(
                _LOGGER,
                logging.ERROR,
                "memory.capture.processing_failed",
                capture_id=capture_id,
                error_type=type(exc).__name__,
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
            )

        result = CaptureResult(
            capture_id=capture_id,
            owner_id=principal.owner_id,
            scenario=turn.scenario,
            conversation_id=turn.conversation_id,
            source_turn_id=turn.source_turn_id,
            metadata=metadata,
            status=CaptureStatus.COMPLETED,
            outcomes=tuple(outcomes),
            created_at=created_at,
            completed_at=self._clock(),
            was_reprocessed=was_reprocessed,
            event_id=turn.event_id,
            contract_version=turn.contract_version,
            payload_fingerprint=turn.payload_fingerprint,
        )
        self._repository.commit_capture(
            principal,
            CaptureWrite(
                result=result,
                memories=tuple(memories),
                reviews=tuple(reviews),
            ),
        )
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.capture.completed",
            auto_saved_count=sum(
                outcome.decision is AdmissionDecision.AUTO_SAVE for outcome in outcomes
            ),
            blocked_count=sum(
                outcome.decision is AdmissionDecision.BLOCKED for outcome in outcomes
            ),
            capture_id=capture_id,
            discarded_count=sum(
                outcome.decision is AdmissionDecision.DISCARD for outcome in outcomes
            ),
            owner_ref=stable_reference(principal.owner_id),
            pending_count=sum(
                outcome.decision is AdmissionDecision.PENDING for outcome in outcomes
            ),
        )
        return result

    def list_pending_reviews(
        self,
        principal: PrincipalContext,
    ) -> Sequence[ReviewItem]:
        """列出当前用户尚未处理的候选，内容与活动记忆隔离。"""

        return self._repository.list_reviews(
            principal,
            status=ReviewStatus.PENDING,
        )

    def get_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem:
        """读取当前用户拥有的候选确认项。"""

        review = self._repository.get_review(principal, review_id)
        if review is None:
            raise ReviewNotFoundError("review is unavailable")
        return review

    def confirm_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> MemoryRecord:
        """确认 pending 候选，并与活动记忆写入原子提交。"""

        review = self._repository.get_review(principal, review_id)
        if review is None:
            raise ReviewNotFoundError("review is unavailable")
        if review.status is ReviewStatus.CONFIRMED:
            if review.resolved_memory_id is None:
                raise ReviewNotFoundError("review is unavailable")
            memory = self._repository.get(principal, review.resolved_memory_id)
            if memory is None:
                raise ReviewNotFoundError("review is unavailable")
            return memory
        if review.status is not ReviewStatus.PENDING:
            raise ReviewNotFoundError("review is unavailable")
        self._scenario_registry.validate_memory_type(
            review.candidate.scenario,
            review.candidate.memory_type,
        )
        self._scenario_registry.validate_business_progress(
            review.candidate.scenario,
            review.candidate.business_progress,
        )
        memory = self._record_from_candidate(review.candidate)
        resolved = self._repository.resolve_review(
            principal,
            review_id,
            status=ReviewStatus.CONFIRMED,
            decided_at=self._clock(),
            memory=memory,
        )
        if resolved is None:
            raise ReviewNotFoundError("review is unavailable")
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.review.confirmed",
            memory_id=memory.item.memory_id,
            owner_ref=stable_reference(principal.owner_id),
            review_id=review_id,
        )
        return memory

    def reject_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem:
        """拒绝 pending 候选，且不创建活动记忆。"""

        existing = self._repository.get_review(principal, review_id)
        if existing is None:
            raise ReviewNotFoundError("review is unavailable")
        if existing.status is ReviewStatus.REJECTED:
            return existing
        if existing.status is not ReviewStatus.PENDING:
            raise ReviewNotFoundError("review is unavailable")
        resolved = self._repository.resolve_review(
            principal,
            review_id,
            status=ReviewStatus.REJECTED,
            decided_at=self._clock(),
        )
        if resolved is None:
            raise ReviewNotFoundError("review is unavailable")
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.review.rejected",
            owner_ref=stable_reference(principal.owner_id),
            review_id=review_id,
        )
        return resolved

    def _record_from_candidate(self, candidate: Candidate) -> MemoryRecord:
        memory_id = self._id_factory()
        revision_id = self._id_factory()
        created_at = self._clock()
        return MemoryRecord(
            item=MemoryItem(
                memory_id=memory_id,
                owner_id=candidate.owner_id,
                scenario=candidate.scenario,
                subject=candidate.subject,
                memory_type=candidate.memory_type,
                created_at=created_at,
            ),
            current_revision=MemoryRevision(
                revision_id=revision_id,
                memory_id=memory_id,
                owner_id=candidate.owner_id,
                revision_number=1,
                content=candidate.content,
                assertion_kind=candidate.assertion_kind,
                lifecycle_status=LifecycleStatus.ACTIVE,
                business_progress=candidate.business_progress,
                save_rationale=candidate.save_rationale,
                observed_at=candidate.observed_at,
                created_at=created_at,
                original_time_expression=candidate.original_time_expression,
                normalized_time=candidate.normalized_time,
            ),
            evidence=(
                Evidence(
                    evidence_id=self._id_factory(),
                    memory_id=memory_id,
                    revision_id=revision_id,
                    owner_id=candidate.owner_id,
                    conversation_id=candidate.conversation_id,
                    source_turn_id=candidate.source_turn_id,
                    source_expression=candidate.source_expression,
                    observed_at=candidate.observed_at,
                    created_at=created_at,
                    source_role=candidate.source_role,
                    source_message_id=candidate.source_message_id,
                    source_tool_name=candidate.source_tool_name,
                ),
            ),
        )

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
    ) -> CaptureResult:
        result = CaptureResult(
            capture_id=capture_id,
            owner_id=principal.owner_id,
            scenario=turn.scenario,
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
        self._repository.commit_capture(
            principal,
            CaptureWrite(result=result),
        )
        log_event(
            _LOGGER,
            logging.WARNING,
            "memory.capture.incomplete",
            capture_id=capture_id,
            failure_code=failure_code,
            owner_ref=stable_reference(principal.owner_id),
            status=status.value,
        )
        return result


def _has_processable_content(value: str) -> bool:
    without_markers = _REDACTION_MARKER.sub("", value)
    return bool(without_markers.strip(" \t\r\n,，。.!！?？;；:："))


def _source_metadata(
    turn: TurnEnvelope,
    source_expression: str,
    guard: SensitiveContentGuard,
) -> dict[str, MessageRole | str | None]:
    """Derive source identity from trusted message blocks, never model fields."""

    matching: list[TurnMessage] = []
    for message in turn.messages:
        redacted = guard.inspect(message.content).redacted_text
        if source_expression in redacted:
            matching.append(message)
    if not matching:
        return {
            "source_role": None,
            "source_message_id": None,
            "source_tool_name": None,
        }
    selected = next(
        (message for message in matching if message.role is MessageRole.USER),
        matching[0],
    )
    return {
        "source_role": selected.role,
        "source_message_id": selected.message_id,
        "source_tool_name": selected.tool_name,
    }
