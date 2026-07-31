"""MCP 工具模块共享的认证、日志、错误映射和 schema 收紧。"""

import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from memory_mcp.auth import (
    MemoryScope,
    RequestPrincipal,
    current_request_principal,
    require_scope,
)
from memory_mcp.core import (
    CaptureNotConfiguredError,
    IdempotencyConflictError,
    MemoryNotFoundError,
    MemoryService,
    ProfileNotRegisteredError,
    ReviewNotFoundError,
)
from memory_mcp.errors import ErrorCode, MemoryMcpBoundaryError
from memory_mcp.logging import log_event, stable_reference
from memory_mcp.schemas import ErrorResponse
from memory_mcp.settings import MemoryServerSettings

_LOGGER = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class ToolSupport:
    """各工具组共享的无状态边界能力。"""

    def __init__(
        self,
        service: MemoryService,
        settings: MemoryServerSettings,
    ) -> None:
        self._service = service
        self._settings = settings

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
            error_type=type(error).__name__,
            request_id=request_id,
            tool_name=tool_name,
        )
        return ErrorResponse(
            request_id=request_id,
            error_code=code,
            message=message,
            retryable=retryable,
        )


def request_id(context: Context) -> str:
    value = getattr(context, "request_id", None)
    return str(value) if value is not None else str(uuid4())


def enforce_strict_tool_arguments(server: FastMCP[Any]) -> None:
    """拒绝所有未声明字段，包括 owner impersonation 参数。"""

    for tool in server._tool_manager.list_tools():
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config = {
            **argument_model.model_config,
            "extra": "forbid",
        }
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)


def _map_error(error: Exception) -> tuple[ErrorCode, str, bool]:
    if isinstance(error, MemoryMcpBoundaryError):
        return error.code, error.public_message, error.retryable
    if isinstance(error, ProfileNotRegisteredError):
        return (
            ErrorCode.PROFILE_NOT_REGISTERED,
            "The requested memory profile_id is unavailable.",
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
