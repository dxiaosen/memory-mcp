"""Environment-backed configuration for the remote Memory MCP service."""

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_lab.observability.logging import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_MAX_BYTES,
)

MemoryScopeName = Literal["memory:read", "memory:write", "memory:review"]


class DemoPrincipalSettings(BaseModel):
    """One prototype token's trusted principal mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_key: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    tenant_id: str = Field(default="demo", min_length=1)
    agent_id: str | None = Field(default=None, min_length=1)
    scopes: frozenset[MemoryScopeName] = frozenset(
        {"memory:read", "memory:write", "memory:review"}
    )


class MemoryServerSettings(BaseSettings):
    """Settings required to build one single-process Memory MCP server."""

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    storage_backend: Literal["sqlite", "postgresql"] = "sqlite"
    database_path: Path = Path(".agent-lab/memory.db")
    database_url: SecretStr | None = None
    database_pool_min_size: int = Field(default=1, ge=1, le=50)
    database_pool_max_size: int = Field(default=5, ge=1, le=100)
    database_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    database_migrate_on_startup: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    mcp_path: str = "/mcp"
    health_path: str = "/health"
    stateless_http: bool = True
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_capture_characters: int = Field(default=100_000, ge=1_000, le=1_000_000)

    auth_issuer_url: AnyHttpUrl = AnyHttpUrl("http://localhost/demo-auth")
    resource_server_url: AnyHttpUrl | None = None
    demo_tokens_json: SecretStr = SecretStr("{}")

    scenario_id: str = Field(default="project-work", min_length=1)
    scenario_memory_types: frozenset[str] = frozenset(
        {"preference", "ongoing_item", "stable_context"}
    )
    scenario_business_progress_values: frozenset[str] = frozenset({"open", "done"})
    scenario_capture_guidance: str = Field(
        default="Capture explicit, durable project-work context.",
        min_length=1,
    )
    scenario_policy_version: str = Field(default="project-work-v1", min_length=1)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file: Path | None = Path(".agent-lab/logs/memory-mcp.log")
    log_max_bytes: int = Field(default=DEFAULT_LOG_MAX_BYTES, ge=1024)
    log_backup_count: int = Field(default=DEFAULT_LOG_BACKUP_COUNT, ge=0, le=100)

    def model_post_init(self, __context: object) -> None:
        for field_name in ("mcp_path", "health_path"):
            value = getattr(self, field_name)
            if not value.startswith("/") or (value != "/" and value.endswith("/")):
                raise ValueError(
                    f"{field_name} must start with '/' and have no trailing slash"
                )
        if self.mcp_path == self.health_path:
            raise ValueError("mcp_path and health_path must be different")
        if self.database_pool_max_size < self.database_pool_min_size:
            raise ValueError(
                "database_pool_max_size must be greater than or equal to "
                "database_pool_min_size"
            )
        if self.storage_backend == "postgresql" and self.database_url is None:
            raise ValueError(
                "MEMORY_MCP_DATABASE_URL is required for PostgreSQL storage"
            )

    def require_postgresql_url(self) -> str:
        """Return the PostgreSQL DSN only at the infrastructure boundary."""

        if self.storage_backend != "postgresql":
            raise ValueError(
                "MEMORY_MCP_STORAGE_BACKEND must be 'postgresql' for this command"
            )
        if self.database_url is None:
            raise ValueError("MEMORY_MCP_DATABASE_URL is required")
        value = self.database_url.get_secret_value().strip()
        if not value:
            raise ValueError("MEMORY_MCP_DATABASE_URL must not be empty")
        return value

    def demo_principals(self) -> dict[str, DemoPrincipalSettings]:
        """Parse the secret JSON token mapping without exposing it in settings repr."""

        raw = self.demo_tokens_json.get_secret_value()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("MEMORY_MCP_DEMO_TOKENS_JSON must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("MEMORY_MCP_DEMO_TOKENS_JSON must be a JSON object")
        principals: dict[str, DemoPrincipalSettings] = {}
        for token, value in payload.items():
            if (
                not isinstance(token, str)
                or not token.strip()
                or token != token.strip()
            ):
                raise ValueError("demo token keys must be non-empty strings")
            principals[token] = DemoPrincipalSettings.model_validate(value)
        _validate_principal_mapping(principals)
        return principals

    def require_demo_principals(self) -> dict[str, DemoPrincipalSettings]:
        """Return configured principals or fail closed during server startup."""

        principals = self.demo_principals()
        if not principals:
            raise ValueError(
                "At least one MEMORY_MCP_DEMO_TOKENS_JSON mapping is required"
            )
        return principals

    @classmethod
    def from_environment(cls) -> Self:
        return cls()


def _validate_principal_mapping(
    principals: dict[str, DemoPrincipalSettings],
) -> None:
    """Prevent configuration from aliasing distinct subjects into one owner."""

    owner_by_subject: dict[tuple[str, str], str] = {}
    subject_by_owner: dict[str, tuple[str, str]] = {}
    for principal in principals.values():
        subject = (principal.tenant_id, principal.subject_id)
        existing_owner = owner_by_subject.setdefault(subject, principal.owner_key)
        if existing_owner != principal.owner_key:
            raise ValueError(
                "one tenant/subject identity must map to exactly one owner_key"
            )
        existing_subject = subject_by_owner.setdefault(principal.owner_key, subject)
        if existing_subject != subject:
            raise ValueError(
                "one owner_key must not alias different tenant/subject identities"
            )
