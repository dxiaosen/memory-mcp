"""通用记忆生命周期的确定性文本规则和历史视图。"""

import re
import unicodedata
from dataclasses import dataclass

from memory_mcp.core.domain.models import Evidence, MemoryRevision

_WHITESPACE = re.compile(r"\s+")


def normalize_memory_text(value: str) -> str:
    """生成仅用于等价判断的稳定文本，不改变持久化的原始表达。"""

    return _WHITESPACE.sub(
        " ",
        unicodedata.normalize("NFKC", value).casefold().strip(),
    )


@dataclass(frozen=True, slots=True)
class MemoryHistoryEntry:
    """一个不可变 revision 及其来源，供显式历史查询使用。"""

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
