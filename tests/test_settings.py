import pytest
from pydantic import ValidationError

from agent_lab.config import KnowledgeSettings, MemorySettings, Settings


def _valid_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "chat_model_name": "chat-model",
        "chat_model_api_key": "chat-key",
        "embedding_model_name": "embedding-model",
        "embedding_model_api_key": "embedding-key",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_have_explicit_engineering_defaults() -> None:
    settings = _valid_settings()

    assert settings.chat_model_provider == "deepseek"
    assert settings.embedding_model_provider == "openai"
    assert settings.document_chunk_size == 800
    assert settings.document_chunk_overlap == 120
    assert settings.retrieval_top_k == 4
    assert settings.agent_recursion_limit == 12
    assert settings.log_level == "INFO"
    assert settings.log_file.as_posix() == ".agent-lab/logs/agent-lab.log"
    assert settings.log_max_bytes == 10 * 1024 * 1024
    assert settings.log_backup_count == 5


def test_settings_require_chat_and_embedding_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "CHAT_MODEL_NAME",
        "CHAT_MODEL_API_KEY",
        "EMBEDDING_MODEL_NAME",
        "EMBEDDING_MODEL_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_knowledge_settings_do_not_require_chat_credentials() -> None:
    settings = KnowledgeSettings(
        embedding_model_name="embedding-model",
        embedding_model_api_key="embedding-key",
        _env_file=None,
    )

    assert settings.embedding_model_name == "embedding-model"
    assert not hasattr(settings, "chat_model_api_key")


def test_memory_settings_are_independent_from_model_credentials() -> None:
    settings = MemorySettings(_env_file=None)

    assert settings.memory_database_path.as_posix() == ".agent-lab/memory.db"
    assert settings.log_level == "INFO"
    assert not hasattr(settings, "embedding_model_api_key")


def test_chunk_overlap_must_be_smaller_than_chunk_size() -> None:
    with pytest.raises(ValidationError, match="DOCUMENT_CHUNK_OVERLAP"):
        _valid_settings(
            document_chunk_size=200,
            document_chunk_overlap=200,
        )
