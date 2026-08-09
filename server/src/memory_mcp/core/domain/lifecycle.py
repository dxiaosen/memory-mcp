"""记忆生命周期的确定性文本规则和历史视图。

提供文本归一化、分词契约与兜底分词器，以及历史版本查询结果。
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from memory_mcp.core.domain.models import Evidence, MemoryRevision

_WHITESPACE = re.compile(r"\s+")
# Python 的 ``\w`` 在 Unicode 模式下会把无空格分隔的 CJK 连续文本当作单个
# token，导致中文 word overlap 信号失效。这里按 ASCII 词边界与 CJK 单字切分，
# 作为没有外部分词器时的兜底；真正投研召回由 adapter 层注入 jieba 分词器。
_FALLBACK_WORD = re.compile(r"[A-Za-z0-9_]+|[一-鿿豈-鶴]", re.UNICODE)


class MemoryTokenizer(Protocol):
    """把文本切分为稳定 token 序列的纯函数契约。"""

    def tokenize(self, text: str) -> tuple[str, ...]:
        """返回归一化后的 token 序列，不改变原始文本。"""
        ...


@dataclass(frozen=True, slots=True)
class SimpleTokenizer:
    """不依赖外部库的兜底分词器，按 ASCII 词与 CJK 单字切分。"""

    def tokenize(self, text: str) -> tuple[str, ...]:
        normalized = normalize_memory_text(text)
        if not normalized:
            return ()
        return tuple(
            token.casefold() for token in _FALLBACK_WORD.findall(normalized) if token
        )


def normalize_memory_text(value: str) -> str:
    """生成仅用于等价判断的稳定文本，不改变持久化的原始表达。"""

    return _WHITESPACE.sub(
        " ",
        unicodedata.normalize("NFKC", value).casefold().strip(),
    )


def normalize_whitespace(value: str) -> str:
    """将任意 Unicode 空白（含 ``\\r\\n``/``\\n``/``\\t``）压成单空格并 trim。

    不做 NFKC/casefold，不改写字符--用于 source_expression 匹配，仅容忍空白差异。
    """

    return " ".join(value.split())


def normalize_compact(value: str) -> str:
    """移除全部 Unicode 空白，保留标点/数字/字符不改写。"""

    return "".join(value.split())


def source_expression_matches(source_expression: str, source: str) -> bool:
    """三级空白归一化 containment，用于 Candidate/Relation 的 source_expression 校验。

    依次尝试 raw -> ``normalize_whitespace`` -> ``normalize_compact`` containment；
    只忽略 Unicode 空白，不改标点/数字/字符。真实原文仅换行/空白差异 -> 匹配；
    模型改写、增标点、拼接独立 bullet（非空白字符保留） -> 不匹配。
    """

    if source_expression in source:
        return True
    if normalize_whitespace(source_expression) in normalize_whitespace(source):
        return True
    return normalize_compact(source_expression) in normalize_compact(source)


def tokenize_memory_text(
    value: str,
    tokenizer: MemoryTokenizer | None = None,
) -> tuple[str, ...]:
    """生成用于相关度计算的稳定 token 序列。"""

    resolved = tokenizer or SimpleTokenizer()
    return resolved.tokenize(value)


@dataclass(frozen=True, slots=True)
class MemoryHistoryEntry:
    """一个不可变历史版本及其来源证据，供显式历史查询使用。"""

    revision: MemoryRevision
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("history revision must contain source evidence")
        for source in self.evidence:
            if source.memory_id != self.revision.memory_id:
                raise ValueError("history evidence must belong to memory item")
            if source.revision_id != self.revision.revision_id:
                raise ValueError("history evidence must belong to revision")
            if source.owner_id != self.revision.owner_id:
                raise ValueError("history evidence owner must match revision owner")
