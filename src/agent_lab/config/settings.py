"""定义按运行入口拆分的环境变量配置及其校验规则。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_lab.observability.logging import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_FILE,
    DEFAULT_LOG_MAX_BYTES,
)

ChatModelProvider = Literal["deepseek", "openai"]
EmbeddingModelProvider = Literal["openai"]


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


class KnowledgeSettings(LoggingSettings):
    """离线索引和在线检索共享的知识库配置。"""

    embedding_model_provider: EmbeddingModelProvider = "openai"
    embedding_model_name: str
    embedding_model_api_key: SecretStr
    embedding_model_base_url: str | None = None
    embedding_model_timeout_seconds: float = Field(default=60.0, gt=0)
    embedding_model_max_retries: int = Field(default=2, ge=0, le=10)

    vector_store_persist_directory: Path = Path(".agent-lab/chroma")
    vector_store_collection_name: str = Field(
        default="agent-lab-knowledge",
        min_length=3,
        max_length=63,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    document_chunk_size: int = Field(default=800, ge=100, le=10_000)
    document_chunk_overlap: int = Field(default=120, ge=0)
    retrieval_top_k: int = Field(default=4, ge=1, le=20)

    @model_validator(mode="after")
    def validate_chunking(self) -> Self:
        """确保文档重叠长度小于分块长度。"""

        if self.document_chunk_overlap >= self.document_chunk_size:
            raise ValueError(
                "DOCUMENT_CHUNK_OVERLAP must be smaller than DOCUMENT_CHUNK_SIZE"
            )
        return self


class AgentSettings(KnowledgeSettings):
    """在线 Agent 所需的聊天模型、知识库和日志配置。"""

    chat_model_provider: ChatModelProvider = "deepseek"
    chat_model_name: str
    chat_model_api_key: SecretStr
    chat_model_base_url: str | None = None
    chat_model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    chat_model_timeout_seconds: float = Field(default=60.0, gt=0)
    chat_model_max_retries: int = Field(default=2, ge=0, le=10)
    agent_recursion_limit: int = Field(default=12, ge=2, le=100)


class MemorySettings(LoggingSettings):
    """通用记忆模块运行和迁移所需的配置。"""

    memory_database_path: Path = Path(".agent-lab/memory.db")


# 保留原有名称，避免现有 Agent 调用方在结构调整中被迫同步修改。
Settings = AgentSettings


@lru_cache
def get_logging_settings() -> LoggingSettings:
    """返回当前进程内缓存的通用日志配置。"""

    return LoggingSettings()


@lru_cache
def get_knowledge_settings() -> KnowledgeSettings:
    """返回当前进程内缓存的知识库配置。"""

    return KnowledgeSettings()


@lru_cache
def get_settings() -> AgentSettings:
    """返回当前进程内缓存的完整 Agent 配置。"""

    return AgentSettings()


@lru_cache
def get_memory_settings() -> MemorySettings:
    """返回当前进程内缓存的通用记忆配置。"""

    return MemorySettings()
