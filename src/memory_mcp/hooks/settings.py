"""Environment-backed settings for a Memory Hook client."""

import re
from typing import Self

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROFILE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


class MemoryHookSettings(BaseSettings):
    """One independent Agent-to-Memory-MCP client configuration."""

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_HOOK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mcp_url: AnyHttpUrl
    bearer_token: SecretStr
    scenario: str = Field(default="general-work", min_length=1)
    timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    fail_open: bool = True
    recall_max_items: int = Field(default=5, ge=1, le=10)
    recall_token_budget: int = Field(default=600, ge=64, le=8_000)
    capture_max_attempts: int = Field(default=3, ge=1, le=10)
    capture_retry_delay_seconds: float = Field(default=0.1, ge=0, le=10)
    run_cache_max_entries: int = Field(default=1_000, ge=1, le=100_000)

    @classmethod
    def from_profile(cls, profile: str) -> Self:
        """Load an isolated profile such as MEMORY_AGENT_A_MCP_URL."""

        if not _PROFILE.fullmatch(profile):
            raise ValueError("profile must contain only letters, digits, '-' or '_'")
        prefix = f"MEMORY_{profile.replace('-', '_').upper()}_"
        return cls(_env_prefix=prefix)  # type: ignore[call-arg]

    def token_value(self) -> str:
        """Reveal the token only at the HTTP authorization boundary."""

        value = self.bearer_token.get_secret_value().strip()
        if not value:
            raise ValueError("bearer_token must not be empty")
        return value
