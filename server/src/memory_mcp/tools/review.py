"""待确认记忆候选的 MCP 工具：列表、确认与拒绝。"""

import asyncio
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from memory_mcp.auth import MemoryScope
from memory_mcp.schemas import (
    ErrorResponse,
    MemoryView,
    PendingReviewListReceipt,
    PendingReviewView,
    ReviewResolutionReceipt,
)
from memory_mcp.tools.shared import READ_ONLY, ToolSupport, request_id


class ReviewTools(ToolSupport):
    def _register_review(self, server: FastMCP[Any]) -> None:
        @server.tool(
            name="list_pending_reviews",
            description="List memory candidates awaiting this user's confirmation.",
            annotations=READ_ONLY,
        )
        async def list_pending_reviews(
            ctx: Context,
        ) -> PendingReviewListReceipt | ErrorResponse:
            current_request_id = request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.REVIEW)
                started_at = self._log_started(
                    current_request_id,
                    principal,
                    "list_pending_reviews",
                )
                reviews = await asyncio.to_thread(
                    self._service.list_pending_reviews,
                    principal.to_core(),
                )
                receipt = PendingReviewListReceipt(
                    request_id=current_request_id,
                    items=tuple(
                        PendingReviewView.from_review(review) for review in reviews
                    ),
                )
                self._log_completed(
                    current_request_id,
                    principal,
                    "list_pending_reviews",
                    started_at,
                    status="completed",
                    result_count=len(reviews),
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    current_request_id,
                    "list_pending_reviews",
                    exc,
                )

        @server.tool(
            name="confirm_pending_memory",
            description=(
                "Confirm one owned pending candidate exactly once. "
                "Set promote_to_team to write the memory as team-shared "
                "public knowledge instead of personal memory. "
                "Use only when the user explicitly asks to confirm a stored "
                "pending review; do not auto-confirm pending records based on "
                "ordinary conversation context."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def confirm_pending_memory(
            review_id: str,
            ctx: Context,
            promote_to_team: str | None = None,
        ) -> ReviewResolutionReceipt | ErrorResponse:
            current_request_id = request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.REVIEW)
                started_at = self._log_started(
                    current_request_id,
                    principal,
                    "confirm_pending_memory",
                )
                identifier = UUID(review_id)
                # promote_to_team 指定目标团队 ID：非空时把候选提升为团队共享记忆，
                # 并以当前 principal 的 team_owner_ids 作为合法团队所有者；为 None 则
                # 落地为个人记忆，不带团队所有者。
                memory = await asyncio.to_thread(
                    self._service.confirm_review,
                    principal.to_core(),
                    identifier,
                    team_id=promote_to_team,
                    team_owner_ids=frozenset(principal.team_owner_ids)
                    if promote_to_team is not None
                    else frozenset(),
                )
                receipt = ReviewResolutionReceipt(
                    request_id=current_request_id,
                    review_id=identifier,
                    status="confirmed",
                    memory=MemoryView.from_record(memory),
                )
                self._log_completed(
                    current_request_id,
                    principal,
                    "confirm_pending_memory",
                    started_at,
                    status="confirmed",
                    result_count=1,
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    current_request_id,
                    "confirm_pending_memory",
                    exc,
                )

        @server.tool(
            name="reject_pending_memory",
            description=(
                "Reject one owned pending candidate exactly once. "
                "Use only when the user explicitly asks to reject a stored "
                "pending review."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def reject_pending_memory(
            review_id: str,
            ctx: Context,
        ) -> ReviewResolutionReceipt | ErrorResponse:
            current_request_id = request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.REVIEW)
                started_at = self._log_started(
                    current_request_id,
                    principal,
                    "reject_pending_memory",
                )
                review = await asyncio.to_thread(
                    self._service.reject_review,
                    principal.to_core(),
                    UUID(review_id),
                )
                receipt = ReviewResolutionReceipt(
                    request_id=current_request_id,
                    review_id=review.review_id,
                    status="rejected",
                )
                self._log_completed(
                    current_request_id,
                    principal,
                    "reject_pending_memory",
                    started_at,
                    status="rejected",
                    result_count=1,
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    current_request_id,
                    "reject_pending_memory",
                    exc,
                )
