"""Profile 驱动的自动关系抽取：端点选择、模型抽取与保守准入。"""

import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from time import perf_counter
from uuid import UUID

from memory_mcp.core.domain import (
    ExpressionBasis,
    LifecycleStatus,
    MemoryRecord,
    MemoryRelation,
    PrincipalContext,
    RelationEndpoint,
    RelationOrigin,
    RelationProposal,
    RelationProvenance,
    RelationScope,
    RelationStatus,
    normalize_memory_text,
)
from memory_mcp.core.exceptions import (
    InvalidMemoryRelationError,
    InvalidModelOutputError,
)
from memory_mcp.core.ports import (
    MAX_RELATION_ENDPOINTS,
    MemoryProfile,
    MemoryRepository,
    ProfileRegistry,
    RelationExtractionRequest,
    RelationExtractor,
)
from memory_mcp.core.support import log_content_event, log_event

_LOGGER = logging.getLogger(__name__)

AUTO_RELATION_CONFIDENCE_THRESHOLD = 0.90
# 关系抽取有界重试上限（recommend.md §1），与 CandidateExtractor 对齐。仅对 InvalidModelOutputError
# （模型结构错误或 admit 校验拒绝）重试；全部 attempt 均有 rejected proposal 才让 capture 原子失败。
_RELATION_EXTRACTION_MAX_ATTEMPTS = 3
_NEGATED_RELATION_EVIDENCE = re.compile(
    r"(?:不|并不|不能|无法|未能|没有|并未|不再)\s*"
    r"(?:明确|直接|真正|足以|构成)?\s*"
    r"(?:支持|挑战|威胁|催化|推动|回答|解决)"
    r"|\b(?:does not|do not|did not|cannot|can not|can't|never|fails? to)\s+"
    r"(?:(?:clearly|directly|necessarily)\s+)?"
    r"(?:support|challenge|threaten|catalyze|address|resolve)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RejectedRelation:
    """被前置校验拒绝的关系建议，保留字段供开发 content 日志调试（recommend.md §1）。"""

    source_memory_id: UUID
    target_memory_id: UUID
    relation_type: str
    confidence: float
    source_expression: str
    reason_code: str


def _normalize_whitespace(value: str) -> str:
    """空白压成单空格 + trim（与 candidate 校验一致，recommend.md §1）。"""

    return " ".join(value.split())


def _normalize_compact(value: str) -> str:
    """移除全部 Unicode 空白，保留标点/数字/字符不改写。"""

    return "".join(value.split())


def _relation_source_expression_in(source_expression: str, source: str) -> bool:
    """三级空白归一化 containment（recommend.md §1）：raw -> whitespace -> compact。

    只忽略 Unicode 空白，不改标点/数字/字符；因此模型改写、增标点、拼接独立句仍判不匹配。
    """

    if source_expression in source:
        return True
    if _normalize_whitespace(source_expression) in _normalize_whitespace(source):
        return True
    return _normalize_compact(source_expression) in _normalize_compact(source)


@dataclass(frozen=True, slots=True)
class AutomaticRelationPlan:
    """一次关系抽取产出的计划：含准入通过的关系、原始建议与统计计数。"""

    endpoint_count: int = 0
    proposal_count: int = 0
    skipped_count: int = 0
    relations: tuple[MemoryRelation, ...] = ()
    proposals: tuple[RelationProposal, ...] = ()


class AutomaticRelationPlanner:
    """把模型关系建议转换为符合 Profile 合约的可信活动关系。"""

    def __init__(
        self,
        repository: MemoryRepository,
        profile_registry: ProfileRegistry,
        extractor: RelationExtractor,
        *,
        id_factory: Callable[[], UUID],
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._profile_registry = profile_registry
        self._extractor = extractor
        self._id_factory = id_factory
        self._clock = clock

    @property
    def model_id(self) -> str:
        return self._extractor.model_id

    @property
    def prompt_version(self) -> str:
        return self._extractor.prompt_version

    @property
    def schema_version(self) -> str:
        return self._extractor.schema_version

    def plan(
        self,
        principal: PrincipalContext,
        *,
        profile: MemoryProfile,
        capture_id: UUID,
        conversation_id: str,
        source_turn_id: str,
        redacted_source: str,
        observed_at: datetime,
        same_capture_memories: tuple[MemoryRecord, ...],
        subject_hint: str | None,
        trusted_user_sources: tuple[str, ...] | None,
    ) -> AutomaticRelationPlan:
        """在有合法端点组合时调用模型抽取关系，并保守准入通过的建议。

        对关系抽取做有界重试（recommend.md §1）：某次 attempt 产出被前置校验拒绝的 proposal
        （invalid_source_expression/endpoint/policy）时记 ``relation_validation_rejected`` 并重试；
        仅当全部 attempt 均有 rejected 才向上抛 ``InvalidModelOutputError``，由 capture 写
        ``incomplete``（保留原子失败安全语义，不静默忽略）。模型结构错误（InvalidModelOutputError）
        同样重试。
        """

        if not profile.relation_policies:
            return AutomaticRelationPlan()
        endpoint_records = self._select_endpoint_records(
            principal,
            profile=profile,
            redacted_source=redacted_source,
            same_capture_memories=same_capture_memories,
            effective_at=self._clock(),
        )
        endpoints = tuple(_endpoint(record) for record in endpoint_records)
        if not _has_compatible_pair(profile, endpoints):
            return AutomaticRelationPlan(endpoint_count=len(endpoints))
        request = RelationExtractionRequest(
            profile_id=profile.profile_id,
            content=redacted_source,
            observed_at=observed_at,
            profile_version=profile.profile_version,
            relation_policies=profile.relation_policies,
            endpoints=endpoints,
            subject_hint=subject_hint,
        )
        max_attempts = _RELATION_EXTRACTION_MAX_ATTEMPTS
        for attempt in range(1, max_attempts + 1):
            _attempt_started_at = perf_counter()
            log_event(
                _LOGGER,
                logging.INFO,
                "memory.capture.relation_extraction_attempt.started",
                capture_id=capture_id,
                attempt=attempt,
                max_attempts=max_attempts,
            )
            try:
                proposals = self._extractor.extract(request)
            except InvalidModelOutputError as exc:
                retryable = attempt < max_attempts
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    "memory.capture.relation_extraction_attempt.failed",
                    capture_id=capture_id,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    duration_ms=round((perf_counter() - _attempt_started_at) * 1000, 3),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    retryable=retryable,
                )
                if retryable:
                    continue
                raise
            accepted, skipped_count, rejected = self._admit(
                principal,
                profile=profile,
                capture_id=capture_id,
                conversation_id=conversation_id,
                source_turn_id=source_turn_id,
                redacted_source=redacted_source,
                endpoint_records=endpoint_records,
                proposals=proposals,
                trusted_user_sources=trusted_user_sources,
            )
            if not rejected:
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "memory.capture.relation_extraction_attempt.completed",
                    capture_id=capture_id,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    duration_ms=round((perf_counter() - _attempt_started_at) * 1000, 3),
                )
                return AutomaticRelationPlan(
                    endpoint_count=len(endpoints),
                    proposal_count=len(proposals),
                    skipped_count=skipped_count,
                    relations=accepted,
                    proposals=proposals,
                )
            # rejected 非空：记 rejected 详情 + failed，重试（保留原子失败安全语义）。
            log_content_event(
                "memory.capture.relation_validation_rejected",
                capture_id=capture_id,
                attempt=attempt,
                rejected=tuple(asdict(item) for item in rejected),
            )
            retryable = attempt < max_attempts
            log_event(
                _LOGGER,
                logging.WARNING,
                "memory.capture.relation_extraction_attempt.failed",
                capture_id=capture_id,
                attempt=attempt,
                max_attempts=max_attempts,
                duration_ms=round((perf_counter() - _attempt_started_at) * 1000, 3),
                error_type="InvalidModelOutputError",
                error_message="relation proposals rejected at validation",
                retryable=retryable,
            )
            if not retryable:
                break
        # 全部 attempt 均有 rejected proposal -> 原子失败，由 capture 写 incomplete。
        raise InvalidModelOutputError(
            "relation source_expression must occur in the redacted source turn"
        )

    def _select_endpoint_records(
        self,
        principal: PrincipalContext,
        *,
        profile: MemoryProfile,
        redacted_source: str,
        same_capture_memories: tuple[MemoryRecord, ...],
        effective_at: datetime,
    ) -> tuple[MemoryRecord, ...]:
        """选取关系端点：优先本轮产生的新记忆，再按相关性补充已存在的记忆。"""
        eligible_types = frozenset(
            memory_type
            for policy in profile.relation_policies.values()
            for memory_type in (
                *policy.source_memory_types,
                *policy.target_memory_types,
            )
        )
        selected_records: list[MemoryRecord] = []
        selected_ids: set[UUID] = set()
        for record in same_capture_memories:
            if (
                record.item.memory_type not in eligible_types
                or not _is_effective_record(
                    record,
                    effective_at,
                )
            ):
                continue
            selected_records.append(record)
            selected_ids.add(record.item.memory_id)

        existing = self._repository.find_current(
            principal,
            profile_id=profile.profile_id,
            effective_at=effective_at,
        )
        ranked_existing = sorted(
            (
                record
                for record in existing
                if record.item.memory_id not in selected_ids
                and record.item.memory_type in eligible_types
            ),
            key=lambda record: (
                _relevance_score(record, redacted_source),
                record.current_revision.observed_at,
                record.item.created_at,
                str(record.item.memory_id),
            ),
            reverse=True,
        )
        selected_records.extend(
            ranked_existing[: max(0, MAX_RELATION_ENDPOINTS - len(selected_records))]
        )
        return tuple(selected_records)

    def _admit(
        self,
        principal: PrincipalContext,
        *,
        profile: MemoryProfile,
        capture_id: UUID,
        conversation_id: str,
        source_turn_id: str,
        redacted_source: str,
        endpoint_records: tuple[MemoryRecord, ...],
        proposals: tuple[RelationProposal, ...],
        trusted_user_sources: tuple[str, ...] | None,
    ) -> tuple[tuple[MemoryRelation, ...], int, tuple[RejectedRelation, ...]]:
        """对模型建议逐条做保守准入校验。

        返回 ``(accepted, skipped_count, rejected)``：``rejected`` 为前置校验拒绝的 proposal
        （invalid_source_expression/endpoint_outside_catalog/policy_mismatch），由调用方决定重试或
        原子失败；``skipped_count`` 计合法但低置信/否定/反向/重复/非用户来源的跳过。不再就地 raise，
        以便单次 attempt 内收集全部 rejected 供调试日志（recommend.md §1）。
        """

        endpoint_by_id = {record.item.memory_id: record for record in endpoint_records}
        accepted: list[MemoryRelation] = []
        accepted_keys: set[tuple[UUID, UUID, str]] = set()
        skipped_count = 0
        rejected: list[RejectedRelation] = []
        for proposal in proposals:
            if not _relation_source_expression_in(
                proposal.source_expression, redacted_source
            ):
                rejected.append(
                    RejectedRelation(
                        source_memory_id=proposal.source_memory_id,
                        target_memory_id=proposal.target_memory_id,
                        relation_type=proposal.relation_type,
                        confidence=proposal.confidence,
                        source_expression=proposal.source_expression,
                        reason_code="invalid_source_expression",
                    )
                )
                continue
            source = endpoint_by_id.get(proposal.source_memory_id)
            target = endpoint_by_id.get(proposal.target_memory_id)
            if source is None or target is None:
                rejected.append(
                    RejectedRelation(
                        source_memory_id=proposal.source_memory_id,
                        target_memory_id=proposal.target_memory_id,
                        relation_type=proposal.relation_type,
                        confidence=proposal.confidence,
                        source_expression=proposal.source_expression,
                        reason_code="relation_endpoint_outside_catalog",
                    )
                )
                continue
            try:
                self._profile_registry.validate_relation(
                    profile.profile_id,
                    proposal.relation_type,
                    source.item.memory_type,
                    target.item.memory_type,
                )
            except InvalidMemoryRelationError:
                rejected.append(
                    RejectedRelation(
                        source_memory_id=proposal.source_memory_id,
                        target_memory_id=proposal.target_memory_id,
                        relation_type=proposal.relation_type,
                        confidence=proposal.confidence,
                        source_expression=proposal.source_expression,
                        reason_code="relation_policy_mismatch",
                    )
                )
                continue
            policy = profile.relation_policies[proposal.relation_type]
            key = (
                proposal.source_memory_id,
                proposal.target_memory_id,
                proposal.relation_type,
            )
            if (
                proposal.expression_basis is not ExpressionBasis.EXPLICIT
                or proposal.confidence < AUTO_RELATION_CONFIDENCE_THRESHOLD
                or _has_insufficient_endpoint_evidence(
                    proposal.source_expression,
                    source,
                    target,
                )
                or _has_negated_relation_evidence(proposal.source_expression)
                or _has_clearly_reversed_direction(
                    proposal.source_expression,
                    source,
                    target,
                    policy.direction_cues,
                )
                or key in accepted_keys
                or (
                    trusted_user_sources is not None
                    and not any(
                        proposal.source_expression in user_source
                        for user_source in trusted_user_sources
                    )
                )
            ):
                skipped_count += 1
                continue
            accepted_keys.add(key)
            accepted.append(
                MemoryRelation(
                    relation_id=self._id_factory(),
                    owner_id=principal.owner_id,
                    profile_id=profile.profile_id,
                    source_memory_id=proposal.source_memory_id,
                    target_memory_id=proposal.target_memory_id,
                    relation_type=proposal.relation_type,
                    status=RelationStatus.ACTIVE,
                    created_at=self._clock(),
                    origin=RelationOrigin.AUTOMATIC,
                    scope=RelationScope.REVISION,
                    source_revision_id=source.current_revision.revision_id,
                    target_revision_id=target.current_revision.revision_id,
                    provenance=RelationProvenance(
                        capture_id=capture_id,
                        conversation_id=conversation_id,
                        source_turn_id=source_turn_id,
                        source_expression=proposal.source_expression,
                        confidence=proposal.confidence,
                        expression_basis=proposal.expression_basis,
                        model_id=self.model_id,
                        prompt_version=self.prompt_version,
                        schema_version=self.schema_version,
                    ),
                )
            )
        return tuple(accepted), skipped_count, tuple(rejected)


def _endpoint(record: MemoryRecord) -> RelationEndpoint:
    """把记忆记录转换为关系抽取所需的端点描述。"""
    return RelationEndpoint(
        memory_id=record.item.memory_id,
        memory_type=record.item.memory_type,
        subject=record.item.subject,
        content=record.current_revision.content,
    )


def _is_effective_record(record: MemoryRecord, effective_at: datetime) -> bool:
    """判断记忆在给定时间点是否处于有效期内且状态为活动。"""
    revision = record.current_revision
    return (
        revision.lifecycle_status is LifecycleStatus.ACTIVE
        and revision.valid_from <= effective_at
        and (revision.valid_until is None or revision.valid_until > effective_at)
    )


def _has_compatible_pair(
    profile: MemoryProfile,
    endpoints: tuple[RelationEndpoint, ...],
) -> bool:
    """判断端点集合中是否存在某条关系策略允许的源-目标类型对。"""
    for policy in profile.relation_policies.values():
        for source in endpoints:
            if source.memory_type not in policy.source_memory_types:
                continue
            if any(
                target.memory_id != source.memory_id
                and target.memory_type in policy.target_memory_types
                for target in endpoints
            ):
                return True
    return False


def _relevance_score(record: MemoryRecord, source: str) -> int:
    """用 subject 命中与字符二元组交叠给端点打粗排分，用于选补充端点。"""
    source_key = normalize_memory_text(source)
    subject_key = normalize_memory_text(record.item.subject)
    content_key = normalize_memory_text(record.current_revision.content)
    score = 0
    if subject_key and subject_key in source_key:
        score += 10_000 + len(subject_key)
    score += len(_bigrams(source_key) & _bigrams(subject_key)) * 8
    score += len(_bigrams(source_key) & _bigrams(content_key))
    return score


def _bigrams(value: str) -> frozenset[str]:
    """生成去空格后的相邻字符二元组集合。"""
    compact = "".join(character for character in value if not character.isspace())
    if len(compact) < 2:
        return frozenset({compact}) if compact else frozenset()
    return frozenset(compact[index : index + 2] for index in range(len(compact) - 1))


def _has_negated_relation_evidence(source_expression: str) -> bool:
    """原文出现明确否定关系动词（如"不支持""does not challenge"）时拒绝自动建边。"""

    return _NEGATED_RELATION_EVIDENCE.search(source_expression) is not None


def _has_insufficient_endpoint_evidence(
    source_expression: str,
    source: MemoryRecord,
    target: MemoryRecord,
) -> bool:
    """原文对至少一端的端点文本匹配长度不足 2，视为证据不足。"""
    expression = _compact_text(source_expression)
    return (
        min(
            _endpoint_match_length(source, expression),
            _endpoint_match_length(target, expression),
        )
        < 2
    )


def _has_clearly_reversed_direction(
    source_expression: str,
    source: MemoryRecord,
    target: MemoryRecord,
    direction_cues: frozenset[str],
) -> bool:
    """当方向提示词两侧的端点文本更支持反向关系时，拒绝该建议方向。"""

    expression = _compact_text(source_expression)
    for raw_cue in direction_cues:
        cue = _compact_text(raw_cue)
        if not cue:
            continue
        offset = 0
        while (cue_at := expression.find(cue, offset)) >= 0:
            left = expression[:cue_at]
            right = expression[cue_at + len(cue) :]
            source_left = _endpoint_match_length(source, left)
            source_right = _endpoint_match_length(source, right)
            target_left = _endpoint_match_length(target, left)
            target_right = _endpoint_match_length(target, right)
            forward_score = source_left + target_right
            reverse_score = target_left + source_right
            if (
                min(target_left, source_right) >= 2
                and reverse_score >= forward_score + 3
            ):
                return True
            offset = cue_at + len(cue)
    return False


def _endpoint_match_length(record: MemoryRecord, text: str) -> int:
    """返回端点记忆的 subject/内容与给定文本的最长公共连续子串长度。"""
    return max(
        _longest_common_span(_compact_text(record.item.subject), text),
        _longest_common_span(_compact_text(record.current_revision.content), text),
    )


def _compact_text(value: str) -> str:
    """规范化文本并去掉非字母数字字符，便于忽略空格与标点的匹配。"""
    return "".join(
        character for character in normalize_memory_text(value) if character.isalnum()
    )


def _longest_common_span(left: str, right: str) -> int:
    """用动态规划求两段文本的最长公共连续子串长度。"""
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    longest = 0
    for left_character in left:
        current = [0]
        for index, right_character in enumerate(right, start=1):
            length = previous[index - 1] + 1 if left_character == right_character else 0
            current.append(length)
            longest = max(longest, length)
        previous = current
    return longest
