import pytest
from memory_mcp.extraction.settings import ExtractionSettings
from pydantic import ValidationError


def _valid_settings(**overrides: object) -> ExtractionSettings:
    values: dict[str, object] = {
        "model_name": "chat-model",
        "api_key": "chat-key",
        "_env_file": None,
    }
    values.update(overrides)
    return ExtractionSettings(**values)


def test_settings_have_explicit_engineering_defaults() -> None:
    settings = _valid_settings()

    assert settings.provider == "deepseek"
    assert settings.temperature == 0.0
    assert settings.timeout_seconds == 60.0
    assert settings.max_retries == 2


def test_real_extraction_settings_require_model_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MEMORY_MCP_MODEL_NAME",
        "MEMORY_MCP_MODEL_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        ExtractionSettings(_env_file=None)


def test_model_settings_use_deployment_facing_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_MCP_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MEMORY_MCP_MODEL_NAME", "configured-model")
    monkeypatch.setenv("MEMORY_MCP_MODEL_API_KEY", "configured-key")

    settings = ExtractionSettings(_env_file=None)

    assert settings.provider == "openai"
    assert settings.model_name == "configured-model"
    assert settings.require_api_key().get_secret_value() == "configured-key"
