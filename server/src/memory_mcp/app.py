"""Memory MCP 组合根与 Streamable HTTP 入口。"""

import asyncio
import logging
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any, Protocol

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from psycopg import Error as PostgreSQLError
from psycopg_pool import PoolTimeout
from starlette.requests import Request
from starlette.responses import JSONResponse

from memory_mcp.auth import StaticTokenVerifier
from memory_mcp.core import CandidateExtractor, MemoryProfile, MemoryService
from memory_mcp.core.adapters.postgresql import (
    PostgreSQLMemoryRepository,
    create_pool,
)
from memory_mcp.core.adapters.postgresql.schema import (
    apply_migrations as apply_postgresql_migrations,
)
from memory_mcp.core.composition import create_memory_service
from memory_mcp.extraction.factory import create_configured_candidate_extractor
from memory_mcp.extraction.settings import ExtractionSettings
from memory_mcp.logging import configure_logging_from_settings, log_event
from memory_mcp.profiles import GeneralWorkProfile
from memory_mcp.settings import MemoryServerSettings
from memory_mcp.tools import MemoryMcpTools

_LOGGER = logging.getLogger(__name__)


class _RunnableServer(Protocol):
    def run(self, *, transport: str) -> None: ...


class MemoryMcpServer(FastMCP[Any]):
    """让进程级存储跟随 ASGI lifespan 的 FastMCP 服务。"""

    def __init__(
        self,
        *args: Any,
        close_storage: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        self._close_storage = close_storage
        self._streamable_app = None
        super().__init__(*args, **kwargs)

    def streamable_http_app(self):
        if self._streamable_app is not None:
            return self._streamable_app
        app = super().streamable_http_app()
        if self._close_storage is not None:
            session_manager_lifespan = app.router.lifespan_context

            @asynccontextmanager
            async def lifespan(starlette_app):
                async with session_manager_lifespan(starlette_app) as state:
                    try:
                        yield state
                    finally:
                        await asyncio.to_thread(self._close_storage)

            app.router.lifespan_context = lifespan
        self._streamable_app = app
        return app


def create_memory_mcp_server(
    settings: MemoryServerSettings,
    *,
    memory_service: MemoryService | None = None,
    candidate_extractor: CandidateExtractor | None = None,
    extraction_settings: ExtractionSettings | None = None,
    profiles: Iterable[MemoryProfile] | None = None,
) -> MemoryMcpServer:
    """创建具备完整认证边界的 Memory MCP 服务。"""

    principals = settings.require_configured_principals()
    close_storage: Callable[[], None] | None = None
    if memory_service is None:
        configured_extractor = candidate_extractor
        if configured_extractor is None:
            configured_extractor = create_configured_candidate_extractor(
                extraction_settings or ExtractionSettings()
            )
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

        configured_profiles = tuple(profiles or (GeneralWorkProfile(),))
        memory_service = create_memory_service(
            repository,
            configured_profiles,
            candidate_extractor=configured_extractor,
        )
    else:

        def health_check() -> None:
            return None

    server = MemoryMcpServer(
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
        close_storage=close_storage,
        token_verifier=StaticTokenVerifier(principals),
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
                "storage": "postgresql",
            }
        )

    return server


def create_app(
    settings: MemoryServerSettings | None = None,
    extraction_settings: ExtractionSettings | None = None,
):
    """构建测试或外部 ASGI Runner 使用的应用。"""

    resolved = settings or MemoryServerSettings.from_environment()
    return create_memory_mcp_server(
        resolved,
        extraction_settings=extraction_settings,
    ).streamable_http_app()


def _run_server(server: _RunnableServer) -> None:
    """持续运行到关闭，且不向操作者显示 Ctrl+C traceback。"""

    try:
        server.run(transport="streamable-http")
    except KeyboardInterrupt:
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.mcp.server.stopped",
            reason="keyboard_interrupt",
        )


def main() -> None:
    """通过 Streamable HTTP 运行远程服务。"""

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
        storage="postgresql",
    )
    _run_server(server)


if __name__ == "__main__":
    main()
