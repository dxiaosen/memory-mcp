"""候选抽取与敏感预检的框架无关端口。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agent_lab.memory.domain.capture import CandidateProposal


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    """只向模型适配器提供脱敏后的会话内容和场景约束。"""

    scenario: str
    conversation_id: str
    source_turn_id: str
    content: str
    observed_at: datetime
    allowed_memory_types: frozenset[str]
    capture_guidance: str
    policy_version: str


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
