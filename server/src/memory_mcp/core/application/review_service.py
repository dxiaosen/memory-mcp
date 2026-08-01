"""待确认记忆用例协调器。"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from memory_mcp.core.application.candidate_processing import CandidateMaterializer
from memory_mcp.core.domain import (
    MemoryRecord,
    PrincipalContext,
    ReviewItem,
    ReviewStatus,
    VerificationStatus,
    normalize_memory_text,
)
from memory_mcp.core.exceptions import ReviewNotFoundError
from memory_mcp.core.ports import (
    DuplicateEvidenceWrite,
    MemoryRepository,
    ProfileRegistry,
    ReplacementWrite,
)
from memory_mcp.logging import log_content_event, log_event, stable_reference

_LOGGER = logging.getLogger(__name__)


class ReviewService:
    """在保持 Repository 原子性的前提下列出并处理待确认候选。"""

    def __init__(
        self,
        repository: MemoryRepository,
        profile_registry: ProfileRegistry,
        materializer: CandidateMaterializer,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._profile_registry = profile_registry
        self._materializer = materializer
        self._clock = clock

    def list_pending(
        self,
        principal: PrincipalContext,
    ) -> Sequence[ReviewItem]:
        reviews = self._repository.list_reviews(
            principal,
            status=ReviewStatus.PENDING,
        )
        log_content_event(
            "memory.review.list",
            reviews=tuple(asdict(review) for review in reviews),
        )
        return reviews

    def get(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem:
        review = self._repository.get_review(principal, review_id)
        if review is None:
            raise ReviewNotFoundError("review is unavailable")
        log_content_event(
            "memory.review.get",
            review=asdict(review),
        )
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
        self._profile_registry.validate_memory_type(
            review.candidate.profile_id,
            review.candidate.memory_type,
        )
        self._profile_registry.validate_business_progress(
            review.candidate.profile_id,
            review.candidate.business_progress,
        )
        current_scope = self._repository.find_current(
            principal,
            profile_id=review.candidate.profile_id,
            subject=review.candidate.subject,
            memory_type=review.candidate.memory_type,
            effective_at=self._clock(),
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
                    verification_status=VerificationStatus.USER_CONFIRMED,
                )
        else:
            memory = self._materializer.record(
                review.candidate,
                verification_status=VerificationStatus.USER_CONFIRMED,
            )
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
        log_content_event(
            "memory.review.confirmed",
            memory=asdict(committed),
            review=asdict(resolved),
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
        log_content_event(
            "memory.review.rejected",
            review=asdict(resolved),
        )
        return resolved
