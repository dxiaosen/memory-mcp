from pathlib import Path

from memory_mcp.extraction.settings import ExtractionSettings
from memory_mcp.hooks import MemoryHookSettings
from memory_mcp.server.settings import MemoryServerSettings

_ROOT = Path(__file__).parents[1]


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
    env_file = _ROOT / ".env.example"

    server = MemoryServerSettings(_env_file=env_file)
    extraction = ExtractionSettings(_env_file=env_file)
    text = env_file.read_text(encoding="utf-8")

    assert server.log_content is False
    principals = tuple(server.require_configured_principals().values())
    assert len(principals) == 1
    assert principals[0].owner_key == "tenant-001:subject-001"
    assert principals[0].tenant_id == "tenant-001"
    assert principals[0].subject_id == "subject-001"
    assert extraction.provider == "openai"
    assert extraction.model_name == "replace-with-model-name"
    assert "MEMORY_HOOK_" not in text
    assert "FIXED_CANDIDATES" not in text
    assert "MODEL_BACKEND" not in text
    assert "MEMORY_MCP_TEST_DATABASE_URL" not in text
    assert "MEMORY_MCP_EXTRACTION_" not in text
    assert "MEMORY_MCP_AUTH_TOKENS_JSON" not in text


def test_agent_environment_template_contains_only_hook_settings() -> None:
    env_file = _ROOT / "examples" / ".env.example"

    settings = MemoryHookSettings(_env_file=env_file)
    text = env_file.read_text(encoding="utf-8")

    assert str(settings.mcp_url) == "https://memory.example.com/mcp"
    assert settings.scenario == "general-work"
    assert "MEMORY_MCP_" not in text
