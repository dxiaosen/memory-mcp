import pytest

from memory_mcp.hooks import MemoryHookSettings, MemoryMcpClient


def test_agent_process_settings_use_stable_prefix_and_hide_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_HOOK_MCP_URL", "https://memory.internal/mcp")
    monkeypatch.setenv("MEMORY_HOOK_BEARER_TOKEN", "agent-process-secret")

    settings = MemoryHookSettings()

    assert str(settings.mcp_url) == "https://memory.internal/mcp"
    assert settings.token_value() == "agent-process-secret"
    assert "agent-process-secret" not in repr(settings)


def test_mcp_client_reuses_and_closes_its_http_pool() -> None:
    async def scenario() -> None:
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

    anyio.run(scenario)
