from pathlib import Path

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
