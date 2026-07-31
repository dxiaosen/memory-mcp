"""候选校验、准入和记忆写入实体化。"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from memory_mcp.core.application.admission import (
    AdmissionOutcome,
    ConservativeAdmissionPolicy,
)
from memory_mcp.core.domain import (
    AdmissionDecision,
    AssertionKind,
    Candidate,
    CandidateProposal,
    CaptureOutcome,
    Evidence,
    ExpressionBasis,
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
    normalize_memory_text,
)
from memory_mcp.core.exceptions import InvalidModelOutputError
from memory_mcp.core.ports import (
    DuplicateEvidenceWrite,
    MemoryRepository,
    ReplacementWrite,
    ScenarioRegistry,
    SensitiveContentGuard,
)

_EXPLICIT_REPLACEMENT = re.compile(
    r"(?:不再|不要再|改成|改为|换成|替换为|以后用|默认(?:改|换)|"
    r"\bno longer\b|\binstead\b|\breplace\b|\bnew default\b|"
    r"\bchange\b.+\bto\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CandidateProcessingResult:
    """一批候选产生的原子写入组成。"""

    candidates: tuple[Candidate, ...]
    outcomes: tuple[CaptureOutcome, ...]
    memories: tuple[MemoryRecord, ...]
    reviews: tuple[ReviewItem, ...]
    duplicate_evidence: tuple[DuplicateEvidenceWrite, ...]
    replacements: tuple[ReplacementWrite, ...]


class CandidateMaterializer:
    """根据可信 Candidate 构造不可变领域写入。"""

    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID],
        clock: Callable[[], datetime],
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock

    def record(self, candidate: Candidate) -> MemoryRecord:
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
                self._evidence(
                    candidate,
                    memory_id=memory_id,
                    revision_id=revision_id,
                    created_at=created_at,
                ),
            ),
        )

    def duplicate(
        self,
        target: MemoryRecord,
        candidate: Candidate,
    ) -> DuplicateEvidenceWrite:
        revision = target.current_revision
        return DuplicateEvidenceWrite(
            memory_id=target.item.memory_id,
            expected_revision_id=revision.revision_id,
            evidence=self._evidence(
                candidate,
                memory_id=target.item.memory_id,
                revision_id=revision.revision_id,
                created_at=self._clock(),
            ),
        )

    def replacement(
        self,
        target: MemoryRecord,
        candidate: Candidate,
    ) -> ReplacementWrite:
        old_revision = target.current_revision
        revision_id = self._id_factory()
        created_at = self._clock()
        revision = MemoryRevision(
            revision_id=revision_id,
            memory_id=target.item.memory_id,
            owner_id=candidate.owner_id,
            revision_number=old_revision.revision_number + 1,
            content=candidate.content,
            assertion_kind=candidate.assertion_kind,
            lifecycle_status=LifecycleStatus.ACTIVE,
            business_progress=candidate.business_progress,
            save_rationale=candidate.save_rationale,
            observed_at=candidate.observed_at,
            created_at=created_at,
            original_time_expression=candidate.original_time_expression,
            normalized_time=candidate.normalized_time,
        )
        return ReplacementWrite(
            memory_id=target.item.memory_id,
            expected_revision_id=old_revision.revision_id,
            revision=revision,
            evidence=(
                self._evidence(
                    candidate,
                    memory_id=target.item.memory_id,
                    revision_id=revision_id,
                    created_at=created_at,
                ),
            ),
        )

    def _evidence(
        self,
        candidate: Candidate,
        *,
        memory_id: UUID,
        revision_id: UUID,
        created_at: datetime,
    ) -> Evidence:
        return Evidence(
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
        )


class CandidateProcessor:
    """校验并分类模型建议，生成原子 Repository 写入。"""

    def __init__(
        self,
        repository: MemoryRepository,
        scenario_registry: ScenarioRegistry,
        sensitive_guard: SensitiveContentGuard,
        admission_policy: ConservativeAdmissionPolicy,
        materializer: CandidateMaterializer,
        *,
        id_factory: Callable[[], UUID],
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._scenario_registry = scenario_registry
        self._sensitive_guard = sensitive_guard
        self._admission_policy = admission_policy
        self._materializer = materializer
        self._id_factory = id_factory
        self._clock = clock

    def process(
        self,
        principal: PrincipalContext,
        turn: TurnEnvelope,
        proposals: Sequence[CandidateProposal],
        *,
        redacted_source: str,
        initial_outcomes: Sequence[CaptureOutcome] = (),
    ) -> CandidateProcessingResult:
        outcomes = list(initial_outcomes)
        candidates: list[Candidate] = []
        memories: list[MemoryRecord] = []
        reviews: list[ReviewItem] = []
        duplicate_evidence: list[DuplicateEvidenceWrite] = []
        replacements: list[ReplacementWrite] = []
        lifecycle_target_ids: set[UUID] = set()
        candidate_scopes: set[tuple[str, str]] = set()

        for proposal in proposals:
            candidate_id = self._id_factory()
            if proposal.source_expression not in redacted_source:
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
                self._sensitive_guard,
            )
            if turn.messages and source_metadata["source_role"] is None:
                raise InvalidModelOutputError(
                    "source_expression must occur in one submitted message"
                )
            candidate_sensitive = self._sensitive_guard.inspect(
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
            candidates.append(candidate)
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
            candidate_scope = (
                normalize_memory_text(candidate.subject),
                candidate.memory_type,
            )
            scope_already_seen = candidate_scope in candidate_scopes
            candidate_scopes.add(candidate_scope)
            if scope_already_seen:
                if admission.decision is AdmissionDecision.AUTO_SAVE:
                    admission = AdmissionOutcome(
                        AdmissionDecision.PENDING,
                        "multiple_candidates_same_scope",
                    )
                current_scope = ()
            else:
                current_scope = self._repository.find_current(
                    principal,
                    scenario=candidate.scenario,
                    subject=candidate.subject,
                    memory_type=candidate.memory_type,
                )
            target = current_scope[0] if len(current_scope) == 1 else None
            if len(current_scope) > 1:
                admission = AdmissionOutcome(
                    AdmissionDecision.PENDING,
                    "ambiguous_lifecycle_target",
                )
            elif target is not None and target.item.memory_id in lifecycle_target_ids:
                admission = AdmissionOutcome(
                    AdmissionDecision.PENDING,
                    "lifecycle_target_already_changed",
                )
            elif target is not None and (
                normalize_memory_text(target.current_revision.content)
                == normalize_memory_text(candidate.content)
            ):
                if admission.decision is AdmissionDecision.AUTO_SAVE:
                    duplicate_evidence.append(
                        self._materializer.duplicate(target, candidate)
                    )
                    lifecycle_target_ids.add(target.item.memory_id)
                    outcomes.append(
                        CaptureOutcome(
                            candidate_id=candidate.candidate_id,
                            decision=AdmissionDecision.AUTO_SAVE,
                            reason_code="duplicate_evidence_added",
                            memory_id=target.item.memory_id,
                        )
                    )
                    continue
            elif target is not None:
                if (
                    admission.decision is AdmissionDecision.AUTO_SAVE
                    and _is_explicit_replacement(candidate)
                ):
                    replacements.append(
                        self._materializer.replacement(target, candidate)
                    )
                    lifecycle_target_ids.add(target.item.memory_id)
                    outcomes.append(
                        CaptureOutcome(
                            candidate_id=candidate.candidate_id,
                            decision=AdmissionDecision.AUTO_SAVE,
                            reason_code="explicit_replacement",
                            memory_id=target.item.memory_id,
                        )
                    )
                    continue
                admission = AdmissionOutcome(
                    AdmissionDecision.PENDING,
                    "ambiguous_lifecycle_conflict",
                )
            if admission.decision is AdmissionDecision.AUTO_SAVE:
                memory = self._materializer.record(candidate)
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

        return CandidateProcessingResult(
            candidates=tuple(candidates),
            outcomes=tuple(outcomes),
            memories=tuple(memories),
            reviews=tuple(reviews),
            duplicate_evidence=tuple(duplicate_evidence),
            replacements=tuple(replacements),
        )


def _source_metadata(
    turn: TurnEnvelope,
    source_expression: str,
    guard: SensitiveContentGuard,
) -> dict[str, MessageRole | str | None]:
    """只从可信消息块派生来源身份，不信任模型字段。"""

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


def _is_explicit_replacement(candidate: Candidate) -> bool:
    """只接受用户明确表达的当前值或默认值变更。"""

    return (
        candidate.source_role is MessageRole.USER
        and candidate.expression_basis is ExpressionBasis.EXPLICIT
        and candidate.assertion_kind
        in {AssertionKind.USER_VIEW, AssertionKind.USER_PROVIDED_FACT}
        and _EXPLICIT_REPLACEMENT.search(candidate.source_expression) is not None
    )
