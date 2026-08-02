"""主动记忆使用的最小类型化 MCP HTTP Client。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal, Protocol, Self

import httpx
from pydantic import BaseModel, ConfigDict

from memory_mcp_agent.settings import MemoryHookSettings

_MCP_PROTOCOL_VERSION = "2025-11-25"
_MCP_SESSION_HEADER = "mcp-session-id"
_MCP_PROTOCOL_HEADER = "mcp-protocol-version"
try:
    _CLIENT_VERSION = version("memory-mcp-agent")
except PackageNotFoundError:
    _CLIENT_VERSION = "0+unknown"


class _Receipt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    request_id: str


class RecalledItem(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    memory_id: str
    revision_id: str
    content: str


class RecallResponse(_Receipt):
    ok: Literal[True] = True
    items: tuple[RecalledItem, ...]
    rendered_context: str
    estimated_tokens: int
    token_budget: int
    truncated: bool


class CaptureSummary(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    auto_saved_count: int = 0
    pending_count: int = 0
    discarded_count: int = 0
    blocked_count: int = 0


class CaptureResponse(_Receipt):
    ok: Literal[True] = True
    capture_id: str
    status: str
    replayed: bool
    summary: CaptureSummary
    created_memory_ids: tuple[str, ...] = ()
    pending_review_ids: tuple[str, ...] = ()
    failure_code: str | None = None


class MemoryHookClientError(RuntimeError):
    """稳定且不含 Secret 的客户端错误。"""

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
        event_id: str,
        profile_id: str | None = None,
        conversation_id: str,
        turn_id: str,
        observed_at: datetime,
        user_input: str,
        final_output: str,
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
        payload = await self._call_tool("recall_memory", arguments)
        try:
            return RecallResponse.model_validate(payload)
        except ValueError as exc:
            raise MemoryHookClientError("invalid_recall_response") from exc

    async def capture_completed_turn(
        self,
        *,
        event_id: str,
        profile_id: str | None = None,
        conversation_id: str,
        turn_id: str,
        observed_at: datetime,
        user_input: str,
        final_output: str,
    ) -> CaptureResponse:
        arguments: dict[str, Any] = {
            "event_id": event_id,
            "contract_version": "1",
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "observed_at": observed_at.isoformat(),
            "messages": [
                {
                    "role": "user",
                    "content": user_input,
                    "message_id": f"{turn_id}:user",
                },
                {
                    "role": "assistant",
                    "content": final_output,
                    "message_id": f"{turn_id}:assistant",
                },
            ],
        }
        if profile_id is not None:
            arguments["profile_id"] = profile_id
        payload = await self._call_tool("capture_completed_turn", arguments)
        try:
            return CaptureResponse.model_validate(payload)
        except ValueError as exc:
            raise MemoryHookClientError("invalid_capture_response") from exc

    async def _call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            async with self._session() as session:
                result = await session.call_tool(name, arguments)
        except MemoryHookClientError:
            raise
        except httpx.HTTPStatusError as exc:
            raise _http_status_error(exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise MemoryHookClientError(
                "memory_mcp_unavailable",
                retryable=True,
            ) from exc
        except Exception as exc:
            raise MemoryHookClientError(
                "memory_mcp_client_error",
            ) from exc

        payload = _structured_payload(result.get("structuredContent"))
        if result.get("isError") is True or payload.get("ok") is False:
            raise MemoryHookClientError(
                str(payload.get("error_code", "memory_mcp_tool_error")),
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
                timeout=self._settings.timeout_seconds,
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
        if self._session_id is None:
            return
        try:
            await self._client.delete(
                self._url,
                headers=self._headers(),
            )
        except httpx.HTTPError:
            pass

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "tools/call",
            {
                "name": name,
                "arguments": dict(arguments),
            },
        )

    async def _request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        initialization: bool = False,
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
    content_type = response.headers.get("content-type", "").casefold()
    if not content_type.startswith("application/json"):
        raise MemoryHookClientError("unsupported_mcp_response_type")
    try:
        payload = response.json()
    except ValueError as exc:
        raise MemoryHookClientError("invalid_mcp_response") from exc
    if not isinstance(payload, dict):
        raise MemoryHookClientError("invalid_mcp_response")
    if payload.get("jsonrpc") != "2.0" or payload.get("id") != request_id:
        raise MemoryHookClientError("invalid_mcp_response")
    if "error" in payload:
        raise MemoryHookClientError("memory_mcp_protocol_error")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise MemoryHookClientError("invalid_mcp_response")
    return {str(key): value for key, value in result.items()}


def _structured_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MemoryHookClientError("missing_structured_response")
    nested = value.get("result")
    payload = nested if isinstance(nested, dict) else value
    return {str(key): item for key, item in payload.items()}


def _http_status_error(status_code: int) -> MemoryHookClientError:
    if status_code in {401, 403}:
        return MemoryHookClientError("memory_mcp_auth_rejected")
    if status_code in {408, 425, 429} or status_code >= 500:
        return MemoryHookClientError(
            "memory_mcp_unavailable",
            retryable=True,
        )
    return MemoryHookClientError("memory_mcp_request_rejected")
