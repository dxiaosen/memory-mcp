"""供离线契约测试和演示使用的进程内 Repository。"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID

from memory_mcp.core.domain import (
    CaptureResult,
    CaptureStatus,
    Evidence,
    LifecycleStatus,
    MaintenanceResult,
    MemoryHistoryEntry,
    MemoryRecallCandidate,
    MemoryRecord,
    MemoryRelation,
    MemoryRelationSummary,
    MemoryRevision,
    PrincipalContext,
    RelationDirection,
    RelationOrigin,
    RelationScope,
    RelationStatus,
    ReviewItem,
    ReviewStatus,
    normalize_memory_text,
)
from memory_mcp.core.exceptions import (
    IdempotencyConflictError,
    InvalidMemoryTypeError,
    ProfileNotRegisteredError,
)
from memory_mcp.core.ports import (
    CaptureWrite,
    DuplicateEvidenceWrite,
    MemoryProfile,
    MemoryRelationPolicy,
    RecallCandidateSet,
    ReplacementWrite,
)


class InMemoryMemoryRepository:
    """严格模拟 owner 范围和记忆配置类型约束，不作为生产存储。"""

    def __init__(self) -> None:
        self._records: dict[UUID, MemoryRecord] = {}
        self._history: dict[UUID, tuple[MemoryHistoryEntry, ...]] = {}
        self._profile_types: dict[str, frozenset[str]] = {}
        self._profile_relation_policies: dict[
            str,
            dict[str, MemoryRelationPolicy],
        ] = {}
        self._captures: dict[
            tuple[str, ...],
            CaptureResult,
        ] = {}
        self._reviews: dict[UUID, ReviewItem] = {}
        self._relations: dict[UUID, MemoryRelation] = {}
        self._capture_lock = Lock()
        self._relation_lock = Lock()

    def register_profile(self, profile: MemoryProfile) -> None:
        self._profile_types[profile.profile_id] = frozenset(profile.memory_types)
        self._profile_relation_policies[profile.profile_id] = dict(
            profile.relation_policies
        )

    def add(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        self._validate_record(principal, record)
        if record.item.memory_id in self._records:
            raise ValueError("memory_id must be unique")
        self._records[record.item.memory_id] = record
        self._history[record.item.memory_id] = (
            MemoryHistoryEntry(
                revision=record.current_revision,
                evidence=record.evidence,
            ),
        )

    def get(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        record = self._records.get(memory_id)
        if record is None or record.item.owner_id != principal.owner_id:
            return None
        return record

    def list(
        self,
        principal: PrincipalContext,
        *,
        active_only: bool,
        effective_at: datetime | None = None,
    ) -> Sequence[MemoryRecord]:
        records = (
            record
            for record in self._records.values()
            if record.item.owner_id == principal.owner_id
        )
        if active_only:
            resolved_time = effective_at or datetime.now(UTC)
            records = (
                record
                for record in records
                if record.current_revision.lifecycle_status is LifecycleStatus.ACTIVE
                and _is_effective(record.current_revision, resolved_time)
            )
        return tuple(sorted(records, key=lambda value: value.item.created_at))

    def find_current(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        subject: str | None = None,
        memory_type: str | None = None,
        effective_at: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[MemoryRecord]:
        """先按可信 owner 和活动 current 集合收窄，再做规范化 subject 匹配。"""

        subject_key = normalize_memory_text(subject) if subject is not None else None
        resolved_time = effective_at or datetime.now(UTC)
        records = (
            record
            for record in self._records.values()
            if record.item.owner_id == principal.owner_id
            and record.item.profile_id == profile_id
            and record.current_revision.lifecycle_status is LifecycleStatus.ACTIVE
            and _is_effective(record.current_revision, resolved_time)
            and (memory_type is None or record.item.memory_type == memory_type)
            and (
                subject_key is None
                or normalize_memory_text(record.item.subject) == subject_key
            )
        )
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        ordered = sorted(
            records,
            key=lambda value: (
                value.current_revision.observed_at,
                value.item.memory_id,
            ),
            reverse=True,
        )
        return tuple(ordered[:limit] if limit is not None else ordered)

    def find_recall_candidates(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        search_text: str,
        subject: str | None,
        effective_at: datetime,
        limit: int,
    ) -> RecallCandidateSet:
        """模拟 PostgreSQL 的 lexical 配额和 recent 补齐。"""

        if limit < 1:
            raise ValueError("limit must be positive")
        normalized_search = normalize_memory_text(search_text)
        if not normalized_search:
            raise ValueError("search_text must not be empty")
        eligible = tuple(
            self.find_current(
                principal,
                profile_id=profile_id,
                subject=subject,
                effective_at=effective_at,
            )
        )
        lexical_limit = (
            1 if limit == 1 else min(limit - 1, max(1, (limit * 7 + 9) // 10))
        )
        scored = tuple(
            (
                max(
                    _trigram_similarity(search_text, record.item.subject),
                    _trigram_similarity(
                        search_text,
                        record.current_revision.content,
                    ),
                ),
                record,
            )
            for record in eligible
        )
        lexical = tuple(
            record
            for score, record in sorted(
                (value for value in scored if value[0] >= 0.08),
                key=lambda value: (
                    value[0],
                    value[1].current_revision.observed_at,
                    value[1].item.memory_id,
                ),
                reverse=True,
            )[:lexical_limit]
        )
        lexical_ids = {record.item.memory_id for record in lexical}
        recent_limit = limit - len(lexical)
        recent = tuple(
            record for record in eligible if record.item.memory_id not in lexical_ids
        )[:recent_limit]
        return RecallCandidateSet(
            candidates=tuple(
                MemoryRecallCandidate(
                    item=record.item,
                    current_revision=record.current_revision,
                )
                for record in (*lexical, *recent)
            ),
            lexical_count=len(lexical),
            recent_count=len(recent),
        )

    def load_recall_evidence(
        self,
        principal: PrincipalContext,
        *,
        revision_ids: Sequence[UUID],
        per_revision_limit: int,
    ) -> dict[UUID, tuple[Evidence, ...]]:
        """返回 selected owned revision 最近的有限来源。"""

        if per_revision_limit < 1:
            raise ValueError("per_revision_limit must be positive")
        requested = frozenset(revision_ids)
        return {
            record.current_revision.revision_id: record.evidence[-per_revision_limit:]
            for record in self._records.values()
            if record.item.owner_id == principal.owner_id
            and record.current_revision.revision_id in requested
        }

    def maintain(
        self,
        *,
        effective_at: datetime,
        review_cutoff: datetime,
        limit: int,
    ) -> MaintenanceResult:
        """按与 PostgreSQL 相同的批次配额物化终态。"""

        if limit < 1:
            raise ValueError("limit must be positive")
        memory_limit = (limit + 1) // 2
        review_limit = limit - memory_limit
        memory_targets = tuple(
            sorted(
                (
                    record
                    for record in self._records.values()
                    if record.current_revision.is_current
                    and record.current_revision.lifecycle_status
                    is LifecycleStatus.ACTIVE
                    and record.current_revision.valid_until is not None
                    and record.current_revision.valid_until <= effective_at
                ),
                key=lambda record: (
                    record.current_revision.valid_until,
                    record.current_revision.revision_id,
                ),
            )[:memory_limit]
        )
        expired_keys = {
            (record.item.owner_id, record.item.memory_id) for record in memory_targets
        }
        for record in memory_targets:
            revision = replace(
                record.current_revision,
                lifecycle_status=LifecycleStatus.EXPIRED,
            )
            self._records[record.item.memory_id] = replace(
                record,
                current_revision=revision,
            )
            self._history[record.item.memory_id] = tuple(
                replace(entry, revision=revision)
                if entry.revision.revision_id == revision.revision_id
                else entry
                for entry in self._history[record.item.memory_id]
            )

        stale_relation_count = 0
        with self._relation_lock:
            for relation_id, relation in tuple(self._relations.items()):
                if relation.status is not RelationStatus.ACTIVE:
                    continue
                if relation.created_at > effective_at:
                    continue
                endpoint_keys = {
                    (relation.owner_id, relation.source_memory_id),
                    (relation.owner_id, relation.target_memory_id),
                }
                if not expired_keys.intersection(endpoint_keys):
                    continue
                self._relations[relation_id] = replace(
                    relation,
                    status=RelationStatus.STALE,
                    stale_at=effective_at,
                    stale_reason="endpoint_expired",
                )
                stale_relation_count += 1

        review_targets = tuple(
            sorted(
                (
                    review
                    for review in self._reviews.values()
                    if review.status is ReviewStatus.PENDING
                    and (
                        (
                            review.candidate.valid_until is not None
                            and review.candidate.valid_until <= effective_at
                        )
                        or review.created_at <= review_cutoff
                    )
                ),
                key=lambda review: (
                    review.candidate.valid_until or review.created_at,
                    review.review_id,
                ),
            )[:review_limit]
        )
        for review in review_targets:
            self._reviews[review.review_id] = replace(
                review,
                status=ReviewStatus.EXPIRED,
                decided_at=effective_at,
            )
        return MaintenanceResult(
            effective_at=effective_at,
            expired_memory_count=len(memory_targets),
            expired_review_count=len(review_targets),
            stale_relation_count=stale_relation_count,
            has_more=(
                len(memory_targets) == memory_limit
                or (review_limit > 0 and len(review_targets) == review_limit)
            ),
        )

    def revoke(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        record = self.get(principal, memory_id)
        if record is None:
            return None
        revision = record.current_revision
        if revision.lifecycle_status is LifecycleStatus.REVOKED:
            return record
        if revision.lifecycle_status is not LifecycleStatus.ACTIVE:
            return None
        revoked = replace(revision, lifecycle_status=LifecycleStatus.REVOKED)
        updated = replace(record, current_revision=revoked)
        self._records[memory_id] = updated
        self._history[memory_id] = tuple(
            replace(entry, revision=revoked)
            if entry.revision.revision_id == revision.revision_id
            else entry
            for entry in self._history[memory_id]
        )
        return updated

    def link_relation(
        self,
        principal: PrincipalContext,
        relation: MemoryRelation,
        *,
        effective_at: datetime,
    ) -> MemoryRelation:
        if relation.origin is not RelationOrigin.MANUAL:
            raise ValueError("explicit relation write must be manual")
        self._validate_relation_write(
            principal,
            relation,
            records=self._records,
            effective_at=effective_at,
        )
        with self._relation_lock:
            for existing in self._relations.values():
                if (
                    existing.owner_id == principal.owner_id
                    and existing.source_memory_id == relation.source_memory_id
                    and existing.target_memory_id == relation.target_memory_id
                    and existing.relation_type == relation.relation_type
                    and existing.status is RelationStatus.ACTIVE
                ):
                    return existing
            if relation.relation_id in self._relations:
                raise ValueError("relation_id must be unique")
            self._relations[relation.relation_id] = relation
        return relation

    def revoke_relation(
        self,
        principal: PrincipalContext,
        relation_id: UUID,
        *,
        revoked_at: datetime,
    ) -> MemoryRelation | None:
        with self._relation_lock:
            relation = self._relations.get(relation_id)
            if relation is None or relation.owner_id != principal.owner_id:
                return None
            if relation.status is RelationStatus.REVOKED:
                return relation
            revoked = replace(
                relation,
                status=RelationStatus.REVOKED,
                revoked_at=revoked_at,
            )
            self._relations[relation_id] = revoked
            return revoked

    def list_relations(
        self,
        principal: PrincipalContext,
        *,
        memory_ids: Sequence[UUID],
        active_only: bool,
        effective_at: datetime | None = None,
    ) -> Sequence[MemoryRelationSummary]:
        requested = frozenset(memory_ids)
        if not requested:
            return ()
        resolved_time = effective_at or datetime.now(UTC)
        summaries: list[MemoryRelationSummary] = []
        for relation in self._relations.values():
            if relation.owner_id != principal.owner_id:
                continue
            if not (
                relation.source_memory_id in requested
                or relation.target_memory_id in requested
            ):
                continue
            source = self.get(principal, relation.source_memory_id)
            target = self.get(principal, relation.target_memory_id)
            if source is None or target is None:
                continue
            if active_only and (
                relation.status is not RelationStatus.ACTIVE
                or any(
                    record.current_revision.lifecycle_status
                    is not LifecycleStatus.ACTIVE
                    or not _is_effective(record.current_revision, resolved_time)
                    for record in (source, target)
                )
            ):
                continue
            if relation.source_memory_id in requested:
                summaries.append(
                    MemoryRelationSummary(
                        relation=relation,
                        direction=RelationDirection.OUTGOING,
                        related_memory_id=target.item.memory_id,
                        related_subject=target.item.subject,
                        related_memory_type=target.item.memory_type,
                    )
                )
            if relation.target_memory_id in requested:
                summaries.append(
                    MemoryRelationSummary(
                        relation=relation,
                        direction=RelationDirection.INCOMING,
                        related_memory_id=source.item.memory_id,
                        related_subject=source.item.subject,
                        related_memory_type=source.item.memory_type,
                    )
                )
        return tuple(
            sorted(
                summaries,
                key=lambda value: (
                    value.relation.created_at,
                    value.relation.relation_id,
                    value.direction.value,
                ),
            )
        )

    def get_history(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> Sequence[MemoryHistoryEntry]:
        record = self._records.get(memory_id)
        if record is None or record.item.owner_id != principal.owner_id:
            return ()
        return tuple(
            sorted(
                self._history[memory_id],
                key=lambda value: value.revision.revision_number,
                reverse=True,
            )
        )

    def get_capture(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        conversation_id: str,
        source_turn_id: str,
        event_id: str | None = None,
    ) -> CaptureResult | None:
        return self._captures.get(
            self._capture_lookup_key(
                owner_id=principal.owner_id,
                profile_id=profile_id,
                conversation_id=conversation_id,
                source_turn_id=source_turn_id,
                event_id=event_id,
            )
        )

    def commit_capture(
        self,
        principal: PrincipalContext,
        write: CaptureWrite,
    ) -> CaptureResult:
        with self._capture_lock:
            return self._commit_capture_locked(principal, write)

    def _commit_capture_locked(
        self,
        principal: PrincipalContext,
        write: CaptureWrite,
    ) -> CaptureResult:
        result = write.result
        if result.owner_id != principal.owner_id:
            raise ValueError("capture owner must match trusted principal")
        if result.status is not CaptureStatus.COMPLETED and (
            write.memories
            or write.reviews
            or write.duplicate_evidence
            or write.replacements
            or write.relations
        ):
            raise ValueError("failed capture cannot persist candidate content")
        key = self._capture_key(result)
        existing = self._captures.get(key)
        if existing is not None:
            if (
                result.payload_fingerprint is not None
                and existing.payload_fingerprint != result.payload_fingerprint
            ):
                raise IdempotencyConflictError(
                    "event identifier was reused with a different payload"
                )
            if existing.status is not CaptureStatus.REPROCESS_REQUIRED:
                return replace(existing, replayed=True)
            if existing.capture_id != result.capture_id:
                raise ValueError("reprocessed capture must preserve capture_id")

        record_ids = {record.item.memory_id for record in write.memories}
        lifecycle_ids = {
            operation.memory_id
            for operation in (*write.duplicate_evidence, *write.replacements)
        }
        review_ids = {review.review_id for review in write.reviews}
        relation_ids = {relation.relation_id for relation in write.relations}
        if len(record_ids) != len(write.memories):
            raise ValueError("capture contains duplicate memory ids")
        if len(review_ids) != len(write.reviews):
            raise ValueError("capture contains duplicate review ids")
        if len(relation_ids) != len(write.relations):
            raise ValueError("capture contains duplicate relation ids")
        if len(lifecycle_ids) != (
            len(write.duplicate_evidence) + len(write.replacements)
        ):
            raise ValueError("capture contains conflicting lifecycle writes")
        if record_ids & lifecycle_ids:
            raise ValueError("new memories cannot also be lifecycle targets")
        for record in write.memories:
            self._validate_record(principal, record)
            if record.item.memory_id in self._records:
                raise ValueError("memory_id must be unique")
        for review in write.reviews:
            self._validate_review(principal, review)
            if review.review_id in self._reviews:
                raise ValueError("review_id must be unique")
        for outcome in result.outcomes:
            if (
                outcome.memory_id is not None
                and outcome.memory_id not in record_ids | lifecycle_ids
            ):
                raise ValueError("capture outcome references unknown memory")
            if outcome.review_id is not None and outcome.review_id not in review_ids:
                raise ValueError("capture outcome references unknown review")

        records = dict(self._records)
        history = dict(self._history)
        reviews = dict(self._reviews)
        captures = dict(self._captures)
        for record in write.memories:
            records[record.item.memory_id] = record
            history[record.item.memory_id] = (
                MemoryHistoryEntry(
                    revision=record.current_revision,
                    evidence=record.evidence,
                ),
            )
        for duplicate in write.duplicate_evidence:
            current = self._require_lifecycle_target(
                records,
                principal,
                duplicate.memory_id,
                duplicate.expected_revision_id,
            )
            self._validate_new_evidence(
                principal,
                current.current_revision,
                duplicate.evidence,
            )
            updated = replace(
                current,
                evidence=(*current.evidence, duplicate.evidence),
            )
            records[duplicate.memory_id] = updated
            history[duplicate.memory_id] = tuple(
                replace(entry, evidence=updated.evidence)
                if entry.revision.revision_id == duplicate.expected_revision_id
                else entry
                for entry in history[duplicate.memory_id]
            )
        for replacement in write.replacements:
            current = self._require_lifecycle_target(
                records,
                principal,
                replacement.memory_id,
                replacement.expected_revision_id,
            )
            self._validate_replacement(principal, current, replacement)
            superseded = replace(
                current.current_revision,
                lifecycle_status=LifecycleStatus.SUPERSEDED,
                is_current=False,
            )
            new_record = MemoryRecord(
                item=current.item,
                current_revision=replacement.revision,
                evidence=replacement.evidence,
            )
            records[replacement.memory_id] = new_record
            history[replacement.memory_id] = (
                *(
                    replace(entry, revision=superseded)
                    if entry.revision.revision_id == replacement.expected_revision_id
                    else entry
                    for entry in history[replacement.memory_id]
                ),
                MemoryHistoryEntry(
                    revision=replacement.revision,
                    evidence=replacement.evidence,
                ),
            )
        with self._relation_lock:
            relations = dict(self._relations)
            for replacement in write.replacements:
                relations = _stale_revision_relations(
                    relations,
                    principal,
                    replacement,
                )
            for relation in write.relations:
                provenance = relation.provenance
                if (
                    relation.origin is not RelationOrigin.AUTOMATIC
                    or provenance is None
                    or provenance.capture_id != result.capture_id
                    or provenance.conversation_id != result.conversation_id
                    or provenance.source_turn_id != result.source_turn_id
                ):
                    raise ValueError(
                        "capture relation provenance must match capture result"
                    )
                self._validate_relation_write(
                    principal,
                    relation,
                    records=records,
                    effective_at=relation.created_at,
                )
                duplicate = any(
                    existing_relation.owner_id == principal.owner_id
                    and existing_relation.source_memory_id == relation.source_memory_id
                    and existing_relation.target_memory_id == relation.target_memory_id
                    and existing_relation.relation_type == relation.relation_type
                    and existing_relation.status is RelationStatus.ACTIVE
                    for existing_relation in relations.values()
                )
                if duplicate:
                    continue
                if relation.relation_id in relations:
                    raise ValueError("relation_id must be unique")
                relations[relation.relation_id] = relation
            reviews.update((review.review_id, review) for review in write.reviews)
            captures[key] = result
            self._records = records
            self._history = history
            self._reviews = reviews
            self._captures = captures
            self._relations = relations
        return result

    def list_reviews(
        self,
        principal: PrincipalContext,
        *,
        status: ReviewStatus,
    ) -> Sequence[ReviewItem]:
        return tuple(
            sorted(
                (
                    review
                    for review in self._reviews.values()
                    if review.owner_id == principal.owner_id and review.status is status
                ),
                key=lambda value: (value.created_at, value.review_id),
            )
        )

    def get_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem | None:
        review = self._reviews.get(review_id)
        if review is None or review.owner_id != principal.owner_id:
            return None
        return review

    def resolve_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
        *,
        status: ReviewStatus,
        decided_at: datetime,
        memory: MemoryRecord | None = None,
        duplicate_evidence: DuplicateEvidenceWrite | None = None,
        replacement: ReplacementWrite | None = None,
    ) -> ReviewItem | None:
        review = self.get_review(principal, review_id)
        if review is None:
            return None
        if review.status is status:
            return review
        if review.status is not ReviewStatus.PENDING:
            return None
        if status is ReviewStatus.CONFIRMED:
            writes = tuple(
                value
                for value in (memory, duplicate_evidence, replacement)
                if value is not None
            )
            if len(writes) != 1:
                raise ValueError("confirmed review requires one memory write")
            if memory is not None:
                self._validate_record(principal, memory)
                self._validate_review_memory(review, memory)
                if memory.item.memory_id in self._records:
                    raise ValueError("memory_id must be unique")
        elif status is ReviewStatus.REJECTED:
            if any(
                value is not None for value in (memory, duplicate_evidence, replacement)
            ):
                raise ValueError("rejected review cannot create memory")
        else:
            raise ValueError("review resolution must be confirmed or rejected")

        resolved_memory_id = (
            memory.item.memory_id
            if memory is not None
            else (
                duplicate_evidence.memory_id
                if duplicate_evidence is not None
                else replacement.memory_id
                if replacement is not None
                else None
            )
        )
        resolved = replace(
            review,
            status=status,
            decided_at=decided_at,
            resolved_memory_id=(
                resolved_memory_id if status is ReviewStatus.CONFIRMED else None
            ),
        )
        records = dict(self._records)
        history = dict(self._history)
        reviews = dict(self._reviews)
        if memory is not None:
            records[memory.item.memory_id] = memory
            history[memory.item.memory_id] = (
                MemoryHistoryEntry(
                    revision=memory.current_revision,
                    evidence=memory.evidence,
                ),
            )
        if duplicate_evidence is not None:
            current = self._require_lifecycle_target(
                records,
                principal,
                duplicate_evidence.memory_id,
                duplicate_evidence.expected_revision_id,
            )
            self._validate_new_evidence(
                principal,
                current.current_revision,
                duplicate_evidence.evidence,
            )
            updated = replace(
                current,
                evidence=(*current.evidence, duplicate_evidence.evidence),
            )
            records[duplicate_evidence.memory_id] = updated
            history[duplicate_evidence.memory_id] = tuple(
                replace(entry, evidence=updated.evidence)
                if entry.revision.revision_id == duplicate_evidence.expected_revision_id
                else entry
                for entry in history[duplicate_evidence.memory_id]
            )
        if replacement is not None:
            current = self._require_lifecycle_target(
                records,
                principal,
                replacement.memory_id,
                replacement.expected_revision_id,
            )
            self._validate_replacement(principal, current, replacement)
            superseded = replace(
                current.current_revision,
                lifecycle_status=LifecycleStatus.SUPERSEDED,
                is_current=False,
            )
            records[replacement.memory_id] = MemoryRecord(
                item=current.item,
                current_revision=replacement.revision,
                evidence=replacement.evidence,
            )
            history[replacement.memory_id] = (
                *(
                    replace(entry, revision=superseded)
                    if entry.revision.revision_id == replacement.expected_revision_id
                    else entry
                    for entry in history[replacement.memory_id]
                ),
                MemoryHistoryEntry(
                    revision=replacement.revision,
                    evidence=replacement.evidence,
                ),
            )
        reviews[review_id] = resolved
        with self._relation_lock:
            relations = dict(self._relations)
            if replacement is not None:
                relations = _stale_revision_relations(
                    relations,
                    principal,
                    replacement,
                )
            self._records = records
            self._history = history
            self._reviews = reviews
            self._relations = relations
        return resolved

    @staticmethod
    def _require_lifecycle_target(
        records: dict[UUID, MemoryRecord],
        principal: PrincipalContext,
        memory_id: UUID,
        expected_revision_id: UUID,
    ) -> MemoryRecord:
        record = records.get(memory_id)
        if (
            record is None
            or record.item.owner_id != principal.owner_id
            or record.current_revision.revision_id != expected_revision_id
            or record.current_revision.lifecycle_status is not LifecycleStatus.ACTIVE
        ):
            raise ValueError("lifecycle target is no longer current")
        return record

    @staticmethod
    def _validate_new_evidence(
        principal: PrincipalContext,
        revision: MemoryRevision,
        evidence: Evidence,
    ) -> None:
        if (
            evidence.owner_id != principal.owner_id
            or evidence.memory_id != revision.memory_id
            or evidence.revision_id != revision.revision_id
        ):
            raise ValueError("duplicate evidence must match current revision")

    @staticmethod
    def _validate_replacement(
        principal: PrincipalContext,
        current: MemoryRecord,
        replacement,
    ) -> None:
        revision = replacement.revision
        if (
            revision.owner_id != principal.owner_id
            or revision.memory_id != current.item.memory_id
            or revision.revision_number != current.current_revision.revision_number + 1
            or not revision.is_current
            or revision.lifecycle_status is not LifecycleStatus.ACTIVE
            or not replacement.evidence
        ):
            raise ValueError("replacement revision is invalid")
        for source in replacement.evidence:
            InMemoryMemoryRepository._validate_new_evidence(
                principal,
                revision,
                source,
            )

    def _validate_relation_write(
        self,
        principal: PrincipalContext,
        relation: MemoryRelation,
        *,
        records: dict[UUID, MemoryRecord],
        effective_at: datetime,
    ) -> None:
        if relation.owner_id != principal.owner_id:
            raise ValueError("relation owner must match trusted principal")
        if relation.status is not RelationStatus.ACTIVE:
            raise ValueError("new relation must be active")
        if relation.origin is RelationOrigin.LEGACY:
            raise ValueError("new relation cannot use legacy origin")
        source = records.get(relation.source_memory_id)
        target = records.get(relation.target_memory_id)
        if (
            source is None
            or target is None
            or source.item.owner_id != principal.owner_id
            or target.item.owner_id != principal.owner_id
        ):
            raise ValueError("relation endpoints are unavailable")
        if (
            source.item.profile_id != relation.profile_id
            or target.item.profile_id != relation.profile_id
        ):
            raise ValueError("relation endpoints must share the relation profile")
        policy = self._profile_relation_policies.get(
            relation.profile_id,
            {},
        ).get(relation.relation_type)
        if (
            policy is None
            or source.item.memory_type not in policy.source_memory_types
            or target.item.memory_type not in policy.target_memory_types
        ):
            raise ValueError("relation does not match the registered policy")
        for record in (source, target):
            revision = record.current_revision
            if (
                revision.lifecycle_status is not LifecycleStatus.ACTIVE
                or not _is_effective(revision, effective_at)
            ):
                raise ValueError("relation endpoints must be active and effective")
        if (
            relation.source_revision_id != source.current_revision.revision_id
            or relation.target_revision_id != target.current_revision.revision_id
        ):
            raise ValueError("relation revision snapshots must match current endpoints")

    def _validate_record(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        if record.item.owner_id != principal.owner_id:
            raise ValueError("record owner must match trusted principal")
        profile_types = self._profile_types.get(record.item.profile_id)
        if profile_types is None:
            raise ProfileNotRegisteredError(
                f"profile_id is not registered: {record.item.profile_id}"
            )
        if record.item.memory_type not in profile_types:
            raise InvalidMemoryTypeError(
                "memory type is not registered for profile_id "
                f"{record.item.profile_id}: {record.item.memory_type}"
            )

    def _validate_review(
        self,
        principal: PrincipalContext,
        review: ReviewItem,
    ) -> None:
        if review.owner_id != principal.owner_id:
            raise ValueError("review owner must match trusted principal")
        if review.status is not ReviewStatus.PENDING:
            raise ValueError("new review must be pending")
        profile_types = self._profile_types.get(review.candidate.profile_id)
        if profile_types is None or review.candidate.memory_type not in profile_types:
            raise InvalidMemoryTypeError(
                "review memory type is not registered for profile_id "
                f"{review.candidate.profile_id}: {review.candidate.memory_type}"
            )

    @staticmethod
    def _capture_key(
        result: CaptureResult,
    ) -> tuple[str, ...]:
        return InMemoryMemoryRepository._capture_lookup_key(
            owner_id=result.owner_id,
            profile_id=result.profile_id,
            conversation_id=result.conversation_id,
            source_turn_id=result.source_turn_id,
            event_id=result.event_id,
        )

    @staticmethod
    def _capture_lookup_key(
        *,
        owner_id: str,
        profile_id: str,
        conversation_id: str,
        source_turn_id: str,
        event_id: str | None,
    ) -> tuple[str, ...]:
        if event_id is not None:
            return (
                owner_id,
                "event",
                event_id,
            )
        return (
            owner_id,
            profile_id,
            "legacy",
            conversation_id,
            source_turn_id,
        )

    @staticmethod
    def _validate_review_memory(
        review: ReviewItem,
        memory: MemoryRecord,
    ) -> None:
        candidate = review.candidate
        revision = memory.current_revision
        source = memory.evidence[0]
        if (
            memory.item.owner_id != candidate.owner_id
            or memory.item.profile_id != candidate.profile_id
            or memory.item.subject != candidate.subject
            or memory.item.memory_type != candidate.memory_type
            or revision.content != candidate.content
            or revision.assertion_kind is not candidate.assertion_kind
            or revision.business_progress != candidate.business_progress
            or revision.save_rationale != candidate.save_rationale
            or revision.observed_at != candidate.observed_at
            or revision.extraction_confidence != candidate.confidence
            or revision.verification_status.value != "user_confirmed"
            or revision.sensitivity_level is not candidate.sensitivity_level
            or revision.valid_from != candidate.valid_from
            or revision.valid_until != candidate.valid_until
            or revision.last_verified_at != candidate.last_verified_at
            or revision.original_time_expression != candidate.original_time_expression
            or revision.normalized_time != candidate.normalized_time
            or source.conversation_id != candidate.conversation_id
            or source.source_turn_id != candidate.source_turn_id
            or source.source_expression != candidate.source_expression
            or source.source_role is not candidate.source_role
            or source.source_message_id != candidate.source_message_id
            or source.source_tool_name != candidate.source_tool_name
            or source.source_type is not candidate.source_type
            or source.source_uri != candidate.source_uri
            or source.source_title != candidate.source_title
            or source.source_publisher != candidate.source_publisher
            or source.published_at != candidate.published_at
            or source.retrieved_at != candidate.retrieved_at
            or source.content_hash != candidate.content_hash
            or source.citation_locator != candidate.citation_locator
        ):
            raise ValueError("confirmed memory must match pending candidate")


def _is_effective(revision: MemoryRevision, at_time: datetime) -> bool:
    return revision.valid_from <= at_time and (
        revision.valid_until is None or revision.valid_until > at_time
    )


def _trigram_similarity(left: str, right: str) -> float:
    """用于 InMemory 契约测试的稳定 trigram 近似。"""

    left_trigrams = _trigrams(normalize_memory_text(left))
    right_trigrams = _trigrams(normalize_memory_text(right))
    if not left_trigrams or not right_trigrams:
        return 0.0
    return len(left_trigrams & right_trigrams) / max(
        len(left_trigrams),
        len(right_trigrams),
    )


def _trigrams(value: str) -> frozenset[str]:
    compact = value.replace(" ", "")
    if len(compact) < 3:
        return frozenset({compact}) if compact else frozenset()
    return frozenset(compact[index : index + 3] for index in range(len(compact) - 2))


def _stale_revision_relations(
    relations: dict[UUID, MemoryRelation],
    principal: PrincipalContext,
    replacement: ReplacementWrite,
) -> dict[UUID, MemoryRelation]:
    """返回物化 replacement 失效边后的关系副本。"""

    stale_at = replacement.revision.created_at
    current_revision_id = replacement.revision.revision_id
    return {
        relation_id: (
            replace(
                relation,
                status=RelationStatus.STALE,
                stale_at=stale_at,
                stale_reason="endpoint_revision_changed",
            )
            if relation.owner_id == principal.owner_id
            and relation.scope is RelationScope.REVISION
            and relation.status is RelationStatus.ACTIVE
            and (
                (
                    relation.source_memory_id == replacement.memory_id
                    and relation.source_revision_id != current_revision_id
                )
                or (
                    relation.target_memory_id == replacement.memory_id
                    and relation.target_revision_id != current_revision_id
                )
            )
            else relation
        )
        for relation_id, relation in relations.items()
    }
