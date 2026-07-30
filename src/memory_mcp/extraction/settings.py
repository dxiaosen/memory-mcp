"""Environment-backed settings for model-assisted candidate extraction."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ChatModelProvider = Literal["deepseek", "openai"]


class ChatModelSettings(BaseSettings):
    """Configuration shared by supported LangChain chat-model providers."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    chat_model_provider: ChatModelProvider = "deepseek"
    chat_model_name: str
    chat_model_api_key: SecretStr
    chat_model_base_url: str | None = None
    chat_model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    chat_model_timeout_seconds: float = Field(default=60.0, gt=0)
    chat_model_max_retries: int = Field(default=2, ge=0, le=10)
