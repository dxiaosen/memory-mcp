"""Agent Hook 的非内容运行日志。"""

from __future__ import annotations

import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from os import PathLike
from pathlib import Path
from typing import Any

_MANAGED_HANDLER_ATTRIBUTE = "_memory_mcp_agent_managed"
# 命中即整体脱敏的字段名。只保留真正的凭证字段；内容字段（prompt/query/
# answer/content 等）不再脱敏——排障优先，需要在失败日志里看到实际输入。
# 命中 _*_api_key / _*_password / _*_secret 后缀的字段同样脱敏。
_SENSITIVE_FIELD_NAMES = {
    "api_key",
    "password",
    "secret",
    "token",
}
_SENSITIVE_FIELD_SUFFIXES = ("_api_key", "_password", "_secret", "_token")


def configure_logging(
    *,
    level: str = "INFO",
    log_file: str | PathLike[str] | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    content: bool = False,
) -> None:
    """幂等配置 stderr 和可选滚动文件。

    content 参数保留向后兼容，但不再有实际作用——内容字段（prompt/query/
    answer/content）已从脱敏集移除，排障时直接记录，不再拒绝内容日志模式。
    """

    del content
    numeric_level = logging.getLevelNamesMapping().get(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"unsupported log level: {level}")
    if max_bytes < 1 or backup_count < 0:
        raise ValueError("invalid log rotation settings")

    root_logger = logging.getLogger()
    for handler in tuple(root_logger.handlers):
        if not getattr(handler, _MANAGED_HANDLER_ATTRIBUTE, False):
            continue
        root_logger.removeHandler(handler)
        handler.close()

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


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    """写入确定性排序并按字段名脱敏的单行事件。

    字段按名称排序保证日志可 diff；命中敏感字段名时整体替换为 [REDACTED]。
    """

    normalized_event = event.strip()
    if not normalized_event:
        raise ValueError("event must not be empty")
    parts = [f"event={_encode(normalized_event)}"]
    for key in sorted(fields):
        value = "[REDACTED]" if _is_sensitive_field(key) else fields[key]
        parts.append(f"{key}={_encode(value)}")
    logger.log(level, " ".join(parts))


def stable_reference(value: str) -> str:
    """返回不暴露原始标识的稳定短引用。"""

    normalized = value.strip()
    if not normalized:
        raise ValueError("identifier must not be empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _is_sensitive_field(field_name: str) -> bool:
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
