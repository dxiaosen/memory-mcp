"""远程 Memory MCP 服务的环境配置。"""

import json
import re
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from memory_mcp.logging import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_MAX_BYTES,
)

MemoryScopeName = Literal["memory:read", "memory:write", "memory:review"]
MIN_STATIC_TOKEN_LENGTH = 32
# 身份分量字符集：首字符为字母或数字，其余允许 ``._-`` 但禁止冒号，
# 保证 derive_owner_key 用 ``:`` 拼接的 tenant:subject 不会被歧义解析。
IDENTITY_COMPONENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_IDENTITY_COMPONENT_RE = re.compile(IDENTITY_COMPONENT_PATTERN)


class ConfiguredPrincipal(BaseModel):
    """一条静态 Token 对应的可信主体配置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(min_length=1, pattern=IDENTITY_COMPONENT_PATTERN)
    tenant_id: str = Field(
        default="default",
        min_length=1,
        pattern=IDENTITY_COMPONENT_PATTERN,
    )
    scopes: frozenset[MemoryScopeName] = frozenset(
        {"memory:read", "memory:write", "memory:review"}
    )
    default_profile_id: str = Field(
        default="general-work",
        min_length=1,
        pattern=IDENTITY_COMPONENT_PATTERN,
    )
    team_ids: frozenset[str] = frozenset()

    @property
    def owner_key(self) -> str:
        """由可信租户和主体身份唯一派生存储隔离键。"""

        return derive_owner_key(self.tenant_id, self.subject_id)

    @property
    def team_owner_keys(self) -> tuple[str, ...]:
        """该主体所属团队的公共记忆 owner key 集合。"""

        return tuple(
            derive_team_owner_key(self.tenant_id, team_id)
            for team_id in sorted(self.team_ids)
        )


class MemoryServerSettings(BaseSettings):
    """构建单进程 Memory MCP 服务所需的配置。"""

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
    max_capture_characters: int = Field(default=100_000, ge=1_000, le=1_000_000)
    recall_max_items: int = Field(default=10, ge=1, le=10)
    recall_max_token_budget: int = Field(default=1_200, ge=64, le=8_000)
    recall_candidate_limit: int = Field(default=500, ge=1, le=10_000)
    maintenance_interval_seconds: int = Field(default=300, ge=0, le=86_400)
    capture_reprocess_interval_seconds: int = Field(default=5, ge=0, le=3600)
    capture_enqueue_enabled: bool = True
    team_extraction_interval_seconds: int = Field(default=3600, ge=0, le=86_400)
    team_extraction_similarity_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    team_extraction_min_cluster_size: int = Field(default=2, ge=2, le=100)

    auth_issuer_url: AnyHttpUrl = AnyHttpUrl("http://localhost/memory-mcp-auth")
    resource_server_url: AnyHttpUrl | None = None
    auth_tokens: SecretStr = SecretStr("{}")
    sensitive_rules: SecretStr | None = None

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file: Path | None = Path(".memory-mcp/logs/memory-mcp.log")
    log_max_bytes: int = Field(default=DEFAULT_LOG_MAX_BYTES, ge=1024)
    log_backup_count: int = Field(default=DEFAULT_LOG_BACKUP_COUNT, ge=0, le=100)
    log_content: bool = False

    @field_validator("log_file", mode="before")
    @classmethod
    def normalize_optional_log_file(cls, value: object) -> object:
        """将空日志文件配置解释为仅输出到控制台。"""

        if isinstance(value, str) and not value.strip():
            return None
        return value

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

    def require_postgresql_url(self) -> str:
        """只在基础设施边界返回 PostgreSQL DSN。"""

        if self.database_url is None:
            raise ValueError("MEMORY_MCP_DATABASE_URL is required")
        value = self.database_url.get_secret_value().strip()
        if not value:
            raise ValueError("MEMORY_MCP_DATABASE_URL must not be empty")
        return value

    def configured_principals(self) -> dict[str, ConfiguredPrincipal]:
        """解析静态 Token JSON，且不在配置 repr 中泄露内容。"""

        raw = self.auth_tokens.get_secret_value()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("MEMORY_MCP_AUTH_TOKENS must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("MEMORY_MCP_AUTH_TOKENS must be a JSON object")
        principals: dict[str, ConfiguredPrincipal] = {}
        for token, value in payload.items():
            if (
                not isinstance(token, str)
                or not token.strip()
                or token != token.strip()
            ):
                raise ValueError("configured token keys must be non-empty strings")
            if len(token) < MIN_STATIC_TOKEN_LENGTH:
                raise ValueError(
                    "configured tokens must contain at least "
                    f"{MIN_STATIC_TOKEN_LENGTH} characters"
                )
            principals[token] = ConfiguredPrincipal.model_validate(value)
        return principals

    def require_configured_principals(self) -> dict[str, ConfiguredPrincipal]:
        """返回已配置主体；没有主体时在启动阶段安全失败。"""

        principals = self.configured_principals()
        if not principals:
            raise ValueError("At least one MEMORY_MCP_AUTH_TOKENS mapping is required")
        return principals

    def configured_sensitive_rules(self) -> list[dict[str, str]] | None:
        """解析可选的敏感规则 JSON；未配置时返回 None 使用默认规则。

        JSON 是 ``[{"category": str, "pattern": str}, ...]`` 数组。
        规则按声明顺序应用；空数组视为"不覆盖默认"而非"清空规则"。
        """

        if self.sensitive_rules is None:
            return None
        raw = self.sensitive_rules.get_secret_value().strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("MEMORY_MCP_SENSITIVE_RULES must be valid JSON") from exc
        if not isinstance(payload, list):
            raise ValueError("MEMORY_MCP_SENSITIVE_RULES must be a JSON array")
        rules: list[dict[str, str]] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise ValueError(
                    f"MEMORY_MCP_SENSITIVE_RULES[{index}] must be a JSON object"
                )
            category = item.get("category")
            pattern = item.get("pattern")
            if not isinstance(category, str) or not category.strip():
                raise ValueError(
                    f"MEMORY_MCP_SENSITIVE_RULES[{index}].category must be a "
                    "non-empty string"
                )
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError(
                    f"MEMORY_MCP_SENSITIVE_RULES[{index}].pattern must be a "
                    "non-empty string"
                )
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"MEMORY_MCP_SENSITIVE_RULES[{index}].pattern is invalid: {exc}"
                ) from exc
            rules.append({"category": category, "pattern": pattern})
        return rules

    @classmethod
    def from_environment(cls) -> Self:
        return cls()


def derive_owner_key(tenant_id: str, subject_id: str) -> str:
    """用已校验的身份分量生成无歧义 owner key。"""

    if not _IDENTITY_COMPONENT_RE.fullmatch(tenant_id):
        raise ValueError("tenant_id has an invalid format")
    if not _IDENTITY_COMPONENT_RE.fullmatch(subject_id):
        raise ValueError("subject_id has an invalid format")
    return f"{tenant_id}:{subject_id}"


def derive_team_owner_key(tenant_id: str, team_id: str) -> str:
    """用已校验的租户和团队 ID 生成团队公共记忆 owner key。

    使用 ``team:`` 中缀确保与个人 owner key（``tenant_id:subject_id``）
    不冲突——个人 subject_id 受 ``IDENTITY_COMPONENT_PATTERN`` 约束
    不含冒号，而团队 key 的第二段以 ``team:`` 开头。
    """

    if not _IDENTITY_COMPONENT_RE.fullmatch(tenant_id):
        raise ValueError("tenant_id has an invalid format")
    if not _IDENTITY_COMPONENT_RE.fullmatch(team_id):
        raise ValueError("team_id has an invalid format")
    return f"{tenant_id}:team:{team_id}"
