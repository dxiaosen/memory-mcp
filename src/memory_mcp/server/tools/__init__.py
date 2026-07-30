"""按能力分组的 Memory MCP 工具注册入口。"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from memory_mcp.server.tools.capture import CaptureTools
from memory_mcp.server.tools.memory import MemoryTools
from memory_mcp.server.tools.recall import RecallTools
from memory_mcp.server.tools.review import ReviewTools
from memory_mcp.server.tools.shared import (
    ToolSupport,
    enforce_strict_tool_arguments,
)


class MemoryMcpTools(
    CaptureTools,
    MemoryTools,
    RecallTools,
    ReviewTools,
    ToolSupport,
):
    """Owner-safe MCP facade over one MemoryService instance."""

    def register(self, server: FastMCP[Any]) -> None:
        self._register_capture(server)
        self._register_memory(server)
        self._register_recall(server)
        self._register_review(server)
        enforce_strict_tool_arguments(server)


__all__ = ["MemoryMcpTools"]
