import json
import tomllib
from pathlib import Path

import pytest
from memory_mcp.extraction.settings import ExtractionSettings
from memory_mcp.settings import MemoryServerSettings
from memory_mcp_agent import MemoryHookSettings

_ROOT = Path(__file__).parents[2]


def test_systemd_units_load_secrets_from_environment_file() -> None:
    service = (_ROOT / "deploy" / "systemd" / "memory-mcp.service").read_text(
        encoding="utf-8"
    )
    migration = (_ROOT / "deploy" / "systemd" / "memory-mcp-migrate.service").read_text(
        encoding="utf-8"
    )

    assert "EnvironmentFile=/etc/memory-mcp/memory-mcp.env" in service
    assert "EnvironmentFile=/etc/memory-mcp/memory-mcp.env" in migration
    assert "ExecStart=/opt/memory-mcp/.venv/bin/memory-mcp" in service
    assert "ExecStart=/opt/memory-mcp/.venv/bin/memory-mcp-db migrate" in migration
    assert "ReadWritePaths=/var/log/memory-mcp" in migration
    assert "postgresql://" not in service
    assert "postgresql://" not in migration


def test_server_environment_template_contains_only_production_service_settings() -> (
    None
):
    env_file = _ROOT / "server" / ".env.example"

    server = MemoryServerSettings(_env_file=env_file)
    extraction = ExtractionSettings(_env_file=env_file)
    text = env_file.read_text(encoding="utf-8")

    assert server.log_content is False
    principals = tuple(server.require_configured_principals().values())
    assert len(principals) == 1
    assert principals[0].owner_key == "tenant-001:subject-001"
    assert principals[0].tenant_id == "tenant-001"
    assert principals[0].subject_id == "subject-001"
    assert principals[0].default_profile_id == "investment-research"
    assert extraction.provider == "openai"
    assert extraction.model_name == "replace-with-model-name"
    assert server.maintenance_interval_seconds == 300
    assert "MEMORY_HOOK_" not in text
    assert "FIXED_CANDIDATES" not in text
    assert "MODEL_BACKEND" not in text
    assert "MEMORY_MCP_TEST_DATABASE_URL" not in text
    assert "MEMORY_MCP_EXTRACTION_" not in text
    assert "MEMORY_MCP_AUTH_TOKENS_JSON" not in text


def test_agent_environment_template_contains_only_hook_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 隔离 shell 里可能存在的同名环境变量，确保测试只读取 .env.example。
    monkeypatch.delenv("MEMORY_MCP_URL", raising=False)
    monkeypatch.delenv("MEMORY_MCP_TOKEN", raising=False)
    env_file = _ROOT / "agent" / ".env.example"

    settings = MemoryHookSettings(_env_file=env_file)
    text = env_file.read_text(encoding="utf-8")

    assert str(settings.mcp_url) == "https://memory.example.com/mcp"
    assert settings.profile_id is None
    assignments = {
        line.split("=", maxsplit=1)[0]
        for line in text.splitlines()
        if line and not line.startswith("#")
    }
    assert assignments == {"MEMORY_MCP_URL", "MEMORY_MCP_TOKEN"}
    # 可选的 recall/capture 超时只在注释中以安全默认值出现，
    # 不应作为必填赋值项（行首赋值）出现。
    uncommented_hook_lines = [
        line
        for line in text.splitlines()
        if line.startswith("MEMORY_HOOK_")
    ]
    assert uncommented_hook_lines == []


def test_agent_hook_templates_register_only_top_level_turn_events() -> None:
    codex = (_ROOT / "examples" / "agents" / "codex-hooks.json").read_text(
        encoding="utf-8"
    )
    claude = (_ROOT / "examples" / "agents" / "claude-code-settings.json").read_text(
        encoding="utf-8"
    )

    for text in (codex, claude):
        payload = json.loads(text)
        assert set(payload["hooks"]) == {"UserPromptSubmit", "Stop"}
        assert '"UserPromptSubmit"' in text
        assert '"Stop"' in text
        assert '"SubagentStop"' not in text
        assert text.count('"command": "memory-mcp-hook"') == 2
        assert "MEMORY_MCP_TOKEN" not in text


def test_server_and_agent_are_independent_distributions() -> None:
    workspace = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = tomllib.loads(
        (_ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8")
    )
    agent = tomllib.loads(
        (_ROOT / "agent" / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert workspace["tool"]["uv"]["workspace"]["members"] == [
        "server",
        "agent",
    ]
    assert "project" not in workspace

    assert server["project"]["name"] == "memory-mcp"
    assert set(server["project"]["scripts"]) == {"memory-mcp", "memory-mcp-db"}
    assert "memory-mcp-agent" not in server["project"]["dependencies"]

    assert agent["project"]["name"] == "memory-mcp-agent"
    assert agent["project"]["requires-python"] == ">=3.11"
    assert agent["project"]["scripts"] == {
        "memory-mcp-hook": "memory_mcp_agent.cli:main"
    }
    dependencies = tuple(agent["project"]["dependencies"])
    assert dependencies == (
        "httpx>=0.28.1",
        "pydantic>=2.11,<3",
        "pydantic-settings>=2.5,<3",
    )
