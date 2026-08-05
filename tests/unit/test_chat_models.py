from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from memory_mcp.extraction.chat_models import create_chat_model
from memory_mcp.extraction.settings import ExtractionSettings


def _settings(**overrides: object) -> ExtractionSettings:
    values: dict[str, object] = {
        "model_name": "chat-model",
        "api_key": "chat-key",
        "_env_file": None,
    }
    values.update(overrides)
    return ExtractionSettings(**values)


def test_chat_model_factory_uses_configured_provider() -> None:
    deepseek_model = create_chat_model(_settings(provider="deepseek"))
    openai_model = create_chat_model(_settings(provider="openai"))

    assert isinstance(deepseek_model, ChatDeepSeek)
    assert deepseek_model.extra_body == {"thinking": {"type": "disabled"}}
    assert isinstance(openai_model, ChatOpenAI)
