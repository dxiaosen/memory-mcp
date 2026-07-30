"""供离线契约测试和演示使用的进程内 Repository。"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from agent_lab.memory.domain import (
    CaptureResult,
    CaptureStatus,
    LifecycleStatus,
    MemoryRecord,
    PrincipalContext,
    ReviewItem,
    ReviewStatus,
)
from agent_lab.memory.exceptions import (
    InvalidMemoryTypeError,
    ScenarioNotRegisteredError,
)
from agent_lab.memory.ports import CaptureWrite, ScenarioPolicy


class InMemoryMemoryRepository:
    """严格模拟 owner 范围和场景类型约束，不作为生产存储。"""

    def __init__(self) -> None:
        self._records: dict[UUID, MemoryRecord] = {}
        self._scenario_types: dict[str, frozenset[str]] = {}
        self._captures: dict[
            tuple[str, ...],
            CaptureResult,
        ] = {}
        self._reviews: dict[UUID, ReviewItem] = {}

    def register_scenario(self, policy: ScenarioPolicy) -> None:
        self._scenario_types[policy.scenario_id] = frozenset(policy.memory_types)

    def add(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        self._validate_record(principal, record)
        if record.item.memory_id in self._records:
            raise ValueError("memory_id must be unique")
        self._records[record.item.memory_id] = record

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
    ) -> Sequence[MemoryRecord]:
        records = (
            record
            for record in self._records.values()
            if record.item.owner_id == principal.owner_id
        )
        if active_only:
            records = (
                record
                for record in records
                if record.current_revision.lifecycle_status is LifecycleStatus.ACTIVE
            )
        return tuple(sorted(records, key=lambda value: value.item.created_at))

    def get_capture(
        self,
        principal: PrincipalContext,
        *,
        scenario: str,
        conversation_id: str,
        source_turn_id: str,
        policy_version: str,
        event_id: str | None = None,
    ) -> CaptureResult | None:
        return self._captures.get(
            self._capture_lookup_key(
                owner_id=principal.owner_id,
                scenario=scenario,
                conversation_id=conversation_id,
                source_turn_id=source_turn_id,
                policy_version=policy_version,
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
            write.memories or write.reviews
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
        review_ids = {review.review_id for review in write.reviews}
        if len(record_ids) != len(write.memories):
            raise ValueError("capture contains duplicate memory ids")
        if len(review_ids) != len(write.reviews):
            raise ValueError("capture contains duplicate review ids")
        for record in write.memories:
            self._validate_record(principal, record)
            if record.item.memory_id in self._records:
                raise ValueError("memory_id must be unique")
        for review in write.reviews:
            self._validate_review(principal, review)
            if review.review_id in self._reviews:
                raise ValueError("review_id must be unique")
        for outcome in result.outcomes:
            if outcome.memory_id is not None and outcome.memory_id not in record_ids:
                raise ValueError("capture outcome references unknown memory")
            if outcome.review_id is not None and outcome.review_id not in review_ids:
                raise ValueError("capture outcome references unknown review")

        records = dict(self._records)
        reviews = dict(self._reviews)
        captures = dict(self._captures)
        records.update((record.item.memory_id, record) for record in write.memories)
        reviews.update((review.review_id, review) for review in write.reviews)
        captures[key] = result
        self._records = records
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
    ) -> ReviewItem | None:
        review = self.get_review(principal, review_id)
        if review is None:
            return None
        if review.status is status:
            return review
        if review.status is not ReviewStatus.PENDING:
            return None
        if status is ReviewStatus.CONFIRMED:
            if memory is None:
                raise ValueError("confirmed review requires memory")
            self._validate_record(principal, memory)
            self._validate_review_memory(review, memory)
            if memory.item.memory_id in self._records:
                raise ValueError("memory_id must be unique")
        elif status is ReviewStatus.REJECTED:
            if memory is not None:
                raise ValueError("rejected review cannot create memory")
        else:
            raise ValueError("review resolution must be confirmed or rejected")

        resolved = replace(
            review,
            status=status,
            decided_at=decided_at,
            resolved_memory_id=(
                memory.item.memory_id
                if status is ReviewStatus.CONFIRMED and memory is not None
                else None
            ),
        )
        records = dict(self._records)
        reviews = dict(self._reviews)
        if memory is not None:
            records[memory.item.memory_id] = memory
        reviews[review_id] = resolved
        self._records = records
        self._reviews = reviews
        return resolved

    def _validate_record(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        if record.item.owner_id != principal.owner_id:
            raise ValueError("record owner must match trusted principal")
        scenario_types = self._scenario_types.get(record.item.scenario)
        if scenario_types is None:
            raise ScenarioNotRegisteredError(
                f"scenario is not registered: {record.item.scenario}"
            )
        if record.item.memory_type not in scenario_types:
            raise InvalidMemoryTypeError(
                "memory type is not registered for scenario "
                f"{record.item.scenario}: {record.item.memory_type}"
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
        scenario_types = self._scenario_types.get(review.candidate.scenario)
        if scenario_types is None or review.candidate.memory_type not in scenario_types:
            raise InvalidMemoryTypeError(
                "review memory type is not registered for scenario "
                f"{review.candidate.scenario}: {review.candidate.memory_type}"
            )

    @staticmethod
    def _capture_key(
        result: CaptureResult,
    ) -> tuple[str, ...]:
        return InMemoryMemoryRepository._capture_lookup_key(
            owner_id=result.owner_id,
            scenario=result.scenario,
            conversation_id=result.conversation_id,
            source_turn_id=result.source_turn_id,
            policy_version=result.metadata.policy_version,
            event_id=result.event_id,
        )

    @staticmethod
    def _capture_lookup_key(
        *,
        owner_id: str,
        scenario: str,
        conversation_id: str,
        source_turn_id: str,
        policy_version: str,
        event_id: str | None,
    ) -> tuple[str, ...]:
        if event_id is not None:
            return (
                owner_id,
                scenario,
                "event",
                event_id,
                policy_version,
            )
        return (
            owner_id,
            scenario,
            "legacy",
            conversation_id,
            source_turn_id,
            policy_version,
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
            or memory.item.scenario != candidate.scenario
            or memory.item.subject != candidate.subject
            or memory.item.memory_type != candidate.memory_type
            or revision.content != candidate.content
            or revision.assertion_kind is not candidate.assertion_kind
            or revision.business_progress != candidate.business_progress
            or revision.save_rationale != candidate.save_rationale
            or revision.observed_at != candidate.observed_at
            or revision.original_time_expression != candidate.original_time_expression
            or revision.normalized_time != candidate.normalized_time
            or source.conversation_id != candidate.conversation_id
            or source.source_turn_id != candidate.source_turn_id
            or source.source_expression != candidate.source_expression
            or source.source_role is not candidate.source_role
            or source.source_message_id != candidate.source_message_id
            or source.source_tool_name != candidate.source_tool_name
        ):
            raise ValueError("confirmed memory must match pending candidate")
