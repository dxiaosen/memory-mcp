import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import anyio
import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from memory_mcp.app import create_memory_mcp_server
from memory_mcp.core import (
    AssertionKind,
    ExpressionBasis,
    MemoryRelationPolicy,
    PrincipalContext,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.profiles import GeneralWorkProfile
from memory_mcp.settings import MemoryServerSettings
from pydantic import SecretStr

from tests.support.fakes import (
    FakeCandidateExtractor,
    TestMemoryProfile,
    candidate_proposal,
    project_preference_command,
)

_OBSERVED_AT = "2026-07-30T10:00:00+08:00"
_TOKEN_A_AGENT_A = "analyst-a-agent-a-token-00000000001"
_TOKEN_A_AGENT_B = "analyst-a-agent-b-token-00000000002"
_TOKEN_A_READ = "analyst-a-read-only-token-0000000003"
_TOKEN_B_AGENT_B = "analyst-b-agent-b-token-00000000004"


def _token_payload() -> str:
    return json.dumps(
        {
            _TOKEN_A_AGENT_A: {
                "tenant_id": "default",
                "subject_id": "analyst-a",
                "scopes": [
                    "memory:read",
                    "memory:write",
                    "memory:review",
                ],
            },
            _TOKEN_A_AGENT_B: {
                "tenant_id": "default",
                "subject_id": "analyst-a",
                "scopes": [
                    "memory:read",
                    "memory:write",
                    "memory:review",
                ],
            },
            _TOKEN_A_READ: {
                "tenant_id": "default",
                "subject_id": "analyst-a",
                "scopes": ["memory:read"],
            },
            _TOKEN_B_AGENT_B: {
                "tenant_id": "default",
                "subject_id": "analyst-b",
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
        auth_tokens=SecretStr(_token_payload()),
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
        "profile_id": "general-work",
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
    memory_service=None,
) -> Iterator[str]:
    port = _free_port()
    settings = _settings(port)
    service = memory_service or create_memory_service(
        InMemoryMemoryRepository(),
        [GeneralWorkProfile()],
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
                health = response.json()
                assert health["maintenance"]["state"] in {"starting", "ok"}
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
                    "revoke_memory",
                    "link_memories",
                    "revoke_memory_relation",
                    "search_memories",
                    "batch_confirm_pending",
                    "get_memory_stats",
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
                assert detail["item"]["evidence"][0]["source_type"] == "conversation"
                assert detail["item"]["extraction_confidence"] == 0.95
                assert detail["item"]["verification_status"] == "user_asserted"
                assert detail["item"]["sensitivity_level"] == "confidential"
                assert detail["item"]["valid_until"] is None
                recalled = _payload(
                    await session.call_tool(
                        "recall_memory",
                        arguments={
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
                assert recalled["items"][0]["verification_status"] == "user_asserted"
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
                _TOKEN_A_AGENT_A,
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
                            "profile_id": "general-work",
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
                _TOKEN_A_AGENT_B,
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
                revoke = _payload(
                    await session.call_tool(
                        "revoke_memory",
                        arguments={"memory_id": memory_id},
                    )
                )
                assert revoke["error_code"] == "memory_unavailable"
                recalled = _payload(
                    await session.call_tool(
                        "recall_memory",
                        arguments={
                            "profile_id": "general-work",
                            "query": "项目周报 表格",
                        },
                    )
                )
                assert recalled["items"] == []

            anyio.run(
                _with_session,
                url,
                _TOKEN_B_AGENT_B,
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
                revoke_denied = _payload(
                    await session.call_tool(
                        "revoke_memory",
                        arguments={"memory_id": memory_id},
                    )
                )
                assert revoke_denied["error_code"] == "permission_denied"

            anyio.run(
                _with_session,
                url,
                _TOKEN_A_READ,
                read_only,
            )

            async def revoke_owner_memory(session: ClientSession):
                revoked = _payload(
                    await session.call_tool(
                        "revoke_memory",
                        arguments={"memory_id": memory_id},
                    )
                )
                repeated = _payload(
                    await session.call_tool(
                        "revoke_memory",
                        arguments={"memory_id": memory_id},
                    )
                )
                assert revoked["memory"] == repeated["memory"]
                assert revoked["memory"]["lifecycle_status"] == "revoked"
                memories = _payload(
                    await session.call_tool("list_memories", arguments={})
                )
                assert memories["items"] == []
                recalled = _payload(
                    await session.call_tool(
                        "recall_memory",
                        arguments={
                            "profile_id": "general-work",
                            "query": "周报 要点",
                        },
                    )
                )
                assert recalled["items"] == []
                detail = _payload(
                    await session.call_tool(
                        "get_memory",
                        arguments={
                            "memory_id": memory_id,
                            "include_history": True,
                        },
                    )
                )
                assert detail["item"]["lifecycle_status"] == "revoked"
                assert detail["history"][0]["lifecycle_status"] == "revoked"

            anyio.run(
                _with_session,
                url,
                _TOKEN_A_AGENT_B,
                revoke_owner_memory,
            )

    finally:
        assert all(
            "secret-password-123" not in request.content
            for request in first_extractor.requests
        )


def test_remote_transport_uses_principal_default_when_profile_is_omitted() -> None:
    extractor = _extractor()
    with _running_server(extractor=extractor) as url:

        async def call_without_profile(session: ClientSession) -> None:
            event = _event(event_id="event-server-default")
            event.pop("profile_id")
            capture = _payload(
                await session.call_tool(
                    "capture_completed_turn",
                    arguments=event,
                )
            )
            recall = _payload(
                await session.call_tool(
                    "recall_memory",
                    arguments={"query": "周报格式"},
                )
            )

            assert capture["ok"] is True
            assert extractor.requests[0].profile_id == "general-work"
            assert recall["ok"] is True

        anyio.run(
            _with_session,
            url,
            _TOKEN_A_AGENT_A,
            call_without_profile,
        )


def test_remote_transport_memory_relation_scopes_isolation_and_history() -> None:
    profile = replace(
        TestMemoryProfile(),
        relation_policies={
            "supports": MemoryRelationPolicy(
                source_memory_types=frozenset({"preference"}),
                target_memory_types=frozenset({"ongoing_item"}),
                description="A preference supports an ongoing item.",
            )
        },
    )
    service = create_memory_service(InMemoryMemoryRepository(), [profile])
    principal = PrincipalContext("default:analyst-a")
    source = service.create_memory(principal, project_preference_command())
    target = service.create_memory(
        principal,
        replace(
            project_preference_command(),
            subject="model-update",
            memory_type="ongoing_item",
            content="持续更新项目模型",
            source_turn_id="session-1-turn-2",
            source_expression="持续更新项目模型",
        ),
    )

    with _running_server(
        extractor=FakeCandidateExtractor(),
        memory_service=service,
    ) as url:

        async def link_and_read(session: ClientSession):
            extra_identity = await session.call_tool(
                "link_memories",
                arguments={
                    "source_memory_id": str(source.item.memory_id),
                    "target_memory_id": str(target.item.memory_id),
                    "relation_type": "supports",
                    "owner_id": "default:analyst-b",
                },
            )
            assert extra_identity.isError is True
            invalid_direction = _payload(
                await session.call_tool(
                    "link_memories",
                    arguments={
                        "source_memory_id": str(target.item.memory_id),
                        "target_memory_id": str(source.item.memory_id),
                        "relation_type": "supports",
                    },
                )
            )
            assert invalid_direction["error_code"] == "invalid_relation"
            arguments = {
                "source_memory_id": str(source.item.memory_id),
                "target_memory_id": str(target.item.memory_id),
                "relation_type": "supports",
            }
            linked = _payload(
                await session.call_tool("link_memories", arguments=arguments)
            )
            replay = _payload(
                await session.call_tool("link_memories", arguments=arguments)
            )
            assert linked["relation"] == replay["relation"]
            assert linked["relation"]["origin"] == "manual"
            assert linked["relation"]["scope"] == "item"
            assert linked["relation"]["source_revision_id"] is not None
            assert linked["relation"]["target_revision_id"] is not None
            assert linked["relation"]["provenance"] is None
            detail = _payload(
                await session.call_tool(
                    "get_memory",
                    arguments={"memory_id": str(target.item.memory_id)},
                )
            )
            assert detail["relations"][0]["direction"] == "incoming"
            return linked["relation"]["relation_id"]

        relation_id = anyio.run(
            _with_session,
            url,
            _TOKEN_A_AGENT_A,
            link_and_read,
        )

        async def read_only(session: ClientSession):
            denied = _payload(
                await session.call_tool(
                    "link_memories",
                    arguments={
                        "source_memory_id": str(source.item.memory_id),
                        "target_memory_id": str(target.item.memory_id),
                        "relation_type": "supports",
                    },
                )
            )
            assert denied["error_code"] == "permission_denied"
            revoke_denied = _payload(
                await session.call_tool(
                    "revoke_memory_relation",
                    arguments={"relation_id": relation_id},
                )
            )
            assert revoke_denied["error_code"] == "permission_denied"

        anyio.run(_with_session, url, _TOKEN_A_READ, read_only)

        async def other_owner(session: ClientSession):
            link_unavailable = _payload(
                await session.call_tool(
                    "link_memories",
                    arguments={
                        "source_memory_id": str(source.item.memory_id),
                        "target_memory_id": str(target.item.memory_id),
                        "relation_type": "supports",
                    },
                )
            )
            assert link_unavailable["error_code"] == "memory_unavailable"
            unavailable = _payload(
                await session.call_tool(
                    "revoke_memory_relation",
                    arguments={"relation_id": relation_id},
                )
            )
            assert unavailable["error_code"] == "relation_unavailable"

        anyio.run(_with_session, url, _TOKEN_B_AGENT_B, other_owner)

        async def revoke_and_read_history(session: ClientSession):
            revoked = _payload(
                await session.call_tool(
                    "revoke_memory_relation",
                    arguments={"relation_id": relation_id},
                )
            )
            repeated = _payload(
                await session.call_tool(
                    "revoke_memory_relation",
                    arguments={"relation_id": relation_id},
                )
            )
            assert revoked["relation"] == repeated["relation"]
            assert revoked["relation"]["status"] == "revoked"
            default_detail = _payload(
                await session.call_tool(
                    "get_memory",
                    arguments={"memory_id": str(source.item.memory_id)},
                )
            )
            assert default_detail["relations"] == []
            history = _payload(
                await session.call_tool(
                    "get_memory",
                    arguments={
                        "memory_id": str(source.item.memory_id),
                        "include_history": True,
                    },
                )
            )
            assert history["relations"][0]["status"] == "revoked"

        anyio.run(
            _with_session,
            url,
            _TOKEN_A_AGENT_B,
            revoke_and_read_history,
        )


def test_agent_hook_adapter_cross_host_transport_and_owner_isolation(
    tmp_path,
) -> None:
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                "以后项目周报默认用表格",
                content="项目周报默认使用表格",
            ),
        )
    )

    hook_command = Path(sys.executable).with_name("memory-mcp-hook")

    def handle(
        url: str,
        token: str,
        event: dict[str, object],
    ) -> dict[str, object]:
        environment = os.environ.copy()
        environment.update(
            {
                "MEMORY_MCP_URL": url,
                "MEMORY_MCP_TOKEN": token,
            }
        )
        completed = subprocess.run(
            [str(hook_command)],
            input=json.dumps(event, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=True,
            cwd=tmp_path,
            env=environment,
        )
        assert token not in completed.stdout
        assert token not in completed.stderr
        for field in ("prompt", "last_assistant_message"):
            content = event.get(field)
            if isinstance(content, str):
                assert content not in completed.stderr
        return json.loads(completed.stdout)

    with _running_server(extractor=extractor) as url:
        codex_before = handle(
            url,
            _TOKEN_A_AGENT_A,
            {
                "session_id": "codex-session",
                "turn_id": "codex-turn-1",
                "cwd": str(tmp_path),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "以后项目周报默认用表格",
            },
        )
        assert codex_before == {}

        codex_after = handle(
            url,
            _TOKEN_A_AGENT_A,
            {
                "session_id": "codex-session",
                "turn_id": "codex-turn-1",
                "cwd": str(tmp_path),
                "hook_event_name": "Stop",
                "last_assistant_message": "好的，以后默认使用表格。",
            },
        )
        assert codex_after == {}

        claude_recall = handle(
            url,
            _TOKEN_A_AGENT_B,
            {
                "session_id": "claude-session",
                "prompt_id": "claude-prompt-1",
                "cwd": str(tmp_path),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "项目周报应该使用什么格式？",
            },
        )
        context = claude_recall["hookSpecificOutput"]["additionalContext"]
        assert "项目周报默认使用表格" in context

        generic_cwd = tmp_path / "generic-workspace"
        generic_recall = handle(
            url,
            _TOKEN_A_AGENT_B,
            {
                "conversation_id": "generic-conversation",
                "run_id": "generic-run-1",
                "cwd": str(generic_cwd),
                "hook_event_name": "BeforeRun",
                "user_input": "以后项目周报默认用表格，项目周报应该使用什么格式？",
            },
        )
        generic_context = generic_recall["hookSpecificOutput"]["additionalContext"]
        assert "项目周报默认使用表格" in generic_context
        assert len(list((generic_cwd / ".memory-mcp/hooks").glob("*.json"))) == 1

        generic_after = handle(
            url,
            _TOKEN_A_AGENT_B,
            {
                "conversation_id": "generic-conversation",
                "run_id": "generic-run-1",
                "cwd": str(generic_cwd),
                "hook_event_name": "AfterRun",
                "final_output": "应该继续使用表格。",
            },
        )
        assert generic_after == {}
        assert list((generic_cwd / ".memory-mcp/hooks").glob("*.json")) == []

        isolated = handle(
            url,
            _TOKEN_B_AGENT_B,
            {
                "session_id": "other-session",
                "turn_id": "other-turn-1",
                "cwd": str(tmp_path),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "项目周报应该使用什么格式？",
            },
        )
        assert isolated == {}
