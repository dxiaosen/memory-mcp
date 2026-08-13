"""候选记忆处理：校验模型建议、执行准入决策、产出记忆写入与待确认项。"""

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter
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
    source_expression_matches,
)
from memory_mcp.core.exceptions import (
    InvalidMemoryTypeError,
    InvalidProfileProgressError,
)
from memory_mcp.core.ports import (
    DuplicateEvidenceWrite,
    EmbeddingProvider,
    MemoryRepository,
    ProfileRegistry,
    ReplacementWrite,
    SensitiveContentGuard,
    embed_single,
)
from memory_mcp.core.support import log_event

# 显式替换意图但字面 subject 未命中时的语义 fallback 阈值。
# 比 semantic_dedup_threshold 宽松，用于新旧判断措辞不同但仍语义相关的场景；
# 仅在 _is_explicit_replacement 且 Profile 未配 semantic_dedup_threshold 时启用。
# 显式替换意图但字面 subject 未命中时的语义 fallback 阈值。比 semantic_dedup_threshold
# 宽松，用于新旧判断措辞不同但仍语义相关的场景；0.60 保证只有明显语义相关才命中。
# 加 top1-top2 margin 约束，避免误伤独立 thesis（宁可 Pending 不替错）。
_REPLACEMENT_FALLBACK_THRESHOLD = 0.60
_REPLACEMENT_FALLBACK_MARGIN = 0.08
# replacement fallback 歧义豁免：top1 达此阈值即视为强匹配（明显是真目标），
# 即使 top1-top2 margin 不足也允许替换——top2 多半是同主题的另一条相关判断，
# 不构成"无法确定替谁"的真歧义。低于此值、刚过 fallback 阈值时仍走 margin 歧义保护。
_REPLACEMENT_STRONG_MATCH_THRESHOLD = 0.75

# assistant 跨类型回声检测的保守默认阈值：当 candidate 所属 memory_type 未配
# semantic_dedup_threshold 时用此值。回声是高度重复，0.90 足够保守不会误杀
# 独立判断，又能拦住 assistant 复述已有记忆换类型抽取的情况。
_ASSISTANT_ECHO_DEFAULT_THRESHOLD = 0.90

_EXPLICIT_REPLACEMENT = re.compile(
    r"(?:"
    r"不再|不要再|改成|改为|换成|替换为|以后用|默认(?:改|换)|"
    r"改(?:一下|了)?|调整(?:下)?|修订|修正|更新|变更|"
    r"不能只看[^。；！？]{0,20}?还要|"
    r"不再关注|不在关注|不再看|去掉|删掉|移除|"
    # 增量扩展/框架调整：在既有判断上增加关注点、扩展维度——属于对已有研究框架
    # 的修订，旧判断被扩展后的新版本 supersede，与"改成/调整"同列。
    r"增加对?[^。；！？]{0,15}?关注|扩展对?[^。；！？]{0,15}?(?:关注|维度)|"
    r"补充[^。；！？]{0,10}?(?:指标|维度|关注)|纳入[^。；！？]{0,10}?(?:指标|维度|关注)|"
    r"新增对?[^。；！？]{0,15}?关注|"
    r"\bno longer\b|\binstead\b|\breplace\b|\bnew default\b|"
    r"\bchange\b.+\bto\b|\brevise\b|\bupdate\b|\bmodify\b|"
    r"\badd\b.+\b(?:focus|attention|metric)\b|\bexpand\b.+\bto\b"
    r")",
    re.IGNORECASE,
)
# 操作指令模式：不要使用/读取/调用/打开工具、文件、skill、memory、联网。
# 这类指令不是投研长期偏好，默认丢弃；除非用户显式表达跨会话持久（下方 _EXPLICIT_DURABLE）。
_OPERATIONAL_INSTRUCTION_RE = re.compile(
    r"(?:不(?:要|需|需要)?|别|勿)"
    r"(?:使用|读取|调用|打开|引入|访问|运行)"
    r"[^，。；！？\n]{0,15}?"
    r"(?:工具|skill|memory|记忆|文件|网络|联网)"
    r"|(?:不(?:要|需|需要)?|别|勿)联网"
    r"|(?:不(?:要|需|需要)?|别|勿)打开[^，。；！？\n]{0,10}?文件"
)
# 用户显式跨会话持久偏好：即便含操作指令词，也视为长期偏好保留。
_EXPLICIT_DURABLE_PREFERENCE_RE = re.compile(
    r"以后所有会话|今后始终|长期默认|从今以后|每次都|以后(?:分析|研究|输出|默认|都|用)"
)
# inspect/manage/test turn 模式：用户在查看/检查/管理 Memory MCP 记忆，而非陈述投研判断。
# 这类 turn 的 source_expression/content 不应进入长期记忆。确定性兜底（prompt 只引导）。
_INSPECT_MANAGE_RE = re.compile(
    r"(?:查看|检查|查下|帮我查|看看|列出|确认下|可以查)"
    r"[^，。；！？\n]{0,20}?"
    r"(?:Pending|pending|待确认|待审|记忆|memory|review|owner_id|团队记忆)"
    r"|(?:请(?:查看|检查|告诉我))"
    r"|(?:告诉我[^，。；！？\n]{0,15}?(?:验证|指标|判断|跟踪|风险|结论))"
    r"|(?:我当前有权限访问.*记忆)"
    r"|(?:不应该作为我的个人记忆)"
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RejectedProposal:
    """前置校验阶段被拒绝的候选建议，保留 proposal 关键字段供开发日志调试。

    被拒候选未进入 ``Candidate`` 构造（缺少可信 source_metadata），但记录其
    subject/content/source_expression 等便于定位模型为何被拒。
    """

    candidate_id: UUID
    reason_code: str
    subject: str
    memory_type: str
    content: str
    source_expression: str
    assertion_kind: AssertionKind
    expression_basis: ExpressionBasis


@dataclass(frozen=True, slots=True)
class CandidateProcessingResult:
    """一批候选处理后的原子写入结果：新记忆、待确认项、重复证据与替换。"""

    candidates: tuple[Candidate, ...]
    outcomes: tuple[CaptureOutcome, ...]
    memories: tuple[MemoryRecord, ...]
    reviews: tuple[ReviewItem, ...]
    duplicate_evidence: tuple[DuplicateEvidenceWrite, ...]
    replacements: tuple[ReplacementWrite, ...]
    rejected_proposals: tuple[RejectedProposal, ...] = ()
    timing: dict[str, float] | None = None


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
            log_event(
                _LOGGER,
                logging.WARNING,
                "memory.embedding.computation_failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
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
        rejected: list[RejectedProposal] = []
        lifecycle_target_ids: set[UUID] = set()
        candidate_scopes: set[tuple[str, str]] = set()
        replacement_types: set[str] = set()  # 本轮已做过 replacement 的 memory_type
        # 分阶段耗时累加：校验/准入/lifecycle 三段在循环内累加。
        _validation_duration = 0.0
        _admission_duration = 0.0
        _lifecycle_duration = 0.0

        for proposal in proposals:
            candidate_id = self._id_factory()
            _validation_started_at = perf_counter()
            if not source_expression_matches(
                proposal.source_expression, redacted_source
            ):
                # 单条候选的 source_expression 不匹配脱敏后原文时，只丢弃该条，
                # 不让一条坏候选拖垮整轮：单条 source_expression 不匹配只丢弃该条，
                # 用户研究基准不应因模型一次编造而整轮丢失。
                rejected.append(
                    RejectedProposal(
                        candidate_id=candidate_id,
                        reason_code="invalid_source_expression",
                        subject=proposal.subject,
                        memory_type=proposal.memory_type,
                        content=proposal.content,
                        source_expression=proposal.source_expression,
                        assertion_kind=proposal.assertion_kind,
                        expression_basis=proposal.expression_basis,
                    )
                )
                outcomes.append(
                    CaptureOutcome(
                        candidate_id=candidate_id,
                        decision=AdmissionDecision.DISCARD,
                        reason_code="invalid_source_expression",
                    )
                )
                _validation_duration += perf_counter() - _validation_started_at
                continue
            if _is_operational_instruction(proposal):
                # 操作指令（不要使用工具/读取文件/联网等）不是长期研究偏好：默认丢弃，
                # 除非用户显式表达跨会话持久。类型无关，不在 Core 硬编码
                # research_preference（尊重 Profile 边界铁律）。
                rejected.append(
                    RejectedProposal(
                        candidate_id=candidate_id,
                        reason_code="operational_instruction",
                        subject=proposal.subject,
                        memory_type=proposal.memory_type,
                        content=proposal.content,
                        source_expression=proposal.source_expression,
                        assertion_kind=proposal.assertion_kind,
                        expression_basis=proposal.expression_basis,
                    )
                )
                outcomes.append(
                    CaptureOutcome(
                        candidate_id=candidate_id,
                        decision=AdmissionDecision.DISCARD,
                        reason_code="operational_instruction",
                    )
                )
                _validation_duration += perf_counter() - _validation_started_at
                continue
            # 单条 Candidate 的业务字段错误（memory_type / business_progress 等）只丢弃该条，
            # 不让整轮 Capture 失败：单条字段错误只 discard 该条。
            try:
                self._profile_registry.validate_memory_type(
                    turn.profile_id,
                    proposal.memory_type,
                )
                self._profile_registry.validate_business_progress(
                    turn.profile_id,
                    proposal.business_progress,
                )
            except (
                InvalidMemoryTypeError,
                InvalidProfileProgressError,
                ValueError,
            ) as exc:
                reason_code = (
                    "invalid_memory_type"
                    if isinstance(exc, InvalidMemoryTypeError)
                    else "invalid_business_progress"
                    if isinstance(exc, InvalidProfileProgressError)
                    else "invalid_candidate_field"
                )
                rejected.append(
                    RejectedProposal(
                        candidate_id=candidate_id,
                        reason_code=reason_code,
                        subject=proposal.subject,
                        memory_type=proposal.memory_type,
                        content=proposal.content,
                        source_expression=proposal.source_expression,
                        assertion_kind=proposal.assertion_kind,
                        expression_basis=proposal.expression_basis,
                    )
                )
                outcomes.append(
                    CaptureOutcome(
                        candidate_id=candidate_id,
                        decision=AdmissionDecision.DISCARD,
                        reason_code=reason_code,
                    )
                )
                _validation_duration += perf_counter() - _validation_started_at
                continue
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
                # source_expression 在脱敏原文中出现但无法定位到具体消息：
                # 同样降级为丢弃单条，而非整轮失败。
                rejected.append(
                    RejectedProposal(
                        candidate_id=candidate_id,
                        reason_code="ambiguous_source_message",
                        subject=proposal.subject,
                        memory_type=proposal.memory_type,
                        content=proposal.content,
                        source_expression=proposal.source_expression,
                        assertion_kind=proposal.assertion_kind,
                        expression_basis=proposal.expression_basis,
                    )
                )
                outcomes.append(
                    CaptureOutcome(
                        candidate_id=candidate_id,
                        decision=AdmissionDecision.DISCARD,
                        reason_code="ambiguous_source_message",
                    )
                )
                _validation_duration += perf_counter() - _validation_started_at
                continue
            normalized_assertion_kind = _normalize_assertion_kind(
                proposal.assertion_kind,
                source_metadata["source_role"],
                source_metadata["source_type"],
                proposal.expression_basis,
            )
            if normalized_assertion_kind is not None:
                # 模型把 Assistant 推断标成 user_view/user_provided_fact，或把
                # 外部材料事实标成 user_*：按可信来源纠正，避免语义污染。
                log_event(
                    _LOGGER,
                    logging.DEBUG,
                    "memory.capture.candidate.assertion_normalized",
                    candidate_ref=str(candidate_id),
                    memory_type=proposal.memory_type,
                    source_role=(
                        source_metadata["source_role"].value
                        if source_metadata["source_role"] is not None
                        else None
                    ),
                    source_type=source_metadata["source_type"].value,
                    expression_basis=proposal.expression_basis.value,
                    from_assertion_kind=proposal.assertion_kind.value,
                    to_assertion_kind=normalized_assertion_kind.value,
                )
            resolved_assertion_kind = normalized_assertion_kind or proposal.assertion_kind
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
                assertion_kind=resolved_assertion_kind,
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
            _validation_duration += perf_counter() - _validation_started_at
            _admission_started_at = perf_counter()
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
            _admission_duration += perf_counter() - _admission_started_at
            _lifecycle_started_at = perf_counter()
            # replacement fragment 丢弃：本轮已有同 memory_type 的 replacement，
            # 后续同 type 候选视为该 replacement 的碎片（条件拆解/旧状态说明），
            # discard 不再单独保存（避免一次修正产生多条 Active）。
            if (
                candidate.memory_type in replacement_types
                and admission.decision is AdmissionDecision.AUTO_SAVE
                and candidate.source_role is MessageRole.USER
            ):
                outcomes.append(
                    CaptureOutcome(
                        candidate_id=candidate.candidate_id,
                        decision=AdmissionDecision.DISCARD,
                        reason_code="replacement_fragment",
                    )
                )
                _lifecycle_duration += perf_counter() - _lifecycle_started_at
                continue
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
            if (
                candidate.source_role is MessageRole.ASSISTANT
                and self._is_assistant_restatement(principal, candidate, current_scope)
            ):
                # Assistant 复述已有 active memory（Recall 后回声）-> 丢弃，不建 Pending、
                # 也不当已有 Memory 的新 Evidence。用户本人重述走既有
                # duplicate/evidence 规则，不触发本规则。
                outcomes.append(
                    CaptureOutcome(
                        candidate_id=candidate.candidate_id,
                        decision=AdmissionDecision.DISCARD,
                        reason_code="assistant_restatement",
                    )
                )
                _lifecycle_duration += perf_counter() - _lifecycle_started_at
                continue
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
                    _lifecycle_duration += perf_counter() - _lifecycle_started_at
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
                    replacement_types.add(candidate.memory_type)
                    outcomes.append(
                        CaptureOutcome(
                            candidate_id=candidate.candidate_id,
                            decision=AdmissionDecision.AUTO_SAVE,
                            reason_code="explicit_replacement",
                            memory_id=target.item.memory_id,
                        )
                    )
                    _lifecycle_duration += perf_counter() - _lifecycle_started_at
                    continue
                # 存在同 subject+类型目标但非明确替换，交给用户确认。
                admission = AdmissionOutcome(
                    AdmissionDecision.PENDING,
                    "ambiguous_lifecycle_conflict",
                )
            elif target is None and admission.decision in (
                AdmissionDecision.AUTO_SAVE,
                AdmissionDecision.PENDING,
            ):
                # 字面 subject 无命中：尝试基于嵌入的语义去重，把近似现有记忆
                # 视为生命周期目标，避免同主题 thesis/evidence 碎片化。非用户源
                # 候选即使被 non_user_source 降为 PENDING 也走这里——否则 assistant
                # 复述换了 subject 措辞就会绕过去重直接进 Pending，用户 confirm 后
                # 变成第二条语义重复的 active。阈值由 Profile 的 metadata_policies
                # 声明，None 表示该类型不启用。
                if (
                    candidate.source_role is MessageRole.ASSISTANT
                    and self._is_cross_type_echo(principal, candidate)
                ):
                    # assistant 跨类型复述已有活动记忆（如已有 risk，新抽 thesis）
                    # -> discard，不进 Pending、不合并（跨类型合并会语义错位）。
                    outcomes.append(
                        CaptureOutcome(
                            candidate_id=candidate.candidate_id,
                            decision=AdmissionDecision.DISCARD,
                            reason_code="assistant_cross_type_echo",
                        )
                    )
                    _lifecycle_duration += perf_counter() - _lifecycle_started_at
                    continue
                admission = self._resolve_semantic_target(
                    principal,
                    candidate,
                    admission,
                    lifecycle_target_ids,
                    duplicate_evidence,
                    replacements,
                    replacement_types,
                    outcomes,
                )
                if admission is None:
                    # 已在分支内产出写入并 append outcome，跳过后续 auto_save 新增。
                    _lifecycle_duration += perf_counter() - _lifecycle_started_at
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
            _lifecycle_duration += perf_counter() - _lifecycle_started_at

        return CandidateProcessingResult(
            candidates=tuple(candidates),
            outcomes=tuple(outcomes),
            memories=tuple(memories),
            reviews=tuple(reviews),
            duplicate_evidence=tuple(duplicate_evidence),
            replacements=tuple(replacements),
            rejected_proposals=tuple(rejected),
            timing={
                "candidate_validation_duration_ms": _validation_duration * 1000,
                "admission_duration_ms": _admission_duration * 1000,
                "lifecycle_duration_ms": _lifecycle_duration * 1000,
            },
        )

    def _is_assistant_restatement(
        self,
        principal: PrincipalContext,
        candidate: Candidate,
        current_scope: Sequence[MemoryRecord],
    ) -> bool:
        """assistant 来源候选是否高度重复已有 active memory。

        先看同 subject+type 的精确命中里 content 是否构成复述（归一等价或包含）；
        未命中且 Profile 该类型配了 ``semantic_dedup_threshold`` 时，再用语义相似度兜底，
        捕获换了 subject 措辞的回声。仅在 source_role=assistant 时调用。
        """

        for record in current_scope:
            if _content_restates(record, candidate):
                return True
        metadata_policy = self._profile_registry.metadata_policy(
            candidate.profile_id,
            candidate.memory_type,
        )
        threshold = metadata_policy.semantic_dedup_threshold
        if threshold is None:
            return False
        embedding = self._materializer._compute_embedding(candidate.content)
        if embedding is None:
            return False
        target = self._repository.find_semantically_similar(
            principal,
            profile_id=candidate.profile_id,
            memory_type=candidate.memory_type,
            embedding=embedding,
            threshold=threshold,
            effective_at=self._clock(),
        )
        return target is not None

    def _is_cross_type_echo(
        self,
        principal: PrincipalContext,
        candidate: Candidate,
    ) -> bool:
        """assistant 源候选是否跨 memory_type 复述已有活动记忆。

        同类型语义去重（``_resolve_semantic_target`` / ``_is_assistant_restatement``）
        只查同 memory_type，但 assistant 复述已有判断时模型可能把它抽成不同
        类型的新候选（如已有 risk，新抽 thesis/research_question）。这里不限
        memory_type 查余弦相似度，命中即视为回声。阈值取该类型 Profile 的
        ``semantic_dedup_threshold``；未配时用一个保守默认（0.90），因为回声
        是高度重复，0.90 足够保守不会误杀独立判断。
        """

        metadata_policy = self._profile_registry.metadata_policy(
            candidate.profile_id,
            candidate.memory_type,
        )
        threshold = metadata_policy.semantic_dedup_threshold
        if threshold is None:
            threshold = _ASSISTANT_ECHO_DEFAULT_THRESHOLD
        embedding = self._materializer._compute_embedding(candidate.content)
        if embedding is None:
            return False
        target = self._repository.find_assistant_echo(
            principal,
            profile_id=candidate.profile_id,
            embedding=embedding,
            threshold=threshold,
            effective_at=self._clock(),
        )
        return target is not None

    def _resolve_semantic_target(
        self,
        principal: PrincipalContext,
        candidate: Candidate,
        admission: AdmissionOutcome,
        lifecycle_target_ids: set[UUID],
        duplicate_evidence: list[DuplicateEvidenceWrite],
        replacements: list[ReplacementWrite],
        replacement_types: set[str],
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
        # 显式替换意图但字面 subject 未命中（新旧判断措辞不同）时，允许一次有界
        # replacement fallback：用更宽松的阈值查同 owner+profile+type 的旧 active
        # memory，找到明显目标即作为替换目标。非替换场景下
        # threshold=None 表示该类型不启用语义去重，直接返回。
        is_explicit_replacement = _is_explicit_replacement(candidate)
        if threshold is None and not is_explicit_replacement:
            return admission
        embedding = self._materializer._compute_embedding(candidate.content)
        if embedding is None:
            return admission
        if is_explicit_replacement:
            threshold = _REPLACEMENT_FALLBACK_THRESHOLD
        # 显式替换 fallback 用 top2 + margin 判定唯一明显目标：
        # top1 达阈值但 top1-top2 不足 margin -> 歧义 -> Pending，不替错独立 thesis。
        top1, top2 = self._repository.find_semantically_similar_top2(
            principal,
            profile_id=candidate.profile_id,
            memory_type=candidate.memory_type,
            embedding=embedding,
            threshold=threshold,
            effective_at=self._clock(),
        )
        if top1 is None:
            return admission
        if is_explicit_replacement and top2 is not None:
            if top1[0] - top2[0] < _REPLACEMENT_FALLBACK_MARGIN:
                # top1 相似度足够高（强匹配）时，即使 margin 不足也允许替换：
                # top2 多半是同主题的另一条相关判断，不构成"无法确定替谁"的真歧义。
                # 仅当 top1 刚过 fallback 阈值、与 top2 接近时才保守降 pending。
                if top1[0] < _REPLACEMENT_STRONG_MATCH_THRESHOLD:
                    # top1 和 top2 太接近，无法确定唯一替换目标 -> 交用户确认。
                    return AdmissionOutcome(
                        AdmissionDecision.PENDING,
                        "ambiguous_semantic_replacement_target",
                    )
                # top1 >= 强匹配阈值：允许替换，不因相近 top2 判歧义。
        target = top1[1]
        if target.item.memory_id in lifecycle_target_ids:
            return AdmissionOutcome(
                AdmissionDecision.PENDING,
                "lifecycle_target_already_changed",
            )
        if normalize_memory_text(target.current_revision.content) == (
            normalize_memory_text(candidate.content)
        ):
            if candidate.source_role is MessageRole.USER:
                # 用户源语义重复 -> 追加 Evidence（与字面 duplicate_evidence 对齐）。
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
            # 非用户源（assistant/tool）语义等价命中已有记忆 -> 回声，discard；
            # 不给已有记忆追加 Evidence（非用户源不应作为新证据来源）。
            lifecycle_target_ids.add(target.item.memory_id)
            outcomes.append(
                CaptureOutcome(
                    candidate_id=candidate.candidate_id,
                    decision=AdmissionDecision.DISCARD,
                    reason_code="semantic_assistant_restatement",
                )
            )
            return None
        if _is_explicit_replacement(candidate):
            replacements.append(
                self._materializer.replacement(target, candidate)
            )
            lifecycle_target_ids.add(target.item.memory_id)
            replacement_types.add(candidate.memory_type)
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


def _select_source_message(matching: list[TurnMessage]) -> TurnMessage:
    """同一 source_expression 命中多条消息时按 user > tool > assistant 优先级选择绑定来源。

    优先级：user explicit > tool/document original source > assistant paraphrase。
    模型对同一语义既出现在用户原文又出现在 assistant 复述时，优先绑定用户原始消息，
    使 thesis/risk/ongoing_research/research_preference 等用户判断落到 user 来源。
    """

    for role in (MessageRole.USER, MessageRole.TOOL, MessageRole.ASSISTANT):
        for message in matching:
            if message.role is role:
                return message
    return matching[0]


def _source_metadata(
    turn: TurnEnvelope,
    source_expression: str,
    guard: SensitiveContentGuard,
) -> dict[str, Any]:
    """从可信消息块派生候选的来源身份（角色、消息 ID 等），不信任模型自报字段。"""

    matching: list[TurnMessage] = []
    for message in turn.messages:
        redacted = guard.inspect(message.content).redacted_text
        if source_expression_matches(source_expression, redacted):
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
    selected = _select_source_message(matching)
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


def _normalize_assertion_kind(
    reported: AssertionKind,
    source_role: MessageRole | None,
    source_type: EvidenceSourceType,
    expression_basis: ExpressionBasis,
) -> AssertionKind | None:
    """按可信来源角色/类型与表达基础修正模型自报的 assertion_kind，消除语义冲突。

    assertion_kind 必须与 expression_basis 一致。这里只纠正明确的语义冲突，
    对无冲突的标注返回 None（保持原值）：

    - tool/document/web 来源 + inferred 基础 -> system_inference（从材料推断出的
      结论不是原始事实；本次日志"资本开支强度"即 external_fact+inferred 违规）。
    - tool/document/web 来源 + explicit 基础 + 任意 user_*/system_inference
      -> external_fact（直接摘自材料的事实）。
    - tool/document/web 来源 + ambiguous 基础 -> 不纠正（保守，留待人工）。
    - assistant + user_view/user_provided_fact -> system_inference（Assistant 的
      判断/推断）。
    """

    if source_type in (
        EvidenceSourceType.TOOL,
        EvidenceSourceType.DOCUMENT,
        EvidenceSourceType.WEB,
    ):
        if expression_basis is ExpressionBasis.INFERRED:
            if reported is not AssertionKind.SYSTEM_INFERENCE:
                return AssertionKind.SYSTEM_INFERENCE
            return None
        if expression_basis is ExpressionBasis.EXPLICIT and reported in (
            AssertionKind.USER_VIEW,
            AssertionKind.USER_PROVIDED_FACT,
            AssertionKind.SYSTEM_INFERENCE,
        ):
            return AssertionKind.EXTERNAL_FACT
        return None
    if (
        source_role is MessageRole.ASSISTANT
        and reported in (AssertionKind.USER_VIEW, AssertionKind.USER_PROVIDED_FACT)
    ):
        return AssertionKind.SYSTEM_INFERENCE
    return None


def _is_explicit_replacement(candidate: Candidate) -> bool:
    """判断候选是否构成对已有记忆的显式替换：必须由用户明确表达当前值/默认值变更。

    修订意图词（\"改一下/调整下/修订/不能只看...还要\"）可能出现在 model 生成的
    content 概述里（\"用户修订了...判断标准\"），而非 source_expression 原文摘录，
    因此同时检查 content 与 source_expression。
    """

    if (
        candidate.source_role is not MessageRole.USER
        or candidate.expression_basis is not ExpressionBasis.EXPLICIT
        or candidate.assertion_kind
        not in {AssertionKind.USER_VIEW, AssertionKind.USER_PROVIDED_FACT}
    ):
        return False
    return any(
        _EXPLICIT_REPLACEMENT.search(text) is not None
        for text in (candidate.source_expression, candidate.content)
        if text
    )


def _is_operational_instruction(proposal: CandidateProposal) -> bool:
    """候选是否为操作指令或 inspect/manage turn，且非显式跨会话持久偏好。

    操作指令（不要使用工具/读取文件/联网）不是投研长期偏好，默认丢弃。
    inspect/manage turn（查看/检查/查下 Pending/记忆/团队记忆）是系统操作语义，
    不应进入业务记忆。用户显式表达「以后所有会话都…」等跨会话持久时保留。
    检查 source_expression 与 content。
    """

    text = f"{proposal.source_expression}\n{proposal.content}"
    if _EXPLICIT_DURABLE_PREFERENCE_RE.search(text):
        return False
    return (
        _OPERATIONAL_INSTRUCTION_RE.search(text) is not None
        or _INSPECT_MANAGE_RE.search(text) is not None
    )


def _content_restates(target: MemoryRecord, candidate: Candidate) -> bool:
    """候选 content 是否为已有记忆 content 的复述（归一后等价或一方包含另一方）。

    用于识别 Assistant 回声：归一（NFKC+casefold+空白压缩）后等价、或候选是已有记忆
    的摘录、或已有记忆是候选的摘录，均视为高度重复。
    """

    target_text = normalize_memory_text(target.current_revision.content)
    candidate_text = normalize_memory_text(candidate.content)
    if not target_text or not candidate_text:
        return False
    return (
        target_text == candidate_text
        or candidate_text in target_text
        or target_text in candidate_text
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
