"""Profile 驱动的自动关系端点选择与保守准入。"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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

AUTO_RELATION_CONFIDENCE_THRESHOLD = 0.90


@dataclass(frozen=True, slots=True)
class AutomaticRelationPlan:
    """一次可选关系抽取产生的可信写入。"""

    endpoint_count: int = 0
    proposal_count: int = 0
    skipped_count: int = 0
    relations: tuple[MemoryRelation, ...] = ()
    proposals: tuple[RelationProposal, ...] = ()


class AutomaticRelationPlanner:
    """只把模型建议转换为符合 Profile 合同的活动关系。"""

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
        """在有合法端点组合时抽取，并保守准入关系。"""

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
        proposals = self._extractor.extract(request)
        relations, skipped_count = self._admit(
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
        return AutomaticRelationPlan(
            endpoint_count=len(endpoints),
            proposal_count=len(proposals),
            skipped_count=skipped_count,
            relations=relations,
            proposals=proposals,
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
    ) -> tuple[tuple[MemoryRelation, ...], int]:
        endpoint_by_id = {record.item.memory_id: record for record in endpoint_records}
        accepted: list[MemoryRelation] = []
        accepted_keys: set[tuple[UUID, UUID, str]] = set()
        skipped_count = 0
        for proposal in proposals:
            if proposal.source_expression not in redacted_source:
                raise InvalidModelOutputError(
                    "relation source_expression must occur in the redacted source turn"
                )
            source = endpoint_by_id.get(proposal.source_memory_id)
            target = endpoint_by_id.get(proposal.target_memory_id)
            if source is None or target is None:
                raise InvalidModelOutputError(
                    "relation endpoint is outside the trusted catalog"
                )
            try:
                self._profile_registry.validate_relation(
                    profile.profile_id,
                    proposal.relation_type,
                    source.item.memory_type,
                    target.item.memory_type,
                )
            except InvalidMemoryRelationError as exc:
                raise InvalidModelOutputError(
                    "relation does not match the trusted profile policy"
                ) from exc
            key = (
                proposal.source_memory_id,
                proposal.target_memory_id,
                proposal.relation_type,
            )
            if (
                proposal.expression_basis is not ExpressionBasis.EXPLICIT
                or proposal.confidence < AUTO_RELATION_CONFIDENCE_THRESHOLD
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
        return tuple(accepted), skipped_count


def _endpoint(record: MemoryRecord) -> RelationEndpoint:
    return RelationEndpoint(
        memory_id=record.item.memory_id,
        memory_type=record.item.memory_type,
        subject=record.item.subject,
        content=record.current_revision.content,
    )


def _is_effective_record(record: MemoryRecord, effective_at: datetime) -> bool:
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
    compact = "".join(character for character in value if not character.isspace())
    if len(compact) < 2:
        return frozenset({compact}) if compact else frozenset()
    return frozenset(compact[index : index + 2] for index in range(len(compact) - 1))
