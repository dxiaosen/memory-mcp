"""定义 Agent 层对外返回的数据结构。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceCitation:
    """当前轮回答引用的知识库来源。"""

    source: str
    page: int | None
    chunk_id: str | None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """单轮 Agent 调用汇总后的模型 Token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AgentResponse:
    """提供给应用层的单轮 Agent 响应。"""

    answer: str
    sources: tuple[SourceCitation, ...] = ()
    token_usage: TokenUsage = TokenUsage()
