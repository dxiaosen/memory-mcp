"""根包异常别名，委托到 ``memory_mcp.core.support.exceptions``。

实现保留在 Core 内部，使 Core 的异常基类不必回引根包；根包仅作为传输与
组合根层的稳定导入路径。
"""

from memory_mcp.core.support.exceptions import (
    ConfigurationError,
    MemoryMcpError,
)

__all__ = [
    "ConfigurationError",
    "MemoryMcpError",
]
