"""Memory MCP 组合根与 Streamable HTTP 入口。"""

import asyncio
import logging
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from psycopg import Error as PostgreSQLError
from psycopg_pool import PoolTimeout
from starlette.requests import Request
from starlette.responses import JSONResponse

from memory_mcp.auth import StaticTokenVerifier
from memory_mcp.core import (
    CandidateExtractor,
    MaintenanceResult,
    MemoryProfile,
    MemoryService,
    RelationExtractor,
)
from memory_mcp.core.adapters.postgresql import (
    PostgreSQLMemoryRepository,
    create_pool,
)
from memory_mcp.core.adapters.postgresql.schema import (
    apply_migrations as apply_postgresql_migrations,
)
from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.composition import create_memory_service
from memory_mcp.extraction.factory import create_configured_extractors
from memory_mcp.extraction.settings import ExtractionSettings
from memory_mcp.logging import configure_logging_from_settings, log_event
from memory_mcp.profiles import built_in_profiles
from memory_mcp.settings import ConfiguredPrincipal, MemoryServerSettings
from memory_mcp.tools import MemoryMcpTools

_LOGGER = logging.getLogger(__name__)
# 连续 has_more 续批的软上限与触发后的退避秒数；见 _run_maintenance_loop。
_MAINTENANCE_HAS_MORE_SOFT_LIMIT = 8
_MAINTENANCE_HAS_MORE_BACKOFF_SECONDS = 1


class _RunnableServer(Protocol):
    def run(self, *, transport: str) -> None: ...


class MaintenanceHealth:
    """维护循环的进程内、无正文健康快照。"""

    def __init__(
        self,
        *,
        enabled: bool,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._state: Literal["disabled", "starting", "ok", "degraded"] = (
            "starting" if enabled else "disabled"
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._consecutive_failures = 0
        self._last_success_at: datetime | None = None
        self._last_failure_at: datetime | None = None
        self._last_error_type: str | None = None

    def observe_success(self, result: MaintenanceResult) -> None:
        """记录一次成功并从 degraded 恢复。"""

        self._state = "ok"
        self._consecutive_failures = 0
        self._last_success_at = result.effective_at
        self._last_error_type = None

    def observe_failure(self, error: Exception) -> None:
        """只记录异常类型与计数，不保留错误消息或业务正文。"""

        self._state = "degraded"
        self._consecutive_failures += 1
        self._last_failure_at = self._clock()
        self._last_error_type = type(error).__name__

    def snapshot(self) -> dict[str, object]:
        """返回可直接序列化的稳定健康契约。"""

        return {
            "state": self._state,
            "consecutive_failures": self._consecutive_failures,
            "last_success_at": _isoformat_or_none(self._last_success_at),
            "last_failure_at": _isoformat_or_none(self._last_failure_at),
            "last_error_type": self._last_error_type,
        }


class MemoryMcpServer(FastMCP[Any]):
    """让进程级存储跟随 ASGI lifespan 的 FastMCP 服务。"""

    def __init__(
        self,
        *args: Any,
        close_storage: Callable[[], None] | None = None,
        run_maintenance: Callable[[], MaintenanceResult] | None = None,
        maintenance_interval_seconds: int = 0,
        **kwargs: Any,
    ) -> None:
        self._close_storage = close_storage
        self._run_maintenance = run_maintenance
        self._maintenance_interval_seconds = maintenance_interval_seconds
        self._maintenance_health = MaintenanceHealth(
            enabled=run_maintenance is not None and maintenance_interval_seconds > 0
        )
        self._streamable_app = None
        super().__init__(*args, **kwargs)

    @property
    def maintenance_health(self) -> MaintenanceHealth:
        """返回当前进程维护健康状态。"""

        return self._maintenance_health

    def streamable_http_app(self):
        """构建并缓存 Streamable HTTP ASGI 应用，注入存储与维护生命周期。"""

        if self._streamable_app is not None:
            return self._streamable_app
        app = super().streamable_http_app()
        # 需要管理存储生命周期或维护循环时，包一层 lifespan：在 ASGI 启动
        # 时启动后台维护任务，关闭时先停止维护再释放存储。
        if self._close_storage is not None or (
            self._run_maintenance is not None and self._maintenance_interval_seconds > 0
        ):
            session_manager_lifespan = app.router.lifespan_context

            @asynccontextmanager
            async def lifespan(starlette_app):
                async with session_manager_lifespan(starlette_app) as state:
                    stop_maintenance = asyncio.Event()
                    maintenance_task = (
                        asyncio.create_task(
                            _run_maintenance_loop(
                                self._run_maintenance,
                                interval_seconds=self._maintenance_interval_seconds,
                                stop_event=stop_maintenance,
                                health=self._maintenance_health,
                            )
                        )
                        if self._run_maintenance is not None
                        and self._maintenance_interval_seconds > 0
                        else None
                    )
                    try:
                        yield state
                    finally:
                        stop_maintenance.set()
                        if maintenance_task is not None:
                            await maintenance_task
                        if self._close_storage is not None:
                            await asyncio.to_thread(self._close_storage)

            app.router.lifespan_context = lifespan
        self._streamable_app = app
        return app


def create_memory_mcp_server(
    settings: MemoryServerSettings,
    *,
    memory_service: MemoryService | None = None,
    candidate_extractor: CandidateExtractor | None = None,
    relation_extractor: RelationExtractor | None = None,
    extraction_settings: ExtractionSettings | None = None,
    profiles: Iterable[MemoryProfile] | None = None,
) -> MemoryMcpServer:
    """组装 Memory MCP 服务并注册认证、健康检查与维护循环。

    未注入 ``memory_service`` 时按 settings 自动构建 PostgreSQL 仓储、
    敏感内容守卫与内置 Profile；注入时复用调用方提供的实现，且存储
    健康检查交由调用方负责。
    """

    principals = settings.require_configured_principals()
    close_storage: Callable[[], None] | None = None
    if memory_service is None:
        configured_extractor = candidate_extractor
        configured_relation_extractor = relation_extractor
        if configured_extractor is None:
            configured_extractors = create_configured_extractors(
                extraction_settings or ExtractionSettings()
            )
            configured_extractor = configured_extractors.candidate
            if configured_relation_extractor is None:
                configured_relation_extractor = configured_extractors.relation
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

        configured_profiles = tuple(profiles or built_in_profiles())
        _validate_default_profiles(principals.values(), configured_profiles)
        sensitive_guard = RegexSensitiveContentGuard.from_config(
            settings.configured_sensitive_rules()
        )
        memory_service = create_memory_service(
            repository,
            configured_profiles,
            candidate_extractor=configured_extractor,
            relation_extractor=configured_relation_extractor,
            sensitive_guard=sensitive_guard,
            recall_candidate_limit=settings.recall_candidate_limit,
        )
    else:
        # 外部注入 memory_service 时，存储健康检查由调用方负责，此处禁用
        # /health 对存储的探测，避免越权访问。
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
        run_maintenance=memory_service.run_maintenance,
        maintenance_interval_seconds=settings.maintenance_interval_seconds,
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
                {
                    "status": "unhealthy",
                    "maintenance": server.maintenance_health.snapshot(),
                },
                status_code=503,
            )
        return JSONResponse(
            {
                "status": "ok",
                "service": "memory-mcp",
                "transport": "streamable-http",
                "mcp_path": settings.mcp_path,
                "storage": "postgresql",
                "maintenance": server.maintenance_health.snapshot(),
            }
        )

    return server


async def _run_maintenance_loop(
    operation: Callable[[], MaintenanceResult],
    *,
    interval_seconds: int,
    stop_event: asyncio.Event,
    health: MaintenanceHealth | None = None,
) -> None:
    """周期性运行记忆维护，异常不影响 MCP 服务。

    每轮成功且仍有积压（``has_more``）时立即进入下一批；连续续批超过
    ``_MAINTENANCE_HAS_MORE_SOFT_LIMIT`` 后插入
    ``_MAINTENANCE_HAS_MORE_BACKOFF_SECONDS`` 短延迟，避免在积压持续不
    推进的场景下形成紧密循环而长时间占用数据库连接。异常只记录和降级
    健康状态，不向外传播。
    """

    consecutive_has_more = 0
    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(operation)
            if health is not None:
                health.observe_success(result)
            if result.has_more:
                consecutive_has_more += 1
                delay = (
                    0
                    if consecutive_has_more <= _MAINTENANCE_HAS_MORE_SOFT_LIMIT
                    else _MAINTENANCE_HAS_MORE_BACKOFF_SECONDS
                )
            else:
                consecutive_has_more = 0
                delay = interval_seconds
        except Exception as exc:
            if health is not None:
                health.observe_failure(exc)
            consecutive_has_more = 0
            log_event(
                _LOGGER,
                logging.ERROR,
                "memory.maintenance.failed",
                error_type=type(exc).__name__,
            )
            delay = interval_seconds
        if stop_event.is_set():
            break
        if delay == 0:
            await asyncio.sleep(0)
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except TimeoutError:
            pass


def _isoformat_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _validate_default_profiles(
    principals: Iterable[ConfiguredPrincipal],
    profiles: Iterable[MemoryProfile],
) -> None:
    """在服务启动前拒绝认证主体引用不存在的默认 Profile。"""

    configured_profile_ids = {profile.profile_id for profile in profiles}
    unavailable_defaults = sorted(
        {
            principal.default_profile_id
            for principal in principals
            if principal.default_profile_id not in configured_profile_ids
        }
    )
    if unavailable_defaults:
        raise ValueError(
            "configured principal default_profile_id is not registered: "
            + ", ".join(unavailable_defaults)
        )


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
