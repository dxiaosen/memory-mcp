"""解析 Claude Code 会话 transcript，提取当前轮次的 tool_use。

Claude Code 的 Stop / UserPromptSubmit Hook 通过 stdin 提供 ``transcript_path``，
指向一个 JSONL 会话记录文件。该模块只做无副作用的纯解析：从 transcript 中
还原当前轮次 assistant 块里调用的工具名集合，供 Host Adapter 判定
"查看/管理已存储记忆"的 inspect/manage turn——这类 turn 的 assistant 必然调用
至少一个 memory 管理工具（search_memories/list_memories/revoke_memory 等），
而业务 turn 的 assistant 只依赖 BeforeRun hook 自动调的 recall_memory。

本模块只依赖标准库与通用 dict 结构，不导入 Claude Code 私有格式，也不被
Server/Core 引用。
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from memory_mcp_agent.logging import log_event

_LOGGER = logging.getLogger(__name__)


class TranscriptParseError(ValueError):
    """transcript 路径不可读或结构非法。"""


def collect_turn_tool_uses(
    transcript_path: str | os.PathLike[str] | None,
    *,
    cwd: str | os.PathLike[str] | None = None,
    user_prompt: str | None = None,
) -> set[str]:
    """返回当前轮次 assistant 块里所有 tool_use 的工具名集合。

    用于在 AfterRun 识别"查看/管理已存储记忆"的 inspect/manage turn：这类
    turn 的 assistant 必然调用至少一个 memory 管理工具
    （search_memories/list_memories/revoke_memory 等），而业务 turn 的 assistant
    只依赖 BeforeRun hook 自动调的 recall_memory。

    解析失败、无 transcript 或无 tool_use 时返回空集合，调用方据此降级为
    "不跳过"（保持现有 capture 行为），不抛——best-effort，不阻断 capture 主流程。
    """

    if not transcript_path:
        return set()
    path = Path(transcript_path)
    try:
        entries = list(_iter_jsonl(path))
    except (OSError, ValueError) as exc:
        log_event(
            _LOGGER,
            logging.WARNING,
            "agent_hook.transcript.parse_failed",
            transcript_path=str(path),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return set()
    if not entries:
        return set()
    turn_entries = _slice_current_turn(entries, user_prompt)
    names: set[str] = set()
    for entry in turn_entries:
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return names


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TranscriptParseError(
                    f"invalid JSON at {path}:{line_number}"
                ) from exc
            if isinstance(entry, dict):
                yield entry


# 仅用于 turn 边界定位的空白归一化：\s+ 压成单空格 + trim，不改写字符内容。
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_ws(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def _user_text(entry: dict[str, Any]) -> str | None:
    """从 ``type=="user"`` 条目取用户文本输入；tool_result 条目返回 None。"""

    message = entry.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            # tool_result 块说明这条 user 条目是工具回执，不是用户 prompt 文本。
            if block.get("type") == "tool_result":
                return None
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    texts.append(text)
        if texts:
            return "\n".join(texts)
    return None


def _slice_current_turn(
    entries: Sequence[dict[str, Any]],
    user_prompt: str | None,
) -> list[dict[str, Any]]:
    """返回当前轮次（最近一次用户文本输入之后）的 transcript 条目。

    定位最近一条「用户文本消息」（非 tool_result）作为当前轮次边界，只保留其后的条目
    （含本轮 tool_use / tool_result / assistant 回复），避免把历史轮次重复纳入。
    ``user_prompt`` 提供时优先按内容匹配定位边界；找不到则回退到
    最近一条用户文本消息；都没有则返回全部条目（保持旧行为）。
    """

    user_text_indices = [
        index
        for index, entry in enumerate(entries)
        if entry.get("type") == "user" and _user_text(entry) is not None
    ]
    if not user_text_indices:
        return list(entries)
    boundary = user_text_indices[-1]
    if user_prompt:
        normalized_prompt = _normalize_ws(user_prompt)
        if normalized_prompt:
            for index in reversed(user_text_indices):
                if normalized_prompt in _normalize_ws(_user_text(entries[index]) or ""):
                    boundary = index
                    break
    return list(entries[boundary + 1 :])
