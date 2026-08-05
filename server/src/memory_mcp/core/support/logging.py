"""集中管理运行日志和显式启用的应用内容日志。

本模块是 Core 自包含的纯标准库日志实现，使 domain/application/ports 不必
回引根包即可获得日志能力。根包 ``memory_mcp.logging`` 作为传输与组合根层
的稳定别名委托到这里。
"""

import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from os import PathLike
from pathlib import Path
from typing import Any, Protocol

DEFAULT_LOG_FILE = Path(".memory-mcp/logs/memory-mcp.log")
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
_MANAGED_HANDLER_ATTRIBUTE = "_memory_mcp_managed"
_CONTENT_LOGGER = logging.getLogger("memory_mcp.content")
_content_logging_enabled = False
_SENSITIVE_FIELD_NAMES = {
    "answer",
    "api_key",
    "content",
    "password",
    "prompt",
    "query",
    "secret",
    "source_expression",
}
_SENSITIVE_FIELD_SUFFIXES = ("_api_key", "_password", "_secret")


class LoggingConfiguration(Protocol):
    """日志配置对象所需的最小结构，避免依赖具体 Settings 类型。"""

    @property
    def log_level(self) -> str: ...

    @property
    def log_file(self) -> str | PathLike[str] | None: ...

    @property
    def log_max_bytes(self) -> int: ...

    @property
    def log_backup_count(self) -> int: ...

    @property
    def log_content(self) -> bool: ...


def configure_logging_from_settings(settings: LoggingConfiguration) -> None:
    """使用任意符合最小结构的配置对象初始化日志。"""

    configure_logging(
        level=settings.log_level,
        log_file=settings.log_file,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        content=settings.log_content,
    )


def configure_logging(
    *,
    level: str = "INFO",
    log_file: str | PathLike[str] | None = DEFAULT_LOG_FILE,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
    content: bool = False,
) -> None:
    """幂等配置控制台日志和可选的滚动文件日志。"""

    global _content_logging_enabled

    normalized_level = level.upper()
    numeric_level = logging.getLevelNamesMapping().get(normalized_level)
    if not isinstance(numeric_level, int):
        raise ValueError(f"unsupported log level: {level}")
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if backup_count < 0:
        raise ValueError("backup_count must not be negative")
    _content_logging_enabled = content

    root_logger = logging.getLogger()
    _remove_managed_handlers(root_logger)
    root_logger.setLevel(numeric_level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    setattr(console_handler, _MANAGED_HANDLER_ATTRIBUTE, True)
    root_logger.addHandler(console_handler)

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        setattr(file_handler, _MANAGED_HANDLER_ATTRIBUTE, True)
        root_logger.addHandler(file_handler)

    log_event(
        logging.getLogger(__name__),
        logging.DEBUG,
        "logging.configured",
        configured_level=normalized_level,
        file_enabled=log_file is not None,
        max_bytes=max_bytes,
        backup_count=backup_count,
        content_logging=content,
    )
    if content:
        log_event(
            logging.getLogger(__name__),
            logging.WARNING,
            "logging.content.enabled",
            warning=(
                "application content logging is enabled; disable it after "
                "manual verification"
            ),
        )


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    """写入经过确定性排序和脱敏的单行键值事件。"""

    normalized_event = event.strip()
    if not normalized_event:
        raise ValueError("event must not be empty")

    parts = [f"event={_encode(normalized_event)}"]
    for key in sorted(fields):
        value = fields[key]
        if _is_sensitive_field(key):
            value = "[REDACTED]"
        parts.append(f"{key}={_encode(value)}")
    logger.log(level, " ".join(parts))


def log_content_event(event: str, **fields: Any) -> None:
    """仅在显式启用内容日志时写入应用字段。"""

    if not _content_logging_enabled:
        return
    normalized_event = event.strip()
    if not normalized_event:
        raise ValueError("event must not be empty")
    parts = [f"event={_encode(normalized_event)}"]
    parts.extend(f"{key}={_encode(fields[key])}" for key in sorted(fields))
    _CONTENT_LOGGER.info(" ".join(parts))


def content_logging_enabled() -> bool:
    """为诊断和专项测试返回进程级内容日志状态。"""

    return _content_logging_enabled


def stable_reference(value: str) -> str:
    """返回稳定的 12 位短引用，用于日志中关联身份而不泄露原始值。

    使用 SHA-256 的前 12 个十六进制字符，同一输入恒定不变；截断后
    不具备抗碰撞强度，仅用于审计关联而非安全索引。
    """

    normalized = value.strip()
    if not normalized:
        raise ValueError("identifier must not be empty")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:12]


def _remove_managed_handlers(logger: logging.Logger) -> None:
    for handler in tuple(logger.handlers):
        if not getattr(handler, _MANAGED_HANDLER_ATTRIBUTE, False):
            continue
        logger.removeHandler(handler)
        handler.close()


def _is_sensitive_field(field_name: str) -> bool:
    """按名称和后缀判断字段是否敏感，敏感字段在日志中脱敏为 ``[REDACTED]``。"""

    normalized = field_name.casefold()
    return normalized in _SENSITIVE_FIELD_NAMES or normalized.endswith(
        _SENSITIVE_FIELD_SUFFIXES
    )


def _encode(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
