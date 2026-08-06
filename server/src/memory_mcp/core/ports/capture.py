"""候选/关系抽取与敏感预检的框架无关端口。"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from memory_mcp.core.domain.capture import CandidateProposal
from memory_mcp.core.domain.relations import RelationEndpoint, RelationProposal
from memory_mcp.core.ports.profiles import MemoryRelationPolicy

# 单次关系抽取允许传入的端点上限，防止模型上下文膨胀。
MAX_RELATION_ENDPOINTS = 40
# 单次关系抽取返回的关系建议上限，有界输出便于预算控制。
MAX_RELATION_PROPOSALS = 20


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    """只向模型适配器提供脱敏后的会话内容和记忆配置约束。"""

    profile_id: str
    conversation_id: str
    source_turn_id: str
    content: str
    observed_at: datetime
    allowed_memory_types: frozenset[str]
    capture_guidance: str
    profile_version: str
    # 该 Profile 允许的业务进展值；空集合表示该 Profile 不使用 business_progress，
    # 模型必须留空该字段。透传给模型 prompt，避免其凭空编造不在白名单内的值。
    business_progress_values: frozenset[str] = frozenset()
    subject_hint: str | None = None


class CandidateExtractor(Protocol):
    """把脱敏后的 turn 转换为结构化原子候选建议。"""

    @property
    def model_id(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    @property
    def schema_version(self) -> str: ...

    def extract(self, request: ExtractionRequest) -> tuple[CandidateProposal, ...]:
        """执行一次同步结构化抽取。"""

        ...


@dataclass(frozen=True, slots=True)
class RelationExtractionRequest:
    """只向关系模型暴露脱敏轮次和有界可信端点。"""

    profile_id: str
    content: str
    observed_at: datetime
    profile_version: str
    relation_policies: Mapping[str, MemoryRelationPolicy]
    endpoints: tuple[RelationEndpoint, ...]
    subject_hint: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("profile_id", "content", "profile_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty text")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not self.relation_policies:
            raise ValueError("relation_policies must not be empty")
        if len(self.endpoints) > MAX_RELATION_ENDPOINTS:
            raise ValueError("relation endpoint limit exceeded")
        if len({endpoint.memory_id for endpoint in self.endpoints}) != len(
            self.endpoints
        ):
            raise ValueError("relation endpoints must have unique memory ids")
        if self.subject_hint is not None and (
            not isinstance(self.subject_hint, str) or not self.subject_hint.strip()
        ):
            raise ValueError("subject_hint must be non-empty text when supplied")


class RelationExtractor(Protocol):
    """把脱敏轮次和可信端点转换为结构化关系建议。"""

    @property
    def model_id(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    @property
    def schema_version(self) -> str: ...

    def extract(
        self,
        request: RelationExtractionRequest,
    ) -> tuple[RelationProposal, ...]:
        """执行一次同步结构化关系抽取。"""

        ...


@dataclass(frozen=True, slots=True)
class SensitiveInspection:
    """敏感检测结果；categories 不包含被拦截正文。"""

    redacted_text: str
    categories: tuple[str, ...]

    @property
    def was_redacted(self) -> bool:
        return bool(self.categories)


class SensitiveContentGuard(Protocol):
    """在模型和持久化边界前检测并脱敏禁止内容。"""

    def inspect(self, text: str) -> SensitiveInspection:
        """返回脱敏文本和不含正文的命中类别。"""

        ...
