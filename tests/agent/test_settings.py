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
    assert "agent-process-secret" not in repr(settings)


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

    import anyio

    anyio.run(profile_id)
