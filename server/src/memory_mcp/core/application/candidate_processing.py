"""候选记忆处理：校验模型建议、执行准入决策、产出记忆写入与待确认项。"""

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
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
    EvidenceDocument,
    EvidenceSourceType,
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
    VerificationStatus,
    normalize_memory_text,
)
from memory_mcp.core.exceptions import InvalidModelOutputError
from memory_mcp.core.ports import (
    DuplicateEvidenceWrite,
    EmbeddingProvider,
    MemoryRepository,
    ProfileRegistry,
    ReplacementWrite,
    SensitiveContentGuard,
    embed_single,
)

_EXPLICIT_REPLACEMENT = re.compile(
    r"(?:不再|不要再|改成|改为|换成|替换为|以后用|默认(?:改|换)|"
    r"\bno longer\b|\binstead\b|\breplace\b|\bnew default\b|"
    r"\bchange\b.+\bto\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CandidateProcessingResult:
    """一批候选处理后的原子写入结果：新记忆、待确认项、重复证据与替换。"""

    candidates: tuple[Candidate, ...]
    outcomes: tuple[CaptureOutcome, ...]
    memories: tuple[MemoryRecord, ...]
    reviews: tuple[ReviewItem, ...]
    duplicate_evidence: tuple[DuplicateEvidenceWrite, ...]
    replacements: tuple[ReplacementWrite, ...]


class CandidateMaterializer:
    """将通过校验的候选实体化为不可变的记忆写入结构。"""

    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID],
        clock: Callable[[], datetime],
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._embedding_provider = embedding_provider

    def record(
        self,
        candidate: Candidate,
        *,
        verification_status: VerificationStatus | None = None,
        owner_id: str | None = None,
    ) -> MemoryRecord:
        """构造一条新记忆。

        ``owner_id`` 默认取候选自身的 owner（即当前用户）；当待确认候选被确认
        提升到团队时，由调用方传入团队 owner key，使记忆写入团队公共空间而非
        个人空间。
        """

        resolved_owner_id = owner_id or candidate.owner_id
        memory_id = self._id_factory()
        revision_id = self._id_factory()
        created_at = self._clock()
        return MemoryRecord(
            item=MemoryItem(
                memory_id=memory_id,
                owner_id=resolved_owner_id,
                profile_id=candidate.profile_id,
                subject=candidate.subject,
                memory_type=candidate.memory_type,
                created_at=created_at,
            ),
            current_revision=MemoryRevision(
                revision_id=revision_id,
                memory_id=memory_id,
                owner_id=resolved_owner_id,
                revision_number=1,
                content=candidate.content,
                assertion_kind=candidate.assertion_kind,
                lifecycle_status=LifecycleStatus.ACTIVE,
                business_progress=candidate.business_progress,
                save_rationale=candidate.save_rationale,
                observed_at=candidate.observed_at,
                created_at=created_at,
                extraction_confidence=candidate.confidence,
                verification_status=(
                    verification_status or candidate.verification_status
                ),
                sensitivity_level=candidate.sensitivity_level,
                valid_from=candidate.valid_from,
                valid_until=candidate.valid_until,
                original_time_expression=candidate.original_time_expression,
                normalized_time=candidate.normalized_time,
                embedding=self._compute_embedding(candidate.content),
            ),
            evidence=(
                self._evidence(
                    candidate,
                    memory_id=memory_id,
                    revision_id=revision_id,
                    created_at=created_at,
                    owner_id=resolved_owner_id,
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
        *,
        verification_status: VerificationStatus | None = None,
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
            extraction_confidence=candidate.confidence,
            verification_status=(verification_status or candidate.verification_status),
            sensitivity_level=candidate.sensitivity_level,
            valid_from=candidate.valid_from,
            valid_until=candidate.valid_until,
            original_time_expression=candidate.original_time_expression,
            normalized_time=candidate.normalized_time,
            embedding=self._compute_embedding(candidate.content),
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

    def _compute_embedding(self, content: str) -> tuple[float, ...] | None:
        """计算 content 的 embedding；provider 不可用或失败时返回 None。"""

        try:
            return embed_single(self._embedding_provider, content)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "embedding computation failed: %s: %s",
                type(exc).__name__,
                exc,
            )
        return None

    def _evidence(
        self,
        candidate: Candidate,
        *,
        memory_id: UUID,
        revision_id: UUID,
        created_at: datetime,
        owner_id: str | None = None,
    ) -> Evidence:
        resolved_owner_id = owner_id or candidate.owner_id
        return Evidence(
            evidence_id=self._id_factory(),
            memory_id=memory_id,
            revision_id=revision_id,
            owner_id=resolved_owner_id,
            conversation_id=candidate.conversation_id,
            source_turn_id=candidate.source_turn_id,
            source_expression=candidate.source_expression,
            observed_at=candidate.observed_at,
            created_at=created_at,
            source_role=candidate.source_role,
            source_message_id=candidate.source_message_id,
            source_tool_name=candidate.source_tool_name,
            source_type=candidate.source_type,
            document=_candidate_document(candidate),
        )


class CandidateProcessor:
    """校验候选建议、执行准入与生命周期判定，产出 Repository 原子写入。"""

    def __init__(
        self,
        repository: MemoryRepository,
        profile_registry: ProfileRegistry,
        sensitive_guard: SensitiveContentGuard,
        admission_policy: ConservativeAdmissionPolicy,
        materializer: CandidateMaterializer,
        *,
        id_factory: Callable[[], UUID],
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._profile_registry = profile_registry
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
        """处理一批候选建议：校验来源、判定准入、处理重复/替换，产出原子写入。

        对每个建议依次执行：来源校验 -> 敏感校验 -> 准入判定 -> 生命周期去重
        （重复证据、显式替换或歧义待确认），最终分类为自动保存、待确认或丢弃。
        ``initial_outcomes`` 携带在候选处理前已确定的结果（如敏感内容拦截），
        一并合入返回。
        """
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
            self._profile_registry.validate_memory_type(
                turn.profile_id,
                proposal.memory_type,
            )
            self._profile_registry.validate_business_progress(
                turn.profile_id,
                proposal.business_progress,
            )
            metadata_policy = self._profile_registry.metadata_policy(
                turn.profile_id,
                proposal.memory_type,
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
                        source_metadata["source_uri"],
                        source_metadata["source_title"],
                        source_metadata["source_publisher"],
                        source_metadata["content_hash"],
                        source_metadata["citation_locator"],
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
                profile_id=turn.profile_id,
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
                verification_status=(
                    VerificationStatus.USER_ASSERTED
                    if source_metadata["source_role"] is MessageRole.USER
                    else VerificationStatus.UNVERIFIED
                ),
                sensitivity_level=metadata_policy.sensitivity_level,
                # normalized_time 记录的是文本中表达的业务时间（如"下周三"），
                # 并不意味着记忆要等到那个时间才可见；有效期始终从可信事件时间
                # （observed_at）开始计算。
                valid_from=turn.observed_at,
                valid_until=(
                    turn.observed_at + timedelta(days=metadata_policy.validity_days)
                    if metadata_policy.validity_days is not None
                    else None
                ),
                business_progress=proposal.business_progress,
                original_time_expression=proposal.original_time_expression,
                normalized_time=proposal.normalized_time,
                **source_metadata,
            )
            candidates.append(candidate)
            admission = self._admission_policy.decide(candidate)
            # 即使通过准入，非用户来源（如助手/工具输出）的候选也降级为待确认，
            # 避免把推断性内容直接自动写入。
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
                # 同一 subject+类型在本次批次出现多个候选，无法判定唯一目标，
                # 降级为待确认。
                if admission.decision is AdmissionDecision.AUTO_SAVE:
                    admission = AdmissionOutcome(
                        AdmissionDecision.PENDING,
                        "multiple_candidates_same_scope",
                    )
                current_scope = ()
            else:
                current_scope = self._repository.find_current(
                    principal,
                    profile_id=candidate.profile_id,
                    subject=candidate.subject,
                    memory_type=candidate.memory_type,
                    effective_at=self._clock(),
                )
            target = current_scope[0] if len(current_scope) == 1 else None
            if len(current_scope) > 1:
                # 现存多条同 subject+类型记忆，无法确定应更新哪条。
                admission = AdmissionOutcome(
                    AdmissionDecision.PENDING,
                    "ambiguous_lifecycle_target",
                )
            elif target is not None and target.item.memory_id in lifecycle_target_ids:
                # 本轮已对该目标做过替换/重复证据，再次变更视为歧义。
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
                    # 用户明确表达替换意图且来源为 EXPLICIT，直接生成替换写入。
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
                # 存在同 subject+类型目标但非明确替换，交给用户确认。
                admission = AdmissionOutcome(
                    AdmissionDecision.PENDING,
                    "ambiguous_lifecycle_conflict",
                )
            elif target is None and admission.decision is AdmissionDecision.AUTO_SAVE:
                # 字面 subject 无命中：尝试基于嵌入的语义去重，把近似现有记忆
                # 视为生命周期目标，避免同主题 thesis/evidence 碎片化。阈值由
                # Profile 的 metadata_policies 声明，None 表示该类型不启用。
                admission = self._resolve_semantic_target(
                    principal,
                    candidate,
                    admission,
                    lifecycle_target_ids,
                    duplicate_evidence,
                    replacements,
                    outcomes,
                )
                if admission is None:
                    # 已在分支内产出写入并 append outcome，跳过后续 auto_save 新增。
                    continue
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

    def _resolve_semantic_target(
        self,
        principal: PrincipalContext,
        candidate: Candidate,
        admission: AdmissionOutcome,
        lifecycle_target_ids: set[UUID],
        duplicate_evidence: list[DuplicateEvidenceWrite],
        replacements: list[ReplacementWrite],
        outcomes: list[CaptureOutcome],
    ) -> AdmissionOutcome | None:
        """对未匹配字面 subject 的 auto_save 候选尝试语义去重。

        读 Profile 的 ``semantic_dedup_threshold``：None 表示该类型不启用，
        直接返回原 admission 走新增路径。threshold 非 None 时计算候选嵌入，
        查同 owner+profile+type 的活动记忆，命中则按既有生命周期决策树
        （近似重复 -> duplicate evidence；显式替换 -> replacement；
        否则 -> pending 交用户确认）处理。返回 None 表示已产出写入、
        调用方应跳过后续 auto_save 新增。
        """

        metadata_policy = self._profile_registry.metadata_policy(
            candidate.profile_id,
            candidate.memory_type,
        )
        threshold = metadata_policy.semantic_dedup_threshold
        if threshold is None:
            return admission
        embedding = self._materializer._compute_embedding(candidate.content)
        if embedding is None:
            return admission
        target = self._repository.find_semantically_similar(
            principal,
            profile_id=candidate.profile_id,
            memory_type=candidate.memory_type,
            embedding=embedding,
            threshold=threshold,
            effective_at=self._clock(),
        )
        if target is None:
            return admission
        if target.item.memory_id in lifecycle_target_ids:
            return AdmissionOutcome(
                AdmissionDecision.PENDING,
                "lifecycle_target_already_changed",
            )
        if normalize_memory_text(target.current_revision.content) == (
            normalize_memory_text(candidate.content)
        ):
            duplicate_evidence.append(
                self._materializer.duplicate(target, candidate)
            )
            lifecycle_target_ids.add(target.item.memory_id)
            outcomes.append(
                CaptureOutcome(
                    candidate_id=candidate.candidate_id,
                    decision=AdmissionDecision.AUTO_SAVE,
                    reason_code="semantic_duplicate_evidence",
                    memory_id=target.item.memory_id,
                )
            )
            return None
        if _is_explicit_replacement(candidate):
            replacements.append(
                self._materializer.replacement(target, candidate)
            )
            lifecycle_target_ids.add(target.item.memory_id)
            outcomes.append(
                CaptureOutcome(
                    candidate_id=candidate.candidate_id,
                    decision=AdmissionDecision.AUTO_SAVE,
                    reason_code="semantic_explicit_replacement",
                    memory_id=target.item.memory_id,
                )
            )
            return None
        return AdmissionOutcome(
            AdmissionDecision.PENDING,
            "semantic_lifecycle_conflict",
        )


def _source_metadata(
    turn: TurnEnvelope,
    source_expression: str,
    guard: SensitiveContentGuard,
) -> dict[str, Any]:
    """从可信消息块派生候选的来源身份（角色、消息 ID 等），不信任模型自报字段。"""

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
            "source_type": EvidenceSourceType.CONVERSATION,
            "source_uri": None,
            "source_title": None,
            "source_publisher": None,
            "published_at": None,
            "retrieved_at": None,
            "content_hash": None,
            "citation_locator": None,
        }
    selected = next(
        (message for message in matching if message.role is MessageRole.USER),
        matching[0],
    )
    return {
        "source_role": selected.role,
        "source_message_id": selected.message_id,
        "source_tool_name": selected.tool_name,
        "source_type": (
            selected.source_type
            or (
                EvidenceSourceType.TOOL
                if selected.role is MessageRole.TOOL
                else EvidenceSourceType.CONVERSATION
            )
        ),
        "source_uri": selected.source_uri,
        "source_title": selected.source_title,
        "source_publisher": selected.source_publisher,
        "published_at": selected.published_at,
        "retrieved_at": selected.retrieved_at,
        "content_hash": selected.content_hash,
        "citation_locator": selected.citation_locator,
    }


def _is_explicit_replacement(candidate: Candidate) -> bool:
    """判断候选是否构成对已有记忆的显式替换：必须由用户明确表达当前值/默认值变更。"""

    return (
        candidate.source_role is MessageRole.USER
        and candidate.expression_basis is ExpressionBasis.EXPLICIT
        and candidate.assertion_kind
        in {AssertionKind.USER_VIEW, AssertionKind.USER_PROVIDED_FACT}
        and _EXPLICIT_REPLACEMENT.search(candidate.source_expression) is not None
    )


def _candidate_document(candidate: Candidate) -> EvidenceDocument | None:
    """从 candidate 的内联文档字段构造 EvidenceDocument；无文档字段时返回 None。"""

    has_document = any(
        getattr(candidate, field) is not None
        for field in (
            "source_uri",
            "source_title",
            "source_publisher",
            "published_at",
            "retrieved_at",
            "content_hash",
            "citation_locator",
        )
    )
    if not has_document:
        return None
    return EvidenceDocument(
        source_uri=candidate.source_uri,
        source_title=candidate.source_title,
        source_publisher=candidate.source_publisher,
        published_at=candidate.published_at,
        retrieved_at=candidate.retrieved_at,
        content_hash=candidate.content_hash,
        citation_locator=candidate.citation_locator,
    )
