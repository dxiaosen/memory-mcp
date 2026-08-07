"""静态 Bearer 认证与可信请求主体构造。"""

import logging
from enum import StrEnum
from hashlib import sha256

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from pydantic import BaseModel, ConfigDict, Field

from memory_mcp.core import PrincipalContext
from memory_mcp.errors import (
    PermissionDeniedError,
    UnauthenticatedError,
)
from memory_mcp.logging import log_event
from memory_mcp.settings import (
    ConfiguredPrincipal,
    derive_owner_key,
    derive_team_owner_key,
)

_LOGGER = logging.getLogger(__name__)


class MemoryScope(StrEnum):
    READ = "memory:read"
    WRITE = "memory:write"
    REVIEW = "memory:review"


class RequestPrincipal(BaseModel):
    """与工具请求正文隔离的可信请求身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_key: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    default_profile_id: str = Field(min_length=1)
    scopes: frozenset[MemoryScope]
    # 由 tenant_id + team_id 派生的团队公共记忆 owner key 集合，与个人
    # owner_key（tenant_id:subject_id）命名空间分离，授权后可读写团队记忆。
    team_owner_ids: frozenset[str] = frozenset()

    def to_core(self) -> PrincipalContext:
        return PrincipalContext(
            owner_id=self.owner_key,
            team_owner_ids=tuple(self.team_owner_ids),
        )


class StaticTokenVerifier(TokenVerifier):
    """校验显式配置的 Bearer Token，不签发新 Token。"""

    def __init__(self, mappings: dict[str, ConfiguredPrincipal]) -> None:
        if not mappings:
            raise ValueError("at least one configured token mapping is required")
        self._mappings = dict(mappings)

    async def verify_token(self, token: str) -> AccessToken | None:
        """匹配已配置的静态 Token，未匹配时返回 None 表示拒绝。"""

        configured = self._mappings.get(token)
        if configured is None:
            return None
        return AccessToken(
            token=token,
            client_id=_static_client_id(token),
            scopes=sorted(configured.scopes),
            subject=configured.subject_id,
            claims={
                "tenant_id": configured.tenant_id,
                "default_profile_id": configured.default_profile_id,
                "team_ids": sorted(configured.team_ids),
            },
        )


def current_request_principal() -> RequestPrincipal:
    """只根据已验证的 MCP 认证上下文构造当前主体。"""

    access_token = get_access_token()
    if access_token is None:
        raise UnauthenticatedError
    claims = access_token.claims or {}
    tenant_id = claims.get("tenant_id")
    default_profile_id = claims.get("default_profile_id")
    if not isinstance(tenant_id, str):
        raise UnauthenticatedError
    if not isinstance(default_profile_id, str):
        raise UnauthenticatedError
    subject_id = access_token.subject
    if not isinstance(subject_id, str):
        raise UnauthenticatedError
    try:
        owner_key = derive_owner_key(tenant_id, subject_id)
    except ValueError as exc:
        log_event(
            _LOGGER,
            logging.WARNING,
            "memory.auth.owner_key_derivation_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise UnauthenticatedError from None
    raw_team_ids = claims.get("team_ids", ())
    if not isinstance(raw_team_ids, (list, tuple)):
        raise UnauthenticatedError
    team_owner_ids: set[str] = set()
    for team_id in raw_team_ids:
        if not isinstance(team_id, str) or not team_id.strip():
            raise UnauthenticatedError
        try:
            team_owner_ids.add(derive_team_owner_key(tenant_id, team_id))
        except ValueError as exc:
            log_event(
                _LOGGER,
                logging.WARNING,
                "memory.auth.team_owner_key_derivation_failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise UnauthenticatedError from None
    return RequestPrincipal(
        owner_key=owner_key,
        tenant_id=tenant_id,
        subject_id=subject_id,
        client_id=access_token.client_id,
        default_profile_id=default_profile_id,
        scopes=frozenset(MemoryScope(scope) for scope in access_token.scopes),
        team_owner_ids=frozenset(team_owner_ids),
    )


def require_scope(
    principal: RequestPrincipal,
    required_scope: MemoryScope,
) -> None:
    """主体缺少所需 scope 时抛出权限拒绝错误。"""

    if required_scope not in principal.scopes:
        raise PermissionDeniedError(required_scope.value)


def _static_client_id(token: str) -> str:
    """生成只用于审计的稳定凭据引用，不暴露原始 Token。"""

    digest = sha256(token.encode("utf-8")).hexdigest()[:24]
    return f"static-{digest}"
