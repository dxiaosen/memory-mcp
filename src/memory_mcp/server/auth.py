"""Prototype bearer authentication and trusted principal construction."""

from enum import StrEnum

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from pydantic import BaseModel, ConfigDict, Field

from memory_mcp.core import PrincipalContext
from memory_mcp.server.errors import (
    PermissionDeniedError,
    UnauthenticatedError,
)
from memory_mcp.server.settings import DemoPrincipalSettings


class MemoryScope(StrEnum):
    READ = "memory:read"
    WRITE = "memory:write"
    REVIEW = "memory:review"


class RequestPrincipal(BaseModel):
    """Trusted request identity kept separate from tool payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_key: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    agent_id: str | None = Field(default=None, min_length=1)
    scopes: frozenset[MemoryScope]

    def to_core(self) -> PrincipalContext:
        return PrincipalContext(owner_id=self.owner_key)


class DemoTokenVerifier(TokenVerifier):
    """Validate explicitly configured prototype tokens; never mint tokens."""

    def __init__(self, mappings: dict[str, DemoPrincipalSettings]) -> None:
        if not mappings:
            raise ValueError("at least one demo token mapping is required")
        self._mappings = dict(mappings)

    async def verify_token(self, token: str) -> AccessToken | None:
        configured = self._mappings.get(token)
        if configured is None:
            return None
        return AccessToken(
            token=token,
            client_id=configured.client_id,
            scopes=sorted(configured.scopes),
            subject=configured.subject_id,
            claims={
                "owner_key": configured.owner_key,
                "tenant_id": configured.tenant_id,
                "agent_id": configured.agent_id,
            },
        )


def current_request_principal() -> RequestPrincipal:
    """Build the current principal only from verified MCP auth context."""

    access_token = get_access_token()
    if access_token is None:
        raise UnauthenticatedError
    claims = access_token.claims or {}
    owner_key = claims.get("owner_key")
    tenant_id = claims.get("tenant_id")
    if not isinstance(owner_key, str) or not isinstance(tenant_id, str):
        raise UnauthenticatedError
    subject_id = access_token.subject
    if not isinstance(subject_id, str):
        raise UnauthenticatedError
    agent_id = claims.get("agent_id")
    if agent_id is not None and not isinstance(agent_id, str):
        raise UnauthenticatedError
    return RequestPrincipal(
        owner_key=owner_key,
        tenant_id=tenant_id,
        subject_id=subject_id,
        client_id=access_token.client_id,
        agent_id=agent_id,
        scopes=frozenset(MemoryScope(scope) for scope in access_token.scopes),
    )


def require_scope(
    principal: RequestPrincipal,
    required_scope: MemoryScope,
) -> None:
    if required_scope not in principal.scopes:
        raise PermissionDeniedError(required_scope.value)
