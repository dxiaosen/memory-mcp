import json
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

import anyio
import httpx
import psycopg
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from memory_mcp.app import create_memory_mcp_server
from memory_mcp.core.ports import CandidateExtractor
from memory_mcp.settings import MemoryServerSettings
from pydantic import SecretStr

from tests.support.fakes import FakeCandidateExtractor, candidate_proposal

_DATABASE_ENV = "MEMORY_MCP_TEST_DATABASE_URL"
_OWNER_A_AGENT_A_TOKEN = "owner-a-agent-a-token-000000000001"
_OWNER_A_AGENT_B_TOKEN = "owner-a-agent-b-token-000000000002"
_OWNER_B_AGENT_TOKEN = "owner-b-agent-token-0000000000003"


def _connect_safely(database_url: SecretStr):
    try:
        return psycopg.connect(database_url.get_secret_value())
    except psycopg.Error:
        raise RuntimeError("PostgreSQL test connection failed") from None


def _tokens() -> SecretStr:
    return SecretStr(
        json.dumps(
            {
                _OWNER_A_AGENT_A_TOKEN: {
                    "tenant_id": "test",
                    "subject_id": "owner-a",
                },
                _OWNER_A_AGENT_B_TOKEN: {
                    "tenant_id": "test",
                    "subject_id": "owner-a",
                },
                _OWNER_B_AGENT_TOKEN: {
                    "tenant_id": "test",
                    "subject_id": "owner-b",
                },
            }
        )
    )


def _event() -> dict[str, object]:
    return {
        "profile_id": "general-work",
        "conversation_id": "postgresql-conversation",
        "turn_id": "postgresql-turn-1",
        "user_input": "以后项目周报默认用表格",
        "final_output": "好的，已记录周报默认使用表格格式。",
    }


@contextmanager
def _running_server(
    database_url: SecretStr,
    extractor: CandidateExtractor | None = None,
) -> Iterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    settings = MemoryServerSettings(
        database_url=database_url,
        database_migrate_on_startup=True,
        database_pool_min_size=1,
        database_pool_max_size=4,
        auth_tokens=_tokens(),
        host="127.0.0.1",
        port=port,
        log_file=None,
    )
    server = create_memory_mcp_server(
        settings,
        candidate_extractor=extractor,
    )
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(
            server.streamable_http_app(),
            host=settings.host,
            port=settings.port,
            log_level="error",
            lifespan="on",
        )
    )
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()
    health_url = f"http://127.0.0.1:{port}{settings.health_path}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(health_url, timeout=5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    else:
        uvicorn_server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("PostgreSQL MCP server did not become healthy")
    try:
        yield f"http://127.0.0.1:{port}{settings.mcp_path}"
    finally:
        uvicorn_server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


@asynccontextmanager
async def _session(url: str, token: str) -> AsyncIterator[ClientSession]:
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    ) as client:
        async with streamable_http_client(
            url,
            http_client=client,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session


def _payload(result) -> dict[str, object]:
    assert result.structuredContent is not None
    nested = result.structuredContent.get("result")
    return nested if isinstance(nested, dict) else result.structuredContent


def test_postgresql_mcp_three_turn_recall_and_restart() -> None:
    configured_database_url = os.environ.get(_DATABASE_ENV)
    if not configured_database_url:
        pytest.skip(f"{_DATABASE_ENV} is not configured")
    database_url = SecretStr(configured_database_url)
    del configured_database_url
    with _connect_safely(database_url) as connection:
        if "test" not in connection.info.dbname.casefold():
            pytest.fail(f"{_DATABASE_ENV} must select a disposable test database")
    from memory_mcp.core.adapters.postgresql.schema import apply_migrations

    apply_migrations(database_url.get_secret_value())
    _truncate(database_url)
    first_extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                "以后项目周报默认用表格",
                content="项目周报默认使用表格",
            ),
        )
    )
    try:
        with _running_server(database_url, first_extractor) as first_url:

            async def first_turn() -> None:
                async with _session(first_url, _OWNER_A_AGENT_A_TOKEN) as session:
                    captured = _payload(
                        await session.call_tool(
                            "capture_completed_turn",
                            arguments=_event(),
                        )
                    )
                    assert captured["replayed"] is False

            anyio.run(first_turn)

            async def second_turn() -> None:
                async with _session(first_url, _OWNER_A_AGENT_B_TOKEN) as session:
                    recalled = _payload(
                        await session.call_tool(
                            "recall_memory",
                            arguments={
                                "profile_id": "general-work",
                                "query": "项目周报 表格",
                                "subject": "weekly-report",
                            },
                        )
                    )
                    assert [item["content"] for item in recalled["items"]] == [
                        "项目周报默认使用表格"
                    ]

            anyio.run(second_turn)

            async def isolated_turn() -> None:
                async with _session(first_url, _OWNER_B_AGENT_TOKEN) as session:
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

            anyio.run(isolated_turn)

        replay_extractor = FakeCandidateExtractor()
        with _running_server(database_url, replay_extractor) as reopened_url:

            async def third_turn() -> None:
                async with _session(reopened_url, _OWNER_A_AGENT_A_TOKEN) as session:
                    replay = _payload(
                        await session.call_tool(
                            "capture_completed_turn",
                            arguments=_event(),
                        )
                    )
                    assert replay["replayed"] is True
                    recalled = _payload(
                        await session.call_tool(
                            "recall_memory",
                            arguments={
                                "profile_id": "general-work",
                                "query": "项目周报 表格",
                                "subject": "weekly-report",
                            },
                        )
                    )
                    assert len(recalled["items"]) == 1

            anyio.run(third_turn)
        assert replay_extractor.requests == []
    finally:
        _truncate(database_url)


def test_postgresql_cross_agent_capture_and_recall_end_to_end() -> None:
    configured_database_url = os.environ.get(_DATABASE_ENV)
    if not configured_database_url:
        pytest.skip(f"{_DATABASE_ENV} is not configured")
    database_url = SecretStr(configured_database_url)
    del configured_database_url
    with _connect_safely(database_url) as connection:
        if "test" not in connection.info.dbname.casefold():
            pytest.fail(f"{_DATABASE_ENV} must select a disposable test database")

    from memory_mcp.core.adapters.postgresql.schema import apply_migrations

    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                "以后项目周报默认用表格",
                content="项目周报默认使用表格",
                confidence=0.98,
                save_rationale="用户明确表达了长期格式偏好",
            ),
        )
    )

    async def profile_id(url: str) -> None:
        # Agent A 捕获一条长期格式偏好。
        async with _session(url, _OWNER_A_AGENT_A_TOKEN) as session:
            captured = _payload(
                await session.call_tool(
                    "capture_completed_turn",
                    arguments={
                        "profile_id": "general-work",
                        "conversation_id": "hook-agent-a",
                        "turn_id": "hook-turn-a-1",
                        "user_input": "以后项目周报默认用表格",
                        "final_output": "好的，已记录。",
                    },
                )
            )
            assert captured["replayed"] is False
            assert len(captured["created_memory_ids"]) == 1

        # 同 owner 的 Agent B 召回到该记忆。
        async with _session(url, _OWNER_A_AGENT_B_TOKEN) as session:
            recalled = _payload(
                await session.call_tool(
                    "recall_memory",
                    arguments={
                        "profile_id": "general-work",
                        "query": "项目周报 表格",
                        "subject": "weekly-report",
                    },
                )
            )
            assert len(recalled["items"]) == 1
            assert "项目周报默认使用表格" in recalled["rendered_context"]

        # 隔离 owner 的 Agent 召回不到。
        async with _session(url, _OWNER_B_AGENT_TOKEN) as session:
            isolated = _payload(
                await session.call_tool(
                    "recall_memory",
                    arguments={
                        "profile_id": "general-work",
                        "query": "项目周报 表格",
                        "subject": "weekly-report",
                    },
                )
            )
            assert isolated["items"] == []

    apply_migrations(database_url.get_secret_value())
    _truncate(database_url)
    try:
        with _running_server(
            database_url,
            extractor,
        ) as url:
            anyio.run(profile_id, url)
    finally:
        _truncate(database_url)


def _truncate(database_url: SecretStr) -> None:
    # 外键已移除，CASCADE 不再级联清空；显式清空全部 memory 表。
    with _connect_safely(database_url) as connection:
        connection.execute(
            """
            TRUNCATE TABLE memory_capture_outcomes,
                            memory_relations,
                            memory_review_documents,
                            memory_reviews,
                            memory_evidence_documents,
                            memory_evidence,
                            memory_revisions,
                            memory_items,
                            memory_captures, memory_team_extractions
            """
        )
