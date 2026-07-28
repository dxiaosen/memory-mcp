"""根据配置创建不同供应商的聊天模型。"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from agent_lab.config import Settings
from agent_lab.exceptions import ConfigurationError


def create_chat_model(settings: Settings) -> BaseChatModel:
    """创建配置指定的 LangChain 聊天模型。"""

    common_options = {
        "model": settings.chat_model_name,
        "api_key": settings.chat_model_api_key,
        "temperature": settings.chat_model_temperature,
        "timeout": settings.chat_model_timeout_seconds,
        "max_retries": settings.chat_model_max_retries,
    }
    if settings.chat_model_base_url:
        common_options["base_url"] = settings.chat_model_base_url

    if settings.chat_model_provider == "deepseek":
        return ChatDeepSeek(**common_options)
    if settings.chat_model_provider == "openai":
        return ChatOpenAI(**common_options)

    raise ConfigurationError(
        f"Unsupported chat model provider: {settings.chat_model_provider}"
    )
