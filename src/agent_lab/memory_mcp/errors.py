"""Stable, content-free errors exposed by the Memory MCP boundary."""

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    """Public business error codes shared by all Memory MCP tools."""

    UNAUTHENTICATED = "unauthenticated"
    PERMISSION_DENIED = "permission_denied"
    SCENARIO_NOT_REGISTERED = "scenario_not_registered"
    INVALID_EVENT = "invalid_event"
    UNSUPPORTED_CONTRACT_VERSION = "unsupported_contract_version"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    MEMORY_UNAVAILABLE = "memory_unavailable"
    REVIEW_UNAVAILABLE = "review_unavailable"
    CAPTURE_NOT_CONFIGURED = "capture_not_configured"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


@dataclass(frozen=True, slots=True)
class MemoryMcpBoundaryError(Exception):
    """Expected boundary failure with a safe public representation."""

    code: ErrorCode
    public_message: str
    retryable: bool = False


class UnauthenticatedError(MemoryMcpBoundaryError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.UNAUTHENTICATED,
            "A valid Memory MCP access token is required.",
        )


class PermissionDeniedError(MemoryMcpBoundaryError):
    def __init__(self, required_scope: str) -> None:
        super().__init__(
            ErrorCode.PERMISSION_DENIED,
            f"The authenticated client lacks required scope: {required_scope}.",
        )


class UnsupportedContractVersionError(MemoryMcpBoundaryError):
    def __init__(self, version: str) -> None:
        super().__init__(
            ErrorCode.UNSUPPORTED_CONTRACT_VERSION,
            f"Completed-turn contract version is not supported: {version}.",
        )
