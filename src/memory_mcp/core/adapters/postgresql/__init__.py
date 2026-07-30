"""PostgreSQL persistence adapter for the deployed Memory MCP service."""

from memory_mcp.core.adapters.postgresql.repository import (
    PostgreSQLMemoryRepository,
    PostgreSQLPool,
    create_pool,
)

__all__ = [
    "PostgreSQLMemoryRepository",
    "PostgreSQLPool",
    "create_pool",
]
