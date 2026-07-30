from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from agent_lab.config import ChatModelSettings
from agent_lab.integrations import create_chat_model


def _settings(**overrides: object) -> ChatModelSettings:
    values: dict[str, object] = {
        "chat_model_name": "chat-model",
        "chat_model_api_key": "chat-key",
        "_env_file": None,
    }
    values.update(overrides)
    return ChatModelSettings(**values)


def test_chat_model_factory_uses_provider_specific_integrations() -> None:
    deepseek_model = create_chat_model(_settings(chat_model_provider="deepseek"))
    openai_model = create_chat_model(_settings(chat_model_provider="openai"))

    assert isinstance(deepseek_model, ChatDeepSeek)
    assert isinstance(openai_model, ChatOpenAI)
