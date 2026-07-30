"""Small typed client for the two MCP tools used by lifecycle hooks."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal, Protocol, Self

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict

from memory_mcp.memory_hooks.settings import MemoryHookSettings


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
    """Stable, secret-free client failure."""

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
        scenario: str,
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
        scenario: str,
        conversation_id: str,
        turn_id: str,
        observed_at: datetime,
        user_input: str,
        final_output: str,
    ) -> CaptureResponse: ...


class MemoryMcpClient:
    """Reuse one authenticated HTTP connection pool across MCP invocations."""

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
        """Close the reusable connection pool owned by this client."""

        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def recall_memory(
        self,
        *,
        scenario: str,
        query: str,
        subject: str | None,
        task_intent: str | None,
        max_items: int,
        token_budget: int,
    ) -> RecallResponse:
        payload = await self._call_tool(
            "recall_memory",
            {
                "scenario": scenario,
                "query": query,
                "subject": subject,
                "task_intent": task_intent,
                "max_items": max_items,
                "token_budget": token_budget,
            },
        )
        try:
            return RecallResponse.model_validate(payload)
        except ValueError as exc:
            raise MemoryHookClientError("invalid_recall_response") from exc

    async def capture_completed_turn(
        self,
        *,
        event_id: str,
        scenario: str,
        conversation_id: str,
        turn_id: str,
        observed_at: datetime,
        user_input: str,
        final_output: str,
    ) -> CaptureResponse:
        payload = await self._call_tool(
            "capture_completed_turn",
            {
                "event_id": event_id,
                "contract_version": "1",
                "scenario": scenario,
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
            },
        )
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
                result = await session.call_tool(name, arguments=dict(arguments))
        except MemoryHookClientError:
            raise
        except Exception as exc:
            raise MemoryHookClientError(
                "memory_mcp_unavailable", retryable=True
            ) from exc

        payload = _structured_payload(result.structuredContent)
        if result.isError or payload.get("ok") is False:
            code = payload.get("error_code", "memory_mcp_tool_error")
            retryable = payload.get("retryable", False)
            raise MemoryHookClientError(
                str(code),
                retryable=bool(retryable),
            )
        return payload

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        async with streamable_http_client(
            str(self._settings.mcp_url),
            http_client=self._ensure_http_client(),
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
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


def _structured_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MemoryHookClientError("missing_structured_response")
    nested = value.get("result")
    payload = nested if isinstance(nested, dict) else value
    return {str(key): item for key, item in payload.items()}
