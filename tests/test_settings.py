import pytest
from pydantic import ValidationError

from agent_lab.config import ChatModelSettings, MemorySettings


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
    assert settings.log_level == "INFO"
    assert settings.log_file.as_posix() == ".agent-lab/logs/agent-lab.log"
    assert settings.log_max_bytes == 10 * 1024 * 1024
    assert settings.log_backup_count == 5


def test_chat_model_settings_require_model_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("CHAT_MODEL_NAME", "CHAT_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        ChatModelSettings(_env_file=None)


def test_memory_settings_are_independent_from_model_credentials() -> None:
    settings = MemorySettings(_env_file=None)

    assert settings.memory_database_path.as_posix() == ".agent-lab/memory.db"
    assert settings.log_level == "INFO"
    assert not hasattr(settings, "chat_model_api_key")
