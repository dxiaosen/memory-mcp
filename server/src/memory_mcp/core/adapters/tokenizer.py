"""基于 jieba 的中文分词 adapter，供召回相关度计算注入。"""

from __future__ import annotations

import re

import jieba

from memory_mcp.core.domain import SimpleTokenizer, normalize_memory_text

# ASCII 词边界正则，用于把英文/数字部分按词切分后，再把非 ASCII 段交给 jieba。
_ASCII_WORD = re.compile(r"[A-Za-z0-9_]+")
# 至少含一个字母或数字的 token 才视为有效；纯标点（如 "-"）不应参与 word overlap。
_MEANINGFUL_TOKEN = re.compile(r"[A-Za-z0-9]|[一-鿿]")


class JiebaTokenizer:
    """精确模式中文分词，保证相同输入产出相同 token 序列。

    关闭 HMM 新词发现，使分词结果只依赖词典，从而满足离线评测的
    确定性可复现要求。混合中英文先按 ASCII 词边界预切，再对每段
    非 ASCII 部分用 jieba 精确模式分词；纯标点 token 会被丢弃，
    避免 ``alpha-research`` 这类连字符文本产生虚假的 word overlap。
    """

    def __init__(self) -> None:
        # 关闭 HMM，避免隐式新词发现引入非确定性。
        jieba.initialize()

    def tokenize(self, text: str) -> tuple[str, ...]:
        normalized = normalize_memory_text(text)
        if not normalized:
            return ()
        tokens: list[str] = []
        # 用 ASCII 词边界把英文/数字段和中文段分开：ASCII 段直接作为 token，
        # 中文段（夹在 ASCII 词之间或首尾）交给 jieba 精确模式分词。
        last_end = 0
        for match in _ASCII_WORD.finditer(normalized):
            if match.start() > last_end:
                cjk_segment = normalized[last_end : match.start()]
                tokens.extend(
                    word.casefold()
                    for word in jieba.lcut(cjk_segment, cut_all=False, HMM=False)
                    if word.strip()
                )
            tokens.append(match.group().casefold())
            last_end = match.end()
        if last_end < len(normalized):
            tail = normalized[last_end:]
            tokens.extend(
                word.casefold()
                for word in jieba.lcut(tail, cut_all=False, HMM=False)
                if word.strip()
            )
        return tuple(token for token in tokens if _MEANINGFUL_TOKEN.search(token))


__all__ = ["JiebaTokenizer", "SimpleTokenizer"]
