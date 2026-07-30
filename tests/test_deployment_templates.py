from pathlib import Path

_ROOT = Path(__file__).parents[1]


def test_systemd_units_load_secrets_from_environment_file() -> None:
    service = (_ROOT / "deploy" / "systemd" / "agent-lab-memory.service").read_text(
        encoding="utf-8"
    )
    migration = (
        _ROOT / "deploy" / "systemd" / "agent-lab-memory-migrate.service"
    ).read_text(encoding="utf-8")

    assert "EnvironmentFile=/etc/agent-lab/memory-mcp.env" in service
    assert "EnvironmentFile=/etc/agent-lab/memory-mcp.env" in migration
    assert "ExecStart=/opt/agent-lab/.venv/bin/memory-mcp" in service
    assert "ExecStart=/opt/agent-lab/.venv/bin/memory-db migrate" in migration
    assert "postgresql://" not in service
    assert "postgresql://" not in migration


def test_optional_nginx_template_keeps_application_private() -> None:
    config = (_ROOT / "deploy" / "nginx" / "agent-lab-memory.conf.example").read_text(
        encoding="utf-8"
    )

    assert "proxy_pass http://127.0.0.1:8765" in config
    assert "proxy_buffering off" in config
    assert "proxy_request_buffering off" in config
    assert "Authorization" not in config
