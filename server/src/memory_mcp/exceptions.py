"""项目级可预期异常。"""


class MemoryMcpError(Exception):
    """Memory MCP 可预期异常的基类。"""


class ConfigurationError(MemoryMcpError):
    """配置不完整或不受支持时抛出。"""
