from datetime import UTC, datetime

import anyio
import pytest
from memory_mcp_agent import MemoryHookSettings, MemoryMcpClient
from pydantic import ValidationError


def test_agent_process_settings_use_minimal_names_and_hide_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_MCP_URL", "https://memory.internal/mcp")
    monkeypatch.setenv("MEMORY_MCP_TOKEN", "agent-process-secret")

    settings = MemoryHookSettings()

    assert str(settings.mcp_url) == "https://memory.internal/mcp"
    assert settings.token_value() == "agent-process-secret"
    assert settings.profile_id is None
    assert "agent-process-secret" not in repr(settings)


def test_agent_profile_is_an_optional_advanced_override() -> None:
    settings = MemoryHookSettings(
        mcp_url="https://memory.internal/mcp",
        bearer_token="configured-token",
        profile_id="investment-research",
        _env_file=None,
    )

    assert settings.profile_id == "investment-research"


def test_legacy_agent_connection_names_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_HOOK_MCP_URL", "https://legacy.internal/mcp")
    monkeypatch.setenv("MEMORY_HOOK_BEARER_TOKEN", "legacy-secret")
    monkeypatch.delenv("MEMORY_MCP_URL", raising=False)
    monkeypatch.delenv("MEMORY_MCP_TOKEN", raising=False)

    with pytest.raises(ValidationError) as error:
        MemoryHookSettings()
    assert "legacy-secret" not in str(error.value)


def test_empty_agent_token_is_rejected_during_configuration() -> None:
    with pytest.raises(ValueError):
        MemoryHookSettings(
            mcp_url="https://memory.internal/mcp",
            bearer_token="",
            _env_file=None,
        )


def test_agent_uses_separate_recall_and_capture_timeouts() -> None:
    """recall 与 capture 使用各自超时（recommend.md §2 / P0-A）。

    单一 timeout_seconds=15s 会导致 capture（真实 ~33s）超时后并发重发。
    现拆为 recall_timeout_seconds（默认 15）与 capture_timeout_seconds（默认 70），
    满足 Claude Stop Hook 90s > Agent capture 70s > Server P95 <60s。
    """

    settings = MemoryHookSettings(
        mcp_url="https://memory.internal/mcp",
        bearer_token="configured-token",
        _env_file=None,
    )
    assert settings.recall_timeout_seconds == 15.0
    assert settings.capture_timeout_seconds == 70.0
    assert settings.capture_timeout_seconds > settings.recall_timeout_seconds


def test_agent_timeout_env_overrides_apply() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MEMORY_MCP_URL", "https://memory.internal/mcp")
    monkeypatch.setenv("MEMORY_MCP_TOKEN", "agent-process-secret")
    monkeypatch.setenv("MEMORY_HOOK_RECALL_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("MEMORY_HOOK_CAPTURE_TIMEOUT_SECONDS", "65")
    try:
        settings = MemoryHookSettings()
        assert settings.recall_timeout_seconds == 12.0
        assert settings.capture_timeout_seconds == 65.0
    finally:
        monkeypatch.undo()


def test_mcp_client_reuses_and_closes_its_http_pool() -> None:
    async def profile_id() -> None:
        settings = MemoryHookSettings(
            mcp_url="http://127.0.0.1:8765/mcp",
            bearer_token="configured-token",
            _env_file=None,
        )
        client = MemoryMcpClient(settings)

        first = client._ensure_http_client()
        assert client._ensure_http_client() is first
        await client.aclose()
        assert first.is_closed
        assert client._ensure_http_client() is not first
        await client.aclose()

    anyio.run(profile_id)


def test_mcp_client_omits_unspecified_profile_from_tool_arguments() -> None:
    class RecordingClient(MemoryMcpClient):
        def __init__(self, settings: MemoryHookSettings) -> None:
            super().__init__(settings)
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def _call_tool(self, name, arguments, *, timeout=None):
            self.calls.append((name, dict(arguments)))
            if name == "recall_memory":
                return {
                    "ok": True,
                    "request_id": "request-1",
                    "items": [],
                    "rendered_context": "",
                    "estimated_tokens": 0,
                    "token_budget": 600,
                    "truncated": False,
                }
            return {
                "ok": True,
                "request_id": "request-2",
                "capture_id": "capture-1",
                "status": "completed",
                "replayed": False,
                "summary": {},
            }

    async def scenario() -> None:
        settings = MemoryHookSettings(
            mcp_url="https://memory.internal/mcp",
            bearer_token="configured-token",
            _env_file=None,
        )
        client = RecordingClient(settings)
        await client.recall_memory(
            profile_id=None,
            query="query",
            subject=None,
            task_intent=None,
            max_items=5,
            token_budget=600,
        )
        await client.capture_completed_turn(
            event_id="event-1",
            profile_id=None,
            conversation_id="conversation-1",
            turn_id="turn-1",
            observed_at=datetime(2026, 8, 2, tzinfo=UTC),
            user_input="input",
            final_output="output",
        )

        assert all("profile_id" not in arguments for _, arguments in client.calls)

    anyio.run(scenario)
