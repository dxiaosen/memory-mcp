"""定义环境变量配置及其校验规则。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ChatModelProvider = Literal["deepseek", "openai"]
EmbeddingModelProvider = Literal["openai"]


class Settings(BaseSettings):
    """从环境变量或 ``.env`` 加载的应用配置。"""

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
    agent_recursion_limit: int = Field(default=12, ge=2, le=100)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def validate_chunking(self) -> Settings:
        """确保文档重叠长度小于分块长度。"""

        if self.document_chunk_overlap >= self.document_chunk_size:
            raise ValueError(
                "DOCUMENT_CHUNK_OVERLAP must be smaller than DOCUMENT_CHUNK_SIZE"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """返回当前进程内缓存的应用配置。"""

    return Settings()
