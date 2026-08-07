"""Memory MCP 边界暴露的稳定、无正文错误。"""

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    """所有 Memory MCP 工具共用的公开业务错误码。"""

    UNAUTHENTICATED = "unauthenticated"
    PERMISSION_DENIED = "permission_denied"
    PROFILE_NOT_REGISTERED = "profile_not_registered"
    INVALID_EVENT = "invalid_event"
    UNSUPPORTED_CONTRACT_VERSION = "unsupported_contract_version"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    MEMORY_UNAVAILABLE = "memory_unavailable"
    INVALID_RELATION = "invalid_relation"
    RELATION_UNAVAILABLE = "relation_unavailable"
    REVIEW_UNAVAILABLE = "review_unavailable"
    CAPTURE_NOT_CONFIGURED = "capture_not_configured"
    SUBJECT_SCOPE_CONFLICT = "subject_scope_conflict"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


@dataclass(frozen=True, slots=True)
class MemoryMcpBoundaryError(Exception):
    """可安全返回给客户端的预期边界错误。

    ``public_message`` 不含敏感上下文，可直接用于 MCP 错误响应；
    ``code`` 是跨工具稳定的枚举，供客户端程序化判断。
    """

    code: ErrorCode
    public_message: str
    retryable: bool = False


class UnauthenticatedError(MemoryMcpBoundaryError):
    """缺少有效访问 Token 或 Token 无法识别。"""

    def __init__(self) -> None:
        super().__init__(
            ErrorCode.UNAUTHENTICATED,
            "A valid Memory MCP access token is required.",
        )


class PermissionDeniedError(MemoryMcpBoundaryError):
    """已认证主体缺少执行该操作所需的 scope。"""

    def __init__(self, required_scope: str) -> None:
        super().__init__(
            ErrorCode.PERMISSION_DENIED,
            f"The authenticated client lacks required scope: {required_scope}.",
        )


class UnsupportedContractVersionError(MemoryMcpBoundaryError):
    """客户端提交的 completed-turn 契约版本不被支持。"""

    def __init__(self, version: str) -> None:
        super().__init__(
            ErrorCode.UNSUPPORTED_CONTRACT_VERSION,
            f"Completed-turn contract version is not supported: {version}.",
        )
