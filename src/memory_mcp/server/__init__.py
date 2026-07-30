"""Public construction API for the Memory MCP server."""

from memory_mcp.server.app import create_app, create_memory_mcp_server
from memory_mcp.server.auth import MemoryScope, RequestPrincipal
from memory_mcp.server.settings import (
    DemoPrincipalSettings,
    MemoryServerSettings,
)

__all__ = [
    "DemoPrincipalSettings",
    "MemoryScope",
    "MemoryServerSettings",
    "RequestPrincipal",
    "create_app",
    "create_memory_mcp_server",
]
