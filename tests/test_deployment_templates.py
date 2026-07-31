from pathlib import Path

from memory_mcp.extraction.factory import create_configured_candidate_extractor
from memory_mcp.extraction.settings import ChatModelSettings
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


def test_example_environment_is_parseable_without_external_calls() -> None:
    env_file = _ROOT / ".env.example"

    server = MemoryServerSettings(_env_file=env_file)
    model = ChatModelSettings(_env_file=env_file)
    extractor = create_configured_candidate_extractor(server)

    assert server.extractor_backend == "fixed"
    assert len(server.require_demo_principals()) == 3
    assert model.chat_model_provider == "openai"
    assert extractor.model_id == "fixed-candidate-catalog"
