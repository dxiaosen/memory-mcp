"""待确认候选用例：列出、读取、确认（含团队提升）与拒绝待确认记忆。"""

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
    """管理待确认候选的生命周期：列出、确认（可提升到团队）或拒绝。"""

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
        """列出当前用户所有待确认的候选。"""
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
        """读取一条待确认候选的详情。"""
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
        *,
        team_id: str | None = None,
        team_owner_ids: frozenset[str] = frozenset(),
    ) -> MemoryRecord:
        """确认一条待确认候选并写入记忆。

        当传入 ``team_id`` 时执行团队提升：校验当前 principal 属于该团队，并把
        记忆的 owner 从个人改为团队，使记忆写入团队公共空间。确认时按现有记忆
        的情况决定是新建、追加重复证据还是生成替换。
        """
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
        # 团队提升：确定写入用的 owner。不反推 tenant_id，也不依赖
        # server.settings，而是直接从 principal 携带的 team_owner_ids 中匹配
        # 形如 ``:team:{team_id}`` 后缀的 owner key；匹配不到则视为无权写入。
        target_owner_id = principal.owner_id
        if team_id is not None:
            suffix = f":team:{team_id}"
            matched = tuple(owner for owner in team_owner_ids if owner.endswith(suffix))
            if not matched:
                raise ValueError("principal is not a member of the requested team")
            target_owner_id = matched[0]
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
                owner_id=target_owner_id,
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
            promoted_to_team=team_id is not None,
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
        """拒绝一条待确认候选，标记为已驳回（幂等）。"""
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
