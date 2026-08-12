"""主动记忆使用的最小类型化 MCP HTTP Client。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal, Protocol, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field

from memory_mcp_agent.logging import log_event
from memory_mcp_agent.settings import MemoryHookSettings

_LOGGER = logging.getLogger(__name__)

_MCP_PROTOCOL_VERSION = "2025-11-25"
_MCP_SESSION_HEADER = "mcp-session-id"
_MCP_PROTOCOL_HEADER = "mcp-protocol-version"
try:
    _CLIENT_VERSION = version("memory-mcp-agent")
except PackageNotFoundError:
    # 未安装场景下用占位版本号，避免 initialize 握手缺少 clientInfo.version。
    _CLIENT_VERSION = "0+unknown"


class _Receipt(BaseModel):
    """MCP Tool 调用回执的公共字段，用于校验服务端响应结构。"""

    model_config = ConfigDict(extra="ignore")

    ok: bool
    request_id: str


class RecalledItem(BaseModel):
    """召回得到的一条记忆快照，内容只读。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    memory_id: str
    revision_id: str
    # 命中该条记忆写入时的属主；团队记忆返回真实值，个人记忆为 None。
    owner_id: str | None = None
    content: str


class RecallResponse(_Receipt):
    """recall_memory 的结构化响应，包含命中的记忆条目和渲染后的上下文。"""

    ok: Literal[True] = True
    items: tuple[RecalledItem, ...]
    rendered_context: str
    estimated_tokens: int
    token_budget: int
    truncated: bool


class CaptureSummary(BaseModel):
    """capture_completed_turn 回执的统计摘要（入队返回 pending 时全为 0）。"""

    model_config = ConfigDict(extra="ignore")

    auto_saved_count: int = 0
    pending_count: int = 0
    discarded_count: int = 0
    blocked_count: int = 0


class CaptureResponse(_Receipt):
    """capture_completed_turn 的结构化响应，描述本轮捕获的入队/终态。"""

    model_config = ConfigDict(extra="ignore")

    ok: Literal[True] = True
    capture_id: str
    status: Literal["pending", "completed", "failed", "reprocess_required"]
    replayed: bool
    summary: CaptureSummary = Field(default_factory=CaptureSummary)
    failure_code: str | None = None


class MemoryHookClientError(RuntimeError):
    """客户端对外暴露的稳定错误，只含稳定的错误码，不泄漏敏感信息。"""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class MemoryHookClient(Protocol):
    """主动记忆客户端协议：暴露 recall 与 capture 两个 Tool 的调用契约。

    capture 经 Stop hook 强制触发，调 ``capture_completed_turn`` 入队（服务端
    队列异步抽取）。简化契约：只传对话内容，服务端派生 event_id/幂等字段。
    """

    async def recall_memory(
        self,
        *,
        profile_id: str | None = None,
        query: str,
        subject: str | None,
        task_intent: str | None,
        max_items: int,
        token_budget: int,
    ) -> RecallResponse: ...

    async def capture_completed_turn(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        user_input: str,
        final_output: str,
        profile_id: str | None = None,
        subject_hint: str | None = None,
    ) -> CaptureResponse: ...


class MemoryMcpClient:
    """复用认证 HTTP 连接池，只实现主动记忆所需的两个 MCP Tool。"""

    def __init__(self, settings: MemoryHookSettings) -> None:
        self._settings = settings
        self._http_client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._ensure_http_client()
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """关闭当前客户端持有的可复用连接池。"""

        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def recall_memory(
        self,
        *,
        profile_id: str | None = None,
        query: str,
        subject: str | None,
        task_intent: str | None,
        max_items: int,
        token_budget: int,
    ) -> RecallResponse:
        arguments: dict[str, Any] = {
            "query": query,
            "subject": subject,
            "task_intent": task_intent,
            "max_items": max_items,
            "token_budget": token_budget,
        }
        if profile_id is not None:
            arguments["profile_id"] = profile_id
        payload = await self._call_tool(
            "recall_memory",
            arguments,
            timeout=self._settings.recall_timeout_seconds,
        )
        try:
            return RecallResponse.model_validate(payload)
        except ValueError as exc:
            raise MemoryHookClientError("invalid_recall_response") from exc

    async def capture_completed_turn(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        user_input: str,
        final_output: str,
        profile_id: str | None = None,
        subject_hint: str | None = None,
    ) -> CaptureResponse:
        """入队 capture：服务端派生 event_id/observed_at/contract_version/messages。"""

        arguments: dict[str, Any] = {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "user_input": user_input,
            "final_output": final_output,
        }
        if profile_id is not None:
            arguments["profile_id"] = profile_id
        if subject_hint is not None:
            arguments["subject_hint"] = subject_hint
        payload = await self._call_tool(
            "capture_completed_turn",
            arguments,
            timeout=self._settings.capture_timeout_seconds,
        )
        try:
            return CaptureResponse.model_validate(payload)
        except ValueError as exc:
            raise MemoryHookClientError("invalid_capture_response") from exc

    async def _call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._session() as session:
                result = await session.call_tool(name, arguments, timeout=timeout)
        except MemoryHookClientError:
            raise
        except httpx.HTTPStatusError as exc:
            # HTTP 状态码映射为稳定错误码前，先记全：状态码、异常类型、消息。
            log_event(
                _LOGGER,
                logging.WARNING,
                "mcp_client.http_status_error",
                tool=name,
                status_code=exc.response.status_code,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise _http_status_error(exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            # 连接级错误（DNS/超时/TLS/重置）——具体子类决定根因，必须记。
            log_event(
                _LOGGER,
                logging.WARNING,
                "mcp_client.http_error",
                tool=name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise MemoryHookClientError(
                "memory_mcp_unavailable",
                retryable=True,
            ) from exc
        except Exception as exc:
            # 非预期异常（如服务端响应畸形导致 pydantic 校验失败）：兜底记全。
            log_event(
                _LOGGER,
                logging.ERROR,
                "mcp_client.unexpected_error",
                tool=name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise MemoryHookClientError(
                "memory_mcp_client_error",
            ) from exc

        payload = _structured_payload(result.get("structuredContent"))
        if result.get("isError") is True or payload.get("ok") is False:
            error_code = str(payload.get("error_code", "memory_mcp_tool_error"))
            log_event(
                _LOGGER,
                logging.WARNING,
                "mcp_client.tool_error",
                tool=name,
                error_code=error_code,
                retryable=bool(payload.get("retryable", False)),
            )
            raise MemoryHookClientError(
                error_code,
                retryable=bool(payload.get("retryable", False)),
            )
        return payload

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[_JsonMcpSession]:
        async with _JsonMcpSession(
            self._ensure_http_client(),
            str(self._settings.mcp_url),
        ) as session:
            yield session

    def _ensure_http_client(self) -> httpx.AsyncClient:
        client = self._http_client
        if client is None:
            client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self._settings.token_value()}",
                },
                # 客户端级超时仅覆盖 initialize 等无 per-request 超时的请求；
                # tools/call 显式传 recall/capture 超时，覆盖此默认值。
                timeout=self._settings.recall_timeout_seconds,
            )
            self._http_client = client
        return client


class _JsonMcpSession:
    """面向 Memory MCP JSON response 模式的最小 Streamable HTTP 会话。"""

    def __init__(self, client: httpx.AsyncClient, url: str) -> None:
        self._client = client
        self._url = url
        self._next_id = 1
        self._session_id: str | None = None
        self._protocol_version: str | None = None

    async def __aenter__(self) -> Self:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "memory-mcp-agent",
                    "version": _CLIENT_VERSION,
                },
            },
            initialization=True,
        )
        protocol_version = result.get("protocolVersion")
        if not isinstance(protocol_version, str) or not protocol_version:
            log_event(
                _LOGGER,
                logging.WARNING,
                "mcp_client.invalid_initialize_response",
                protocol_version=protocol_version,
            )
            raise MemoryHookClientError("invalid_mcp_initialize_response")
        self._protocol_version = protocol_version
        await self._notify("notifications/initialized")
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback
        if self._session_id is None:
            return
        try:
            await self._client.delete(
                self._url,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            # session teardown 失败非致命，但必须留痕，否则连接问题无从诊断。
            log_event(
                _LOGGER,
                logging.DEBUG,
                "mcp_client.session_close_failed",
                session_id=self._session_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "tools/call",
            {
                "name": name,
                "arguments": dict(arguments),
            },
            timeout=timeout,
        )

    async def _request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        initialization: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        response = await self._client.post(
            self._url,
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        if initialization:
            session_id = response.headers.get(_MCP_SESSION_HEADER)
            if session_id:
                self._session_id = session_id
        return _json_rpc_result(response, request_id)

    async def _notify(self, method: str) -> None:
        response = await self._client.post(
            self._url,
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "method": method,
            },
        )
        response.raise_for_status()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._session_id is not None:
            headers[_MCP_SESSION_HEADER] = self._session_id
        if self._protocol_version is not None:
            headers[_MCP_PROTOCOL_HEADER] = self._protocol_version
        return headers


def _json_rpc_result(
    response: httpx.Response,
    request_id: int,
) -> dict[str, Any]:
    """解析 JSON-RPC 响应并校验 id 匹配，只接受非 error 结果。"""

    content_type = response.headers.get("content-type", "").casefold()
    if not content_type.startswith("application/json"):
        log_event(
            _LOGGER,
            logging.WARNING,
            "mcp_client.unsupported_response_type",
            status_code=response.status_code,
            content_type=content_type,
        )
        raise MemoryHookClientError("unsupported_mcp_response_type")
    try:
        payload = response.json()
    except ValueError as exc:
        log_event(
            _LOGGER,
            logging.WARNING,
            "mcp_client.invalid_response_json",
            status_code=response.status_code,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise MemoryHookClientError("invalid_mcp_response") from exc
    if not isinstance(payload, dict):
        log_event(
            _LOGGER,
            logging.WARNING,
            "mcp_client.invalid_response_shape",
            status_code=response.status_code,
            payload_type=type(payload).__name__,
        )
        raise MemoryHookClientError("invalid_mcp_response")
    if payload.get("jsonrpc") != "2.0" or payload.get("id") != request_id:
        log_event(
            _LOGGER,
            logging.WARNING,
            "mcp_client.response_id_mismatch",
            status_code=response.status_code,
            expected_id=request_id,
            actual_id=payload.get("id"),
        )
        raise MemoryHookClientError("invalid_mcp_response")
    if "error" in payload:
        error_obj = payload.get("error")
        log_event(
            _LOGGER,
            logging.WARNING,
            "mcp_client.protocol_error",
            status_code=response.status_code,
            error_obj=error_obj,
        )
        raise MemoryHookClientError("memory_mcp_protocol_error")
    result = payload.get("result")
    if not isinstance(result, dict):
        log_event(
            _LOGGER,
            logging.WARNING,
            "mcp_client.invalid_result_shape",
            status_code=response.status_code,
            result_type=type(result).__name__,
        )
        raise MemoryHookClientError("invalid_mcp_response")
    return {str(key): value for key, value in result.items()}


def _structured_payload(value: object) -> dict[str, Any]:
    """从 MCP Tool 返回的 structuredContent 中提取有效 payload。"""

    if not isinstance(value, dict):
        raise MemoryHookClientError("missing_structured_response")
    nested = value.get("result")
    payload = nested if isinstance(nested, dict) else value
    return {str(key): item for key, item in payload.items()}


def _http_status_error(status_code: int) -> MemoryHookClientError:
    """把 HTTP 状态码映射为稳定的客户端错误码，区分鉴权/可重试/永久拒绝。"""
    if status_code in {401, 403}:
        return MemoryHookClientError("memory_mcp_auth_rejected")
    if status_code in {408, 425, 429} or status_code >= 500:
        return MemoryHookClientError(
            "memory_mcp_unavailable",
            retryable=True,
        )
    return MemoryHookClientError("memory_mcp_request_rejected")
