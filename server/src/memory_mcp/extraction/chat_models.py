"""创建 provider 专用的 LangChain 聊天模型。"""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from memory_mcp.exceptions import ConfigurationError
from memory_mcp.extraction.settings import ExtractionSettings


def create_chat_model(settings: ExtractionSettings) -> BaseChatModel:
    """根据配置创建 LangChain 聊天模型。"""

    common_options: dict[str, Any] = {
        "model": settings.require_model_name(),
        "api_key": settings.require_api_key(),
        "temperature": settings.temperature,
        "timeout": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }
    if settings.base_url:
        common_options["base_url"] = settings.base_url

    if settings.provider == "deepseek":
        # 候选抽取强制使用一个 schema tool。DeepSeek V4 默认启用 thinking，
        # 但 thinking 模式不接受 LangChain 的具名 tool_choice。
        # 抽取不需要思维链，因此固定关闭 thinking。
        return ChatDeepSeek(
            **common_options,
            extra_body={"thinking": {"type": "disabled"}},
        )
    if settings.provider == "openai":
        return ChatOpenAI(**common_options)

    raise ConfigurationError(f"Unsupported extraction provider: {settings.provider}")
