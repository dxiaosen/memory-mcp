"""Pending-review use-case coordinator."""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import UUID

from memory_mcp.core.application.candidate_processing import CandidateMaterializer
from memory_mcp.core.domain import (
    MemoryRecord,
    PrincipalContext,
    ReviewItem,
    ReviewStatus,
    normalize_memory_text,
)
from memory_mcp.core.exceptions import ReviewNotFoundError
from memory_mcp.core.ports import (
    DuplicateEvidenceWrite,
    MemoryRepository,
    ReplacementWrite,
    ScenarioRegistry,
)
from memory_mcp.logging import log_event, stable_reference

_LOGGER = logging.getLogger(__name__)


class ReviewService:
    """List and resolve pending candidates while preserving repository atomics."""

    def __init__(
        self,
        repository: MemoryRepository,
        scenario_registry: ScenarioRegistry,
        materializer: CandidateMaterializer,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._scenario_registry = scenario_registry
        self._materializer = materializer
        self._clock = clock

    def list_pending(
        self,
        principal: PrincipalContext,
    ) -> Sequence[ReviewItem]:
        return self._repository.list_reviews(
            principal,
            status=ReviewStatus.PENDING,
        )

    def get(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem:
        review = self._repository.get_review(principal, review_id)
        if review is None:
            raise ReviewNotFoundError("review is unavailable")
        return review

    def confirm(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> MemoryRecord:
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
        current_scope = self._repository.find_current(
            principal,
            scenario=review.candidate.scenario,
            subject=review.candidate.subject,
            memory_type=review.candidate.memory_type,
        )
        if len(current_scope) > 1:
            raise ValueError("review lifecycle target is ambiguous")
        memory: MemoryRecord | None = None
        duplicate: DuplicateEvidenceWrite | None = None
        replacement: ReplacementWrite | None = None
        if current_scope:
            target = current_scope[0]
            if normalize_memory_text(
                target.current_revision.content
            ) == normalize_memory_text(review.candidate.content):
                duplicate = self._materializer.duplicate(
                    target,
                    review.candidate,
                )
            else:
                replacement = self._materializer.replacement(
                    target,
                    review.candidate,
                )
        else:
            memory = self._materializer.record(review.candidate)
        resolved = self._repository.resolve_review(
            principal,
            review_id,
            status=ReviewStatus.CONFIRMED,
            decided_at=self._clock(),
            memory=memory,
            duplicate_evidence=duplicate,
            replacement=replacement,
        )
        if resolved is None or resolved.resolved_memory_id is None:
            raise ReviewNotFoundError("review is unavailable")
        committed = self._repository.get(
            principal,
            resolved.resolved_memory_id,
        )
        if committed is None:
            raise ReviewNotFoundError("review is unavailable")
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.review.confirmed",
            memory_id=committed.item.memory_id,
            owner_ref=stable_reference(principal.owner_id),
            review_id=review_id,
        )
        return committed

    def reject(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem:
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
