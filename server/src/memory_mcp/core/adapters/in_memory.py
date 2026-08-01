"""供离线契约测试和演示使用的进程内 Repository。"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from memory_mcp.core.domain import (
    CaptureResult,
    CaptureStatus,
    Evidence,
    LifecycleStatus,
    MemoryHistoryEntry,
    MemoryRecord,
    MemoryRevision,
    PrincipalContext,
    ReviewItem,
    ReviewStatus,
    normalize_memory_text,
)
from memory_mcp.core.exceptions import (
    InvalidMemoryTypeError,
    ProfileNotRegisteredError,
)
from memory_mcp.core.ports import (
    CaptureWrite,
    DuplicateEvidenceWrite,
    MemoryProfile,
    ReplacementWrite,
)


class InMemoryMemoryRepository:
    """严格模拟 owner 范围和记忆配置类型约束，不作为生产存储。"""

    def __init__(self) -> None:
        self._records: dict[UUID, MemoryRecord] = {}
        self._history: dict[UUID, tuple[MemoryHistoryEntry, ...]] = {}
        self._profile_types: dict[str, frozenset[str]] = {}
        self._captures: dict[
            tuple[str, ...],
            CaptureResult,
        ] = {}
        self._reviews: dict[UUID, ReviewItem] = {}

    def register_profile(self, profile: MemoryProfile) -> None:
        self._profile_types[profile.profile_id] = frozenset(profile.memory_types)

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
        return tuple(
            sorted(
                records, key=lambda value: (value.item.created_at, value.item.memory_id)
            )
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
        profile_version: str,
        event_id: str | None = None,
    ) -> CaptureResult | None:
        return self._captures.get(
            self._capture_lookup_key(
                owner_id=principal.owner_id,
                profile_id=profile_id,
                conversation_id=conversation_id,
                source_turn_id=source_turn_id,
                profile_version=profile_version,
                event_id=event_id,
            )
        )

    def commit_capture(
        self,
        principal: PrincipalContext,
        write: CaptureWrite,
    ) -> None:
        result = write.result
        if result.owner_id != principal.owner_id:
            raise ValueError("capture owner must match trusted principal")
        if result.status is not CaptureStatus.COMPLETED and (
            write.memories
            or write.reviews
            or write.duplicate_evidence
            or write.replacements
        ):
            raise ValueError("failed capture cannot persist candidate content")
        key = self._capture_key(result)
        existing = self._captures.get(key)
        if existing is not None:
            if existing.status is not CaptureStatus.REPROCESS_REQUIRED:
                raise ValueError("completed capture cannot be replaced")
            if existing.capture_id != result.capture_id:
                raise ValueError("reprocessed capture must preserve capture_id")

        record_ids = {record.item.memory_id for record in write.memories}
        lifecycle_ids = {
            operation.memory_id
            for operation in (*write.duplicate_evidence, *write.replacements)
        }
        review_ids = {review.review_id for review in write.reviews}
        if len(record_ids) != len(write.memories):
            raise ValueError("capture contains duplicate memory ids")
        if len(review_ids) != len(write.reviews):
            raise ValueError("capture contains duplicate review ids")
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
        reviews.update((review.review_id, review) for review in write.reviews)
        captures[key] = result
        self._records = records
        self._history = history
        self._reviews = reviews
        self._captures = captures

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
        self._records = records
        self._history = history
        self._reviews = reviews
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
            profile_version=result.metadata.profile_version,
            event_id=result.event_id,
        )

    @staticmethod
    def _capture_lookup_key(
        *,
        owner_id: str,
        profile_id: str,
        conversation_id: str,
        source_turn_id: str,
        profile_version: str,
        event_id: str | None,
    ) -> tuple[str, ...]:
        if event_id is not None:
            return (
                owner_id,
                profile_id,
                "event",
                event_id,
                profile_version,
            )
        return (
            owner_id,
            profile_id,
            "legacy",
            conversation_id,
            source_turn_id,
            profile_version,
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
