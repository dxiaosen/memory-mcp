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


def test_agent_recall_timeout_default_and_env_override() -> None:
    """recall 超时默认 15s，可由 env 覆盖。Phase 1 后 capture 超时已移除。"""

    settings = MemoryHookSettings(
        mcp_url="https://memory.internal/mcp",
        bearer_token="configured-token",
        _env_file=None,
    )
    assert settings.recall_timeout_seconds == 15.0

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MEMORY_MCP_URL", "https://memory.internal/mcp")
    monkeypatch.setenv("MEMORY_MCP_TOKEN", "agent-process-secret")
    monkeypatch.setenv("MEMORY_HOOK_RECALL_TIMEOUT_SECONDS", "12")
    try:
        settings = MemoryHookSettings()
        assert settings.recall_timeout_seconds == 12.0
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


def test_mcp_client_omits_unspecified_profile_from_recall_arguments() -> None:
    class RecordingClient(MemoryMcpClient):
        def __init__(self, settings: MemoryHookSettings) -> None:
            super().__init__(settings)
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def _call_tool(self, name, arguments, *, timeout=None):
            self.calls.append((name, dict(arguments)))
            return {
                "ok": True,
                "request_id": "request-1",
                "items": [],
                "rendered_context": "",
                "estimated_tokens": 0,
                "token_budget": 600,
                "truncated": False,
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

        assert all("profile_id" not in arguments for _, arguments in client.calls)

    anyio.run(scenario)
