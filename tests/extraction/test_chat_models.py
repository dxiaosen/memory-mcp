from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from memory_mcp.extraction.chat_models import create_chat_model
from memory_mcp.extraction.settings import ChatModelSettings


def _settings(**overrides: object) -> ChatModelSettings:
    values: dict[str, object] = {
        "chat_model_name": "chat-model",
        "chat_model_api_key": "chat-key",
        "_env_file": None,
    }
    values.update(overrides)
    return ChatModelSettings(**values)


def test_chat_model_factory_uses_configured_provider() -> None:
    deepseek_model = create_chat_model(_settings(chat_model_provider="deepseek"))
    openai_model = create_chat_model(_settings(chat_model_provider="openai"))

    assert isinstance(deepseek_model, ChatDeepSeek)
    assert deepseek_model.extra_body == {"thinking": {"type": "disabled"}}
    assert isinstance(openai_model, ChatOpenAI)
