"""MCP tool registration and mapping to the existing Memory application service."""

import asyncio
import logging
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from memory_mcp.core import (
    CaptureNotConfiguredError,
    IdempotencyConflictError,
    MemoryNotFoundError,
    MemoryService,
    ReviewNotFoundError,
    ScenarioNotRegisteredError,
)
from memory_mcp.logging import log_event, stable_reference
from memory_mcp.server.auth import (
    MemoryScope,
    RequestPrincipal,
    current_request_principal,
    require_scope,
)
from memory_mcp.server.errors import (
    ErrorCode,
    MemoryMcpBoundaryError,
    UnsupportedContractVersionError,
)
from memory_mcp.server.schemas import (
    CaptureReceipt,
    CompletedTurnEventV1,
    ErrorResponse,
    MemoryDetailReceipt,
    MemoryListReceipt,
    MemorySummaryView,
    MemoryView,
    PendingReviewListReceipt,
    PendingReviewView,
    ReviewResolutionReceipt,
    RoleMessageV1,
    decode_cursor,
    encode_cursor,
)
from memory_mcp.server.settings import MemoryServerSettings

_LOGGER = logging.getLogger(__name__)


class MemoryMcpTools:
    """Owner-safe MCP facade over one MemoryService instance."""

    def __init__(
        self,
        service: MemoryService,
        settings: MemoryServerSettings,
    ) -> None:
        self._service = service
        self._settings = settings

    def register(self, server: FastMCP[Any]) -> None:
        @server.tool(
            name="capture_completed_turn",
            description=(
                "Capture one successfully completed Agent turn. "
                "Owner identity is derived from the access token."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def capture_completed_turn(
            event_id: str,
            contract_version: str,
            scenario: str,
            conversation_id: str,
            turn_id: str,
            observed_at: datetime,
            messages: list[RoleMessageV1],
            ctx: Context,
            subject_hint: str | None = None,
        ) -> CaptureReceipt | ErrorResponse:
            request_id = _request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.WRITE)
                event = CompletedTurnEventV1.model_validate(
                    {
                        "event_id": event_id,
                        "contract_version": contract_version,
                        "scenario": scenario,
                        "conversation_id": conversation_id,
                        "turn_id": turn_id,
                        "observed_at": observed_at,
                        "messages": messages,
                        "subject_hint": subject_hint,
                    }
                )
                if event.contract_version != "1":
                    raise UnsupportedContractVersionError(event.contract_version)
                started_at = self._log_started(
                    request_id,
                    principal,
                    "capture_completed_turn",
                    event_id=event.event_id,
                )
                result = await asyncio.to_thread(
                    self._service.capture_turn,
                    principal.to_core(),
                    event.to_turn_envelope(
                        max_characters=self._settings.max_capture_characters
                    ),
                )
                receipt = CaptureReceipt.from_result(request_id, result)
                self._log_completed(
                    request_id,
                    principal,
                    "capture_completed_turn",
                    started_at,
                    status=result.status.value,
                    result_count=len(result.outcomes),
                )
                return receipt
            except Exception as exc:
                return self._error_response(request_id, "capture_completed_turn", exc)

        @server.tool(
            name="list_memories",
            description="List current active memories for the authenticated owner.",
            annotations=_READ_ONLY,
        )
        async def list_memories(
            ctx: Context,
            scenario: str | None = None,
            subject: str | None = None,
            memory_type: str | None = None,
            limit: int = 50,
            cursor: str | None = None,
        ) -> MemoryListReceipt | ErrorResponse:
            request_id = _request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.READ)
                if not 1 <= limit <= 100:
                    raise ValueError("limit must be between 1 and 100")
                offset = decode_cursor(cursor)
                started_at = self._log_started(
                    request_id,
                    principal,
                    "list_memories",
                )
                owner_records = await asyncio.to_thread(
                    self._service.list_memories,
                    principal.to_core(),
                )
                records = tuple(
                    record
                    for record in owner_records
                    if (scenario is None or record.item.scenario == scenario)
                    and (subject is None or record.item.subject == subject)
                    and (memory_type is None or record.item.memory_type == memory_type)
                )
                selected = records[offset : offset + limit]
                next_offset = offset + len(selected)
                receipt = MemoryListReceipt(
                    request_id=request_id,
                    items=tuple(
                        MemorySummaryView.from_record(item) for item in selected
                    ),
                    next_cursor=(
                        encode_cursor(next_offset)
                        if next_offset < len(records)
                        else None
                    ),
                )
                self._log_completed(
                    request_id,
                    principal,
                    "list_memories",
                    started_at,
                    status="completed",
                    result_count=len(selected),
                )
                return receipt
            except Exception as exc:
                return self._error_response(request_id, "list_memories", exc)

        @server.tool(
            name="get_memory",
            description=(
                "Get one current memory and its sources. "
                "Cross-owner identifiers are indistinguishable from missing ones."
            ),
            annotations=_READ_ONLY,
        )
        async def get_memory(
            memory_id: str,
            ctx: Context,
            include_history: bool = False,
        ) -> MemoryDetailReceipt | ErrorResponse:
            request_id = _request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.READ)
                started_at = self._log_started(
                    request_id,
                    principal,
                    "get_memory",
                )
                record = await asyncio.to_thread(
                    self._service.get_memory,
                    principal.to_core(),
                    UUID(memory_id),
                )
                receipt = MemoryDetailReceipt(
                    request_id=request_id,
                    item=MemoryView.from_record(record),
                    history_included=False,
                )
                self._log_completed(
                    request_id,
                    principal,
                    "get_memory",
                    started_at,
                    status="completed",
                    result_count=1,
                    include_history=include_history,
                )
                return receipt
            except Exception as exc:
                return self._error_response(request_id, "get_memory", exc)

        @server.tool(
            name="list_pending_reviews",
            description="List memory candidates awaiting this user's confirmation.",
            annotations=_READ_ONLY,
        )
        async def list_pending_reviews(
            ctx: Context,
        ) -> PendingReviewListReceipt | ErrorResponse:
            request_id = _request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.REVIEW)
                started_at = self._log_started(
                    request_id,
                    principal,
                    "list_pending_reviews",
                )
                reviews = await asyncio.to_thread(
                    self._service.list_pending_reviews,
                    principal.to_core(),
                )
                receipt = PendingReviewListReceipt(
                    request_id=request_id,
                    items=tuple(
                        PendingReviewView.from_review(review) for review in reviews
                    ),
                )
                self._log_completed(
                    request_id,
                    principal,
                    "list_pending_reviews",
                    started_at,
                    status="completed",
                    result_count=len(reviews),
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    request_id,
                    "list_pending_reviews",
                    exc,
                )

        @server.tool(
            name="confirm_pending_memory",
            description="Confirm one owned pending candidate exactly once.",
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
        ) -> ReviewResolutionReceipt | ErrorResponse:
            request_id = _request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.REVIEW)
                started_at = self._log_started(
                    request_id,
                    principal,
                    "confirm_pending_memory",
                )
                memory = await asyncio.to_thread(
                    self._service.confirm_review,
                    principal.to_core(),
                    UUID(review_id),
                )
                receipt = ReviewResolutionReceipt(
                    request_id=request_id,
                    review_id=UUID(review_id),
                    status="confirmed",
                    memory=MemoryView.from_record(memory),
                )
                self._log_completed(
                    request_id,
                    principal,
                    "confirm_pending_memory",
                    started_at,
                    status="confirmed",
                    result_count=1,
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    request_id,
                    "confirm_pending_memory",
                    exc,
                )

        @server.tool(
            name="reject_pending_memory",
            description="Reject one owned pending candidate exactly once.",
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
            request_id = _request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.REVIEW)
                started_at = self._log_started(
                    request_id,
                    principal,
                    "reject_pending_memory",
                )
                review = await asyncio.to_thread(
                    self._service.reject_review,
                    principal.to_core(),
                    UUID(review_id),
                )
                receipt = ReviewResolutionReceipt(
                    request_id=request_id,
                    review_id=review.review_id,
                    status="rejected",
                )
                self._log_completed(
                    request_id,
                    principal,
                    "reject_pending_memory",
                    started_at,
                    status="rejected",
                    result_count=1,
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    request_id,
                    "reject_pending_memory",
                    exc,
                )

        _enforce_strict_tool_arguments(server)

    @staticmethod
    def _authorize(required_scope: MemoryScope) -> RequestPrincipal:
        principal = current_request_principal()
        require_scope(principal, required_scope)
        return principal

    @staticmethod
    def _log_started(
        request_id: str,
        principal: RequestPrincipal,
        tool_name: str,
        *,
        event_id: str | None = None,
    ) -> float:
        started_at = perf_counter()
        fields: dict[str, object] = {
            "client_ref": stable_reference(principal.client_id),
            "owner_ref": stable_reference(principal.owner_key),
            "request_id": request_id,
            "tool_name": tool_name,
        }
        if event_id is not None:
            fields["event_ref"] = stable_reference(event_id)
        if principal.agent_id is not None:
            fields["agent_ref"] = stable_reference(principal.agent_id)
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.mcp.tool.started",
            **fields,
        )
        return started_at

    @staticmethod
    def _log_completed(
        request_id: str,
        principal: RequestPrincipal,
        tool_name: str,
        started_at: float,
        *,
        status: str,
        result_count: int,
        **fields: object,
    ) -> None:
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.mcp.tool.completed",
            client_ref=stable_reference(principal.client_id),
            agent_ref=(
                stable_reference(principal.agent_id)
                if principal.agent_id is not None
                else None
            ),
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            owner_ref=stable_reference(principal.owner_key),
            request_id=request_id,
            result_count=result_count,
            status=status,
            tool_name=tool_name,
            **fields,
        )

    @staticmethod
    def _error_response(
        request_id: str,
        tool_name: str,
        error: Exception,
    ) -> ErrorResponse:
        code, message, retryable = _map_error(error)
        log_event(
            _LOGGER,
            logging.WARNING,
            "memory.mcp.tool.failed",
            error_code=code.value,
            request_id=request_id,
            tool_name=tool_name,
        )
        return ErrorResponse(
            request_id=request_id,
            error_code=code,
            message=message,
            retryable=retryable,
        )


_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _request_id(context: Context) -> str:
    value = getattr(context, "request_id", None)
    return str(value) if value is not None else str(uuid4())


def _map_error(error: Exception) -> tuple[ErrorCode, str, bool]:
    if isinstance(error, MemoryMcpBoundaryError):
        return error.code, error.public_message, error.retryable
    if isinstance(error, ScenarioNotRegisteredError):
        return (
            ErrorCode.SCENARIO_NOT_REGISTERED,
            "The requested memory scenario is unavailable.",
            False,
        )
    if isinstance(error, IdempotencyConflictError):
        return (
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "The event identifier was already used for a different payload.",
            False,
        )
    if isinstance(error, MemoryNotFoundError):
        return ErrorCode.MEMORY_UNAVAILABLE, "Memory is unavailable.", False
    if isinstance(error, ReviewNotFoundError):
        return ErrorCode.REVIEW_UNAVAILABLE, "Review is unavailable.", False
    if isinstance(error, CaptureNotConfiguredError):
        return (
            ErrorCode.CAPTURE_NOT_CONFIGURED,
            "Memory capture is not configured on this server.",
            False,
        )
    if isinstance(error, ValueError):
        return ErrorCode.INVALID_EVENT, "The request payload is invalid.", False
    return (
        ErrorCode.TEMPORARILY_UNAVAILABLE,
        "The memory service is temporarily unavailable.",
        True,
    )


def _enforce_strict_tool_arguments(server: FastMCP[Any]) -> None:
    """Reject undeclared tool keys, including owner-like impersonation fields.

    MCP Python SDK 1.29 builds function argument models with Pydantic's default
    ``extra=ignore`` behavior. The project pins that SDK version and tightens the
    generated models here until FastMCP exposes a public strict-argument option.
    """

    for tool in server._tool_manager.list_tools():
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config = {
            **argument_model.model_config,
            "extra": "forbid",
        }
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)
