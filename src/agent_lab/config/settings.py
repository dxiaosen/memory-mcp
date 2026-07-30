"""定义 MCP Memory Core 及可选模型适配器的环境变量配置。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_lab.observability.logging import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_FILE,
    DEFAULT_LOG_MAX_BYTES,
)

ChatModelProvider = Literal["deepseek", "openai"]


class LoggingSettings(BaseSettings):
    """所有可执行入口共享的日志配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file: Path = DEFAULT_LOG_FILE
    log_max_bytes: int = Field(default=DEFAULT_LOG_MAX_BYTES, ge=1024)
    log_backup_count: int = Field(default=DEFAULT_LOG_BACKUP_COUNT, ge=0, le=100)


class ChatModelSettings(LoggingSettings):
    """未来真实候选抽取 backend 可复用的聊天模型配置。"""

    chat_model_provider: ChatModelProvider = "deepseek"
    chat_model_name: str
    chat_model_api_key: SecretStr
    chat_model_base_url: str | None = None
    chat_model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    chat_model_timeout_seconds: float = Field(default=60.0, gt=0)
    chat_model_max_retries: int = Field(default=2, ge=0, le=10)


class MemorySettings(LoggingSettings):
    """通用记忆模块运行和迁移所需的配置。"""

    memory_database_path: Path = Path(".agent-lab/memory.db")


@lru_cache
def get_logging_settings() -> LoggingSettings:
    """返回当前进程内缓存的通用日志配置。"""

    return LoggingSettings()


@lru_cache
def get_chat_model_settings() -> ChatModelSettings:
    """返回真实结构化抽取 backend 使用的聊天模型配置。"""

    return ChatModelSettings()


@lru_cache
def get_memory_settings() -> MemorySettings:
    """返回当前进程内缓存的通用记忆配置。"""

    return MemorySettings()
