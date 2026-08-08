"""通用记忆核心可预期的异常。"""

from typing import Any

from memory_mcp.core.support.exceptions import MemoryMcpError


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


class InvalidMemoryRelationError(MemoryCoreError):
    """关系名称或有向端点不符合当前 Profile。"""


class MemoryNotFoundError(MemoryCoreError):
    """当前用户范围内不存在指定记忆。"""


class MemoryRelationNotFoundError(MemoryCoreError):
    """当前用户范围内不存在指定记忆关系。"""


class CaptureNotConfiguredError(MemoryCoreError):
    """应用尚未配置候选抽取器或敏感内容守卫。"""


class InvalidModelOutputError(MemoryCoreError):
    """结构化模型输出不符合候选契约。

    开发阶段需暴露具体失败字段以便排障（recommend.md §0 已放开完整内容日志）。
    ``context`` 携带结构化违规信息（如 ``{"field": "confidence", "value": 1.5}``），
    供 ``memory.capture.invalid_output`` 的 ``error_detail`` 经
    ``_validation_errors`` 提取，避免该字段恒为 null。
    """

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.context: dict[str, Any] | None = context


class IdempotencyConflictError(MemoryCoreError):
    """同一外部事件标识被用于不同的规范化 payload。"""


class ReviewNotFoundError(MemoryCoreError):
    """当前用户范围内不存在可处理的待确认项。"""


class SensitiveContentBlockedError(MemoryCoreError):
    """禁止内容被长期记忆持久化边界拦截。"""


class SubjectScopeConflictError(MemoryCoreError):
    """同一 (owner, profile, subject, memory_type) 已有活动记忆，新建撞唯一索引。

    根因是 ``find_current`` 与写入跨事务的 TOCTOU：并发 confirm/capture 或
    过期未物化的活动记忆使 ``find_current`` 漏判，导致新建分支在唯一索引
    ``memory_items_one_active_scope_idx`` 上撞键。属可预期的边界状态而非
    临时故障——调用方应改 subject 或走 replacement。
    """
