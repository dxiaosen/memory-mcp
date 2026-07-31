"""候选抽取的环境配置。"""

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ExtractionProvider = Literal["deepseek", "openai"]


class ExtractionSettings(BaseSettings):
    """由服务进程持有的真实模型抽取配置。"""

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_MCP_MODEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    provider: ExtractionProvider = "deepseek"
    model_name: str | None = Field(
        default=None,
        validation_alias="MEMORY_MCP_MODEL_NAME",
    )
    api_key: SecretStr | None = None
    base_url: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    max_retries: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def validate_model_credentials(self) -> ExtractionSettings:
        """真实模型配置不完整时在启动阶段失败。"""

        if self.model_name is None or not self.model_name.strip():
            raise ValueError("MEMORY_MCP_MODEL_NAME is required")
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            raise ValueError("MEMORY_MCP_MODEL_API_KEY is required")
        return self

    def require_model_name(self) -> str:
        """返回已校验的模型标识。"""

        if self.model_name is None:
            raise ValueError("MEMORY_MCP_MODEL_NAME is required")
        return self.model_name.strip()

    def require_api_key(self) -> SecretStr:
        """返回已校验的模型凭据且不暴露其内容。"""

        if self.api_key is None:
            raise ValueError("MEMORY_MCP_MODEL_API_KEY is required")
        return self.api_key
