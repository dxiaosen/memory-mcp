"""Create provider-specific LangChain chat models."""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from memory_mcp.exceptions import ConfigurationError
from memory_mcp.extraction.settings import ChatModelSettings


def create_chat_model(settings: ChatModelSettings) -> BaseChatModel:
    """Create the LangChain chat model selected by configuration."""

    common_options: dict[str, Any] = {
        "model": settings.chat_model_name,
        "api_key": settings.chat_model_api_key,
        "temperature": settings.chat_model_temperature,
        "timeout": settings.chat_model_timeout_seconds,
        "max_retries": settings.chat_model_max_retries,
    }
    if settings.chat_model_base_url:
        common_options["base_url"] = settings.chat_model_base_url

    if settings.chat_model_provider == "deepseek":
        # Candidate extraction forces one schema tool. DeepSeek V4 enables thinking
        # by default, while its thinking mode rejects LangChain's named tool_choice.
        # Extraction does not need chain-of-thought, so keep it in non-thinking mode.
        return ChatDeepSeek(
            **common_options,
            extra_body={"thinking": {"type": "disabled"}},
        )
    if settings.chat_model_provider == "openai":
        return ChatOpenAI(**common_options)

    raise ConfigurationError(
        f"Unsupported chat model provider: {settings.chat_model_provider}"
    )
