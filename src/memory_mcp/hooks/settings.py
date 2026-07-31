"""Agent Host 主动记忆客户端的环境配置。"""

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MemoryHookSettings(BaseSettings):
    """单个 Agent 进程独立持有的 Memory MCP 客户端配置。"""

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_HOOK_",
        extra="ignore",
        populate_by_name=True,
    )

    mcp_url: AnyHttpUrl = Field(
        validation_alias=AliasChoices(
            "MEMORY_MCP_URL",
            "MEMORY_HOOK_MCP_URL",
        )
    )
    bearer_token: SecretStr = Field(
        validation_alias=AliasChoices(
            "MEMORY_MCP_TOKEN",
            "MEMORY_HOOK_BEARER_TOKEN",
        )
    )
    scenario: str = Field(default="general-work", min_length=1)
    timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    fail_open: bool = True
    recall_max_items: int = Field(default=5, ge=1, le=10)
    recall_token_budget: int = Field(default=600, ge=64, le=8_000)
    capture_max_attempts: int = Field(default=3, ge=1, le=10)
    capture_retry_delay_seconds: float = Field(default=0.1, ge=0, le=10)
    run_cache_max_entries: int = Field(default=1_000, ge=1, le=100_000)

    def token_value(self) -> str:
        """只在 HTTP Authorization 边界解封 Token。"""

        value = self.bearer_token.get_secret_value().strip()
        if not value:
            raise ValueError("bearer_token must not be empty")
        return value
