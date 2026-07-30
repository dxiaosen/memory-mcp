"""Memory MCP composition root and Streamable HTTP entry point."""

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from psycopg import Error as PostgreSQLError
from psycopg_pool import PoolTimeout
from starlette.requests import Request
from starlette.responses import JSONResponse

from memory_mcp.core import CandidateExtractor, MemoryService, ScenarioPolicy
from memory_mcp.core.adapters.postgresql import (
    PostgreSQLMemoryRepository,
    create_pool,
)
from memory_mcp.core.adapters.postgresql.runtime import (
    apply_migrations as apply_postgresql_migrations,
)
from memory_mcp.core.adapters.sqlite import (
    SQLiteMemoryRepository,
    connection_factory,
)
from memory_mcp.core.adapters.sqlite.runtime import (
    apply_migrations,
    check_health,
)
from memory_mcp.core.composition import create_memory_service
from memory_mcp.logging import configure_logging_from_settings, log_event
from memory_mcp.server.auth import DemoTokenVerifier
from memory_mcp.server.policy import ConfiguredScenarioPolicy
from memory_mcp.server.settings import MemoryServerSettings
from memory_mcp.server.tools import MemoryMcpTools

_LOGGER = logging.getLogger(__name__)


def create_memory_mcp_server(
    settings: MemoryServerSettings,
    *,
    memory_service: MemoryService | None = None,
    candidate_extractor: CandidateExtractor | None = None,
    policies: Iterable[ScenarioPolicy] | None = None,
) -> FastMCP[Any]:
    """Create a fully authenticated Memory MCP server."""

    principals = settings.require_demo_principals()
    close_storage: Callable[[], None] | None = None
    if memory_service is None:
        if settings.storage_backend == "postgresql":
            database_url = settings.require_postgresql_url()
            if settings.database_migrate_on_startup:
                apply_postgresql_migrations(database_url)
            repository = PostgreSQLMemoryRepository(
                create_pool(
                    database_url,
                    min_size=settings.database_pool_min_size,
                    max_size=settings.database_pool_max_size,
                    timeout=settings.database_connect_timeout_seconds,
                )
            )
            health_check = repository.check_health
            close_storage = repository.close
        else:
            apply_migrations(settings.database_path)
            repository = SQLiteMemoryRepository(
                connection_factory(settings.database_path)
            )

            def health_check() -> None:
                check_health(settings.database_path)

        configured_policies = tuple(
            policies or (ConfiguredScenarioPolicy.from_settings(settings),)
        )
        memory_service = create_memory_service(
            repository,
            configured_policies,
            candidate_extractor=candidate_extractor,
        )
    else:

        def health_check() -> None:
            return None

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            if close_storage is not None:
                await asyncio.to_thread(close_storage)

    server: FastMCP[Any] = FastMCP(
        name="Memory MCP",
        instructions=(
            "Owner-scoped long-term memory service. "
            "Never place owner or bearer-token values in tool arguments."
        ),
        host=settings.host,
        port=settings.port,
        streamable_http_path=settings.mcp_path,
        json_response=True,
        stateless_http=settings.stateless_http,
        lifespan=lifespan,
        token_verifier=DemoTokenVerifier(principals),
        auth=AuthSettings(
            issuer_url=settings.auth_issuer_url,
            resource_server_url=settings.resource_server_url,
            required_scopes=[],
        ),
    )
    MemoryMcpTools(memory_service, settings).register(server)

    @server.custom_route(settings.health_path, methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        try:
            await asyncio.to_thread(health_check)
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            PostgreSQLError,
            PoolTimeout,
        ):
            return JSONResponse(
                {"status": "unhealthy"},
                status_code=503,
            )
        return JSONResponse(
            {
                "status": "ok",
                "service": "memory-mcp",
                "transport": "streamable-http",
                "mcp_path": settings.mcp_path,
                "storage": settings.storage_backend,
            }
        )

    return server


def create_app(settings: MemoryServerSettings | None = None):
    """Build the ASGI app used by tests or an external ASGI runner."""

    resolved = settings or MemoryServerSettings.from_environment()
    return create_memory_mcp_server(resolved).streamable_http_app()


def main() -> None:
    """Run the prototype remote service with Streamable HTTP."""

    settings = MemoryServerSettings.from_environment()
    configure_logging_from_settings(settings)
    server = create_memory_mcp_server(settings)
    log_event(
        _LOGGER,
        logging.INFO,
        "memory.mcp.server.starting",
        host=settings.host,
        mcp_path=settings.mcp_path,
        port=settings.port,
        storage=settings.storage_backend,
    )
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
