"""已部署 Memory MCP 服务的 PostgreSQL 持久化 adapter。"""

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
