"""通用记忆关系领域模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from memory_mcp.core.domain.capture import ExpressionBasis


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class RelationStatus(StrEnum):
    """关系自身的治理状态；端点状态仍由 MemoryRevision 管理。"""

    ACTIVE = "active"
    STALE = "stale"
    REVOKED = "revoked"


class RelationOrigin(StrEnum):
    """关系由历史迁移、人工治理或自动抽取产生。"""

    LEGACY = "legacy"
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class RelationScope(StrEnum):
    """关系是跟随稳定记忆身份，还是只对指定 revision 成立。"""

    ITEM = "item"
    REVISION = "revision"


class RelationDirection(StrEnum):
    """以当前记忆为观察点时的关系方向。"""

    OUTGOING = "outgoing"
    INCOMING = "incoming"


@dataclass(frozen=True, slots=True)
class RelationEndpoint:
    """关系模型可引用的一项可信、无 owner 摘要。"""

    memory_id: UUID
    memory_type: str
    subject: str
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.memory_id, UUID):
            raise ValueError("memory_id must be a UUID")
        for field_name in ("memory_type", "subject", "content"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class RelationProposal:
    """模型输出的未受信关系建议；身份只能引用可信端点目录。"""

    source_memory_id: UUID
    target_memory_id: UUID
    relation_type: str
    source_expression: str
    confidence: float
    expression_basis: ExpressionBasis

    def __post_init__(self) -> None:
        if not isinstance(self.source_memory_id, UUID):
            raise ValueError("source_memory_id must be a UUID")
        if not isinstance(self.target_memory_id, UUID):
            raise ValueError("target_memory_id must be a UUID")
        if self.source_memory_id == self.target_memory_id:
            raise ValueError("relation proposal must not be a self loop")
        object.__setattr__(
            self,
            "relation_type",
            _required_text(self.relation_type, "relation_type"),
        )
        object.__setattr__(
            self,
            "source_expression",
            _required_text(self.source_expression, "source_expression"),
        )
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, int | float
        ):
            raise ValueError("confidence must be numeric")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not isinstance(self.expression_basis, ExpressionBasis):
            raise ValueError("expression_basis must be an ExpressionBasis")


@dataclass(frozen=True, slots=True)
class RelationProvenance:
    """自动关系的单份可信抽取证据，不包含 owner 或凭据。"""

    capture_id: UUID
    conversation_id: str
    source_turn_id: str
    source_expression: str
    confidence: float
    expression_basis: ExpressionBasis
    model_id: str
    prompt_version: str
    schema_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.capture_id, UUID):
            raise ValueError("capture_id must be a UUID")
        for field_name in (
            "conversation_id",
            "source_turn_id",
            "source_expression",
            "model_id",
            "prompt_version",
            "schema_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, int | float
        ):
            raise ValueError("confidence must be numeric")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.expression_basis is not ExpressionBasis.EXPLICIT:
            raise ValueError("automatic relation provenance must be explicit")


@dataclass(frozen=True, slots=True)
class MemoryRelation:
    """同 owner、同 Profile 的两个稳定 MemoryItem 之间的有向边。"""

    relation_id: UUID
    owner_id: str
    profile_id: str
    source_memory_id: UUID
    target_memory_id: UUID
    relation_type: str
    status: RelationStatus
    created_at: datetime
    origin: RelationOrigin = RelationOrigin.LEGACY
    scope: RelationScope = RelationScope.ITEM
    source_revision_id: UUID | None = None
    target_revision_id: UUID | None = None
    provenance: RelationProvenance | None = None
    revoked_at: datetime | None = None
    stale_at: datetime | None = None
    stale_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _required_text(self.owner_id, "owner_id"))
        object.__setattr__(
            self,
            "profile_id",
            _required_text(self.profile_id, "profile_id"),
        )
        object.__setattr__(
            self,
            "relation_type",
            _required_text(self.relation_type, "relation_type"),
        )
        if self.source_memory_id == self.target_memory_id:
            raise ValueError("memory relation must not be a self loop")
        if not isinstance(self.status, RelationStatus):
            raise ValueError("status must be a RelationStatus")
        if not isinstance(self.origin, RelationOrigin):
            raise ValueError("origin must be a RelationOrigin")
        if not isinstance(self.scope, RelationScope):
            raise ValueError("scope must be a RelationScope")
        _aware(self.created_at, "created_at")
        if (self.source_revision_id is None) is not (self.target_revision_id is None):
            raise ValueError("relation revision snapshots must be provided together")
        if self.source_revision_id is not None and (
            not isinstance(self.source_revision_id, UUID)
            or not isinstance(self.target_revision_id, UUID)
        ):
            raise ValueError("relation revision snapshots must be UUID values")
        if self.origin is RelationOrigin.LEGACY:
            if (
                self.scope is not RelationScope.ITEM
                or self.source_revision_id is not None
                or self.provenance is not None
            ):
                raise ValueError(
                    "legacy relation must be item scoped without snapshots or provenance"
                )
        elif self.origin is RelationOrigin.MANUAL:
            if (
                self.scope is not RelationScope.ITEM
                or self.provenance is not None
                or self.source_revision_id is None
            ):
                raise ValueError(
                    "manual relation requires item scope and revision snapshots"
                )
        elif (
            self.scope is not RelationScope.REVISION
            or self.source_revision_id is None
            or self.provenance is None
        ):
            raise ValueError(
                "automatic relation requires revision scope, snapshots and provenance"
            )
        if self.provenance is not None and not isinstance(
            self.provenance, RelationProvenance
        ):
            raise ValueError("provenance must be RelationProvenance")
        if self.stale_reason is not None:
            object.__setattr__(
                self,
                "stale_reason",
                _required_text(self.stale_reason, "stale_reason"),
            )
        if (self.stale_at is None) is not (self.stale_reason is None):
            raise ValueError("stale time and reason must be provided together")
        if self.status is RelationStatus.ACTIVE:
            if any(
                value is not None
                for value in (self.revoked_at, self.stale_at, self.stale_reason)
            ):
                raise ValueError("active relation cannot have terminal metadata")
        elif self.status is RelationStatus.STALE:
            if (
                self.revoked_at is not None
                or self.stale_at is None
                or self.stale_reason is None
            ):
                raise ValueError(
                    "stale relation requires time and reason without revocation"
                )
        elif self.revoked_at is None:
            raise ValueError("revoked relation requires revoked_at")
        if self.revoked_at is not None:
            _aware(self.revoked_at, "revoked_at")
            if self.revoked_at < self.created_at:
                raise ValueError("revoked_at must not precede created_at")
        if self.stale_at is not None:
            _aware(self.stale_at, "stale_at")
            if self.stale_at < self.created_at:
                raise ValueError("stale_at must not precede created_at")


@dataclass(frozen=True, slots=True)
class MemoryRelationSummary:
    """面向详情和召回的一跳关系，不复制另一端正文。"""

    relation: MemoryRelation
    direction: RelationDirection
    related_memory_id: UUID
    related_subject: str
    related_memory_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.direction, RelationDirection):
            raise ValueError("direction must be a RelationDirection")
        object.__setattr__(
            self,
            "related_subject",
            _required_text(self.related_subject, "related_subject"),
        )
        object.__setattr__(
            self,
            "related_memory_type",
            _required_text(self.related_memory_type, "related_memory_type"),
        )
        expected = (
            self.relation.target_memory_id
            if self.direction is RelationDirection.OUTGOING
            else self.relation.source_memory_id
        )
        if self.related_memory_id != expected:
            raise ValueError("related memory must match relation direction")
