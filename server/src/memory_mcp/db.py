"""Memory MCP 服务的 PostgreSQL 维护入口。"""

import argparse

from memory_mcp.core.adapters.postgresql.schema import (
    apply_migrations,
    check_health,
)
from memory_mcp.logging import configure_logging_from_settings
from memory_mcp.settings import MemoryServerSettings


def main() -> None:
    """使用部署层配置执行数据库维护。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "health"))
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help=(
            "开发模式：schema 文件 checksum 变更时 drop 重建。"
            "生产环境不要使用此标志，它会清空所有数据。"
        ),
    )
    args = parser.parse_args()

    settings = MemoryServerSettings.from_environment()
    configure_logging_from_settings(settings)
    database_url = settings.require_postgresql_url()
    if args.command == "health":
        check_health(database_url)
        print("Memory PostgreSQL is healthy")
        return
    applied = apply_migrations(
        database_url,
        rebuild_on_checksum_change=args.rebuild,
    )
    if applied:
        print("Applied PostgreSQL migrations: " + ", ".join(applied))
    else:
        print("PostgreSQL schema is up to date")


if __name__ == "__main__":
    main()
