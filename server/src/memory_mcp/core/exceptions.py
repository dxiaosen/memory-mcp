"""通用记忆核心可预期的异常。"""

from memory_mcp.exceptions import MemoryMcpError


class MemoryCoreError(MemoryMcpError):
    """记忆核心操作无法完成。"""


class InvalidMemoryProfileError(MemoryCoreError):
    """记忆配置缺少必需信息或包含非法内容。"""


class ProfileAlreadyRegisteredError(MemoryCoreError):
    """同一个记忆配置标识被重复注册。"""


class ProfileNotRegisteredError(MemoryCoreError):
    """请求使用了尚未注册的记忆配置。"""


class InvalidMemoryTypeError(MemoryCoreError):
    """记忆类型不属于当前记忆配置。"""


class InvalidProfileProgressError(MemoryCoreError):
    """业务进展值不属于当前记忆配置。"""


class MemoryNotFoundError(MemoryCoreError):
    """当前用户范围内不存在指定记忆。"""


class CaptureNotConfiguredError(MemoryCoreError):
    """应用尚未配置候选抽取器或敏感内容守卫。"""


class InvalidModelOutputError(MemoryCoreError):
    """结构化模型输出不符合候选契约。"""


class IdempotencyConflictError(MemoryCoreError):
    """同一外部事件标识被用于不同的规范化 payload。"""


class ReviewNotFoundError(MemoryCoreError):
    """当前用户范围内不存在可处理的待确认项。"""


class SensitiveContentBlockedError(MemoryCoreError):
    """禁止内容被长期记忆持久化边界拦截。"""
