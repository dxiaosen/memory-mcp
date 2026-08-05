"""Core 自包含的纯标准库支撑层。

把日志与异常基类放在 Core 内部，使 domain/application/ports 不必回引根包
``memory_mcp`` 即可获得这些能力，保持分层不变量。根包下同名的
``memory_mcp.logging`` / ``memory_mcp.exceptions`` 仍保留，作为传输与组合根
层的稳定别名，内部委托到这里。
"""

from memory_mcp.core.support.exceptions import (
    ConfigurationError,
    MemoryMcpError,
)
from memory_mcp.core.support.logging import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_FILE,
    DEFAULT_LOG_MAX_BYTES,
    LoggingConfiguration,
    configure_logging,
    configure_logging_from_settings,
    content_logging_enabled,
    log_content_event,
    log_event,
    stable_reference,
)

__all__ = [
    "DEFAULT_LOG_BACKUP_COUNT",
    "DEFAULT_LOG_FILE",
    "DEFAULT_LOG_MAX_BYTES",
    "ConfigurationError",
    "LoggingConfiguration",
    "MemoryMcpError",
    "configure_logging",
    "configure_logging_from_settings",
    "content_logging_enabled",
    "log_content_event",
    "log_event",
    "stable_reference",
]
