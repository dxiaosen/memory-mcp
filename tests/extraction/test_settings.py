import pytest
from pydantic import ValidationError

from memory_mcp.extraction.settings import ChatModelSettings


def _valid_settings(**overrides: object) -> ChatModelSettings:
    values: dict[str, object] = {
        "chat_model_name": "chat-model",
        "chat_model_api_key": "chat-key",
        "_env_file": None,
    }
    values.update(overrides)
    return ChatModelSettings(**values)


def test_settings_have_explicit_engineering_defaults() -> None:
    settings = _valid_settings()

    assert settings.chat_model_provider == "deepseek"
    assert settings.chat_model_temperature == 0.0
    assert settings.chat_model_timeout_seconds == 60.0
    assert settings.chat_model_max_retries == 2


def test_chat_model_settings_require_model_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("CHAT_MODEL_NAME", "CHAT_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        ChatModelSettings(_env_file=None)
