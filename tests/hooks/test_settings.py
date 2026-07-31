import pytest

from memory_mcp.hooks import MemoryHookSettings, MemoryMcpClient


def test_profile_settings_are_independent_and_hide_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_AGENT_A_MCP_URL", "http://agent-a.test/mcp")
    monkeypatch.setenv("MEMORY_AGENT_A_BEARER_TOKEN", "agent-a-secret")
    monkeypatch.setenv("MEMORY_AGENT_B_MCP_URL", "http://agent-b.test/mcp")
    monkeypatch.setenv("MEMORY_AGENT_B_BEARER_TOKEN", "agent-b-secret")

    agent_a = MemoryHookSettings.from_profile("agent-a")
    agent_b = MemoryHookSettings.from_profile("agent-b")

    assert str(agent_a.mcp_url) == "http://agent-a.test/mcp"
    assert str(agent_b.mcp_url) == "http://agent-b.test/mcp"
    assert agent_a.token_value() != agent_b.token_value()
    assert "agent-a-secret" not in repr(agent_a)
    assert "agent-b-secret" not in repr(agent_b)


def test_profile_name_is_bounded() -> None:
    with pytest.raises(ValueError, match="profile"):
        MemoryHookSettings.from_profile("../agent")


def test_mcp_client_reuses_and_closes_its_http_pool() -> None:
    async def scenario() -> None:
        settings = MemoryHookSettings(
            mcp_url="http://127.0.0.1:8765/mcp",
            bearer_token="test-token",
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
