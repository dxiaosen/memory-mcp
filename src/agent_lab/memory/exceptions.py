"""通用记忆核心可预期的异常。"""

from agent_lab.exceptions import AgentLabError


class MemoryCoreError(AgentLabError):
    """记忆核心操作无法完成。"""


class InvalidScenarioPolicyError(MemoryCoreError):
    """场景策略缺少必需信息或包含非法配置。"""


class ScenarioAlreadyRegisteredError(MemoryCoreError):
    """同一个场景标识被重复注册。"""


class ScenarioNotRegisteredError(MemoryCoreError):
    """请求使用了尚未注册的场景。"""


class InvalidMemoryTypeError(MemoryCoreError):
    """记忆类型不属于当前场景。"""


class InvalidScenarioProgressError(MemoryCoreError):
    """业务进展值不属于当前场景。"""


class MemoryNotFoundError(MemoryCoreError):
    """当前用户范围内不存在指定记忆。"""
