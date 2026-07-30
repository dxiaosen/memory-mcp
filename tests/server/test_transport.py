import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import anyio
import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from memory_mcp.core import AssertionKind, ExpressionBasis
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.scenarios import GeneralWorkPolicy
from memory_mcp.server.app import create_memory_mcp_server
from memory_mcp.server.settings import MemoryServerSettings
from tests.support.fakes import (
    FakeCandidateExtractor,
    candidate_proposal,
)

_OBSERVED_AT = "2026-07-30T10:00:00+08:00"


def _token_payload() -> str:
    return json.dumps(
        {
            "token-a-agent-a": {
                "owner_key": "analyst-a",
                "tenant_id": "demo",
                "subject_id": "analyst-a",
                "client_id": "agent-a",
                "scopes": [
                    "memory:read",
                    "memory:write",
                    "memory:review",
                ],
            },
            "token-a-agent-b": {
                "owner_key": "analyst-a",
                "tenant_id": "demo",
                "subject_id": "analyst-a",
                "client_id": "agent-b",
                "scopes": [
                    "memory:read",
                    "memory:write",
                    "memory:review",
                ],
            },
            "token-a-read": {
                "owner_key": "analyst-a",
                "tenant_id": "demo",
                "subject_id": "analyst-a",
                "client_id": "read-only-client",
                "scopes": ["memory:read"],
            },
            "token-b-agent-b": {
                "owner_key": "analyst-b",
                "tenant_id": "demo",
                "subject_id": "analyst-b",
                "client_id": "agent-b",
                "scopes": [
                    "memory:read",
                    "memory:write",
                    "memory:review",
                ],
            },
        }
    )


def _settings(port: int) -> MemoryServerSettings:
    return MemoryServerSettings(
        host="127.0.0.1",
        port=port,
        demo_tokens_json=SecretStr(_token_payload()),
        log_file=None,
    )


def _event(
    *,
    event_id: str = "event-1",
    assistant_text: str = "好的。",
    contract_version: str = "1",
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "contract_version": contract_version,
        "scenario": "general-work",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "observed_at": _OBSERVED_AT,
        "messages": [
            {
                "role": "user",
                "content": (
                    "密码是 secret-password-123。"
                    "以后项目周报默认用表格。"
                    "我可能喜欢要点。"
                ),
            },
            {
                "role": "assistant",
                "content": assistant_text,
            },
        ],
    }


def _extractor() -> FakeCandidateExtractor:
    return FakeCandidateExtractor(
        (
            candidate_proposal("以后项目周报默认用表格"),
            candidate_proposal(
                "我可能喜欢要点",
                content="用户可能偏好要点",
                assertion_kind=AssertionKind.SYSTEM_INFERENCE,
                expression_basis=ExpressionBasis.INFERRED,
            ),
            candidate_proposal(
                "好的",
                content="助手建议继续使用表格",
            ),
        )
    )


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def _running_server(
    *,
    extractor: FakeCandidateExtractor,
) -> Iterator[str]:
    port = _free_port()
    settings = _settings(port)
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [GeneralWorkPolicy()],
        candidate_extractor=extractor,
    )
    mcp_server = create_memory_mcp_server(
        settings,
        memory_service=service,
    )
    app = mcp_server.streamable_http_app()
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level="error",
            lifespan="on",
        )
    )
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()
    health_url = f"http://{settings.host}:{settings.port}{settings.health_path}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            response = httpx.get(health_url, timeout=0.25)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    else:
        uvicorn_server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("Memory MCP test server did not become healthy")
    try:
        yield f"http://{settings.host}:{settings.port}{settings.mcp_path}"
    finally:
        uvicorn_server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


async def _with_session(
    url: str,
    token: str,
    operation,
):
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    ) as http_client:
        async with streamable_http_client(
            url,
            http_client=http_client,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await operation(session)


def _payload(result) -> dict[str, object]:
    assert result.structuredContent is not None
    payload = result.structuredContent
    nested = payload.get("result")
    if isinstance(nested, dict):
        return nested
    return payload


def test_remote_transport_auth_schema_capture_and_governance() -> None:
    first_extractor = _extractor()
    try:
        with _running_server(
            extractor=first_extractor,
        ) as url:
            unauthenticated = httpx.post(
                url,
                json={},
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            )
            assert unauthenticated.status_code == 401

            async def first_run(session: ClientSession):
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert names == {
                    "capture_completed_turn",
                    "list_memories",
                    "get_memory",
                    "list_pending_reviews",
                    "confirm_pending_memory",
                    "reject_pending_memory",
                    "recall_memory",
                }
                capture_tool = next(
                    tool
                    for tool in tools.tools
                    if tool.name == "capture_completed_turn"
                )
                assert capture_tool.inputSchema["additionalProperties"] is False

                owner_attempt = await session.call_tool(
                    "capture_completed_turn",
                    arguments={**_event(), "owner_id": "analyst-b"},
                )
                assert owner_attempt.isError is True

                first = await session.call_tool(
                    "capture_completed_turn",
                    arguments=_event(),
                )
                first_payload = _payload(first)
                assert first_payload["ok"] is True
                assert first_payload["replayed"] is False
                assert first_payload["summary"] == {
                    "auto_saved_count": 1,
                    "pending_count": 2,
                    "discarded_count": 0,
                    "blocked_count": 1,
                }
                serialized = json.dumps(first_payload, ensure_ascii=False)
                assert "secret-password-123" not in serialized

                replay = await session.call_tool(
                    "capture_completed_turn",
                    arguments=_event(),
                )
                assert _payload(replay)["replayed"] is True

                conflict = await session.call_tool(
                    "capture_completed_turn",
                    arguments=_event(assistant_text="已记录。"),
                )
                assert _payload(conflict)["error_code"] == "idempotency_conflict"

                unsupported = await session.call_tool(
                    "capture_completed_turn",
                    arguments=_event(
                        event_id="event-version-2",
                        contract_version="2",
                    ),
                )
                assert (
                    _payload(unsupported)["error_code"]
                    == "unsupported_contract_version"
                )

                memories = await session.call_tool("list_memories", arguments={})
                memory_payload = _payload(memories)
                assert len(memory_payload["items"]) == 1
                assert "evidence" not in memory_payload["items"][0]
                memory_id = memory_payload["items"][0]["memory_id"]
                detail = _payload(
                    await session.call_tool(
                        "get_memory",
                        arguments={"memory_id": memory_id},
                    )
                )
                assert detail["item"]["evidence"][0]["source_role"] == "user"
                recalled = _payload(
                    await session.call_tool(
                        "recall_memory",
                        arguments={
                            "scenario": "general-work",
                            "query": "项目周报 表格",
                            "subject": "weekly-report",
                        },
                    )
                )
                assert len(recalled["items"]) == 1
                assert (
                    recalled["items"][0]["revision_id"]
                    == (detail["item"]["revision_id"])
                )
                assert (
                    "current user request always takes priority"
                    in (recalled["rendered_context"])
                )

                pending = await session.call_tool(
                    "list_pending_reviews",
                    arguments={},
                )
                pending_payload = _payload(pending)
                assert len(pending_payload["items"]) == 2
                assert {item["source_role"] for item in pending_payload["items"]} == {
                    "user",
                    "assistant",
                }
                review_id = pending_payload["items"][0]["review_id"]
                return memory_id, review_id

            memory_id, review_id = anyio.run(
                _with_session,
                url,
                "token-a-agent-a",
                first_run,
            )

            async def second_agent(session: ClientSession):
                memories = _payload(
                    await session.call_tool("list_memories", arguments={})
                )
                assert len(memories["items"]) == 1
                confirmed = _payload(
                    await session.call_tool(
                        "confirm_pending_memory",
                        arguments={"review_id": review_id},
                    )
                )
                assert confirmed["status"] == "confirmed"
                repeated = _payload(
                    await session.call_tool(
                        "confirm_pending_memory",
                        arguments={"review_id": review_id},
                    )
                )
                assert repeated["status"] == "confirmed"
                recalled = _payload(
                    await session.call_tool(
                        "recall_memory",
                        arguments={
                            "scenario": "general-work",
                            "query": "周报 要点",
                            "subject": "weekly-report",
                        },
                    )
                )
                assert [item["content"] for item in recalled["items"]] == [
                    "用户可能偏好要点"
                ]
                detail = _payload(
                    await session.call_tool(
                        "get_memory",
                        arguments={
                            "memory_id": memory_id,
                            "include_history": True,
                        },
                    )
                )
                assert detail["history_included"] is True
                assert [item["revision_number"] for item in detail["history"]] == [2, 1]
                assert detail["history"][1]["lifecycle_status"] == "superseded"

            anyio.run(
                _with_session,
                url,
                "token-a-agent-b",
                second_agent,
            )

            async def other_user(session: ClientSession):
                memories = _payload(
                    await session.call_tool("list_memories", arguments={})
                )
                assert memories["items"] == []
                unavailable = _payload(
                    await session.call_tool(
                        "get_memory",
                        arguments={"memory_id": memory_id},
                    )
                )
                assert unavailable["error_code"] == "memory_unavailable"
                review = _payload(
                    await session.call_tool(
                        "confirm_pending_memory",
                        arguments={"review_id": review_id},
                    )
                )
                assert review["error_code"] == "review_unavailable"
                recalled = _payload(
                    await session.call_tool(
                        "recall_memory",
                        arguments={
                            "scenario": "general-work",
                            "query": "项目周报 表格",
                        },
                    )
                )
                assert recalled["items"] == []

            anyio.run(
                _with_session,
                url,
                "token-b-agent-b",
                other_user,
            )

            async def read_only(session: ClientSession):
                denied = _payload(
                    await session.call_tool(
                        "capture_completed_turn",
                        arguments=_event(event_id="read-only-event"),
                    )
                )
                assert denied["error_code"] == "permission_denied"

            anyio.run(
                _with_session,
                url,
                "token-a-read",
                read_only,
            )

    finally:
        assert all(
            "secret-password-123" not in request.content
            for request in first_extractor.requests
        )
