"""解析 Claude Code 会话 transcript，提取文件/工具来源以补全 Evidence provenance。

Claude Code 的 Stop / UserPromptSubmit Hook 通过 stdin 提供 ``transcript_path``，
指向一个 JSONL 会话记录文件。该模块只做无副作用的纯解析：从 transcript 中
还原 ``Read`` 等文件读取工具调用及其结果，构造通用 ``RoleMessageV1`` 风格的
tool/document 消息，供 Host Adapter 纳入 ``capture_completed_turn`` 的 messages。

本模块只依赖标准库与通用 dict 结构，不导入 Claude Code 私有格式，也不被
Server/Core 引用——来源可信化只发生在 Host Adapter。
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

# 文件读取类工具名 → 从 input 取 file_path 的键。Read 是 Claude Code 内置工具，
# 其它宿主可能用不同名称；按需扩展。
_FILE_READ_TOOLS: dict[str, str] = {
    "Read": "file_path",
}

# 单条 tool/document 消息内容上限，避免把超大文件原文塞进 capture 请求。
_MAX_DOCUMENT_CONTENT_CHARS = 8000


class TranscriptParseError(ValueError):
    """transcript 路径不可读或结构非法。"""


def extract_document_messages(
    transcript_path: str | os.PathLike[str] | None,
    *,
    cwd: str | os.PathLike[str] | None = None,
    user_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """从 transcript 提取文件/文档来源消息，返回通用 RoleMessageV1 风格字典。

    返回值形如::

        {
            "role": "tool",
            "content": "<文件内容前若干字符>",
            "message_id": "<turn_id>:document:<index>",
            "tool_name": "Read",
            "source_type": "document",
            "source_uri": "<绝对路径>",
            "source_title": "<文件名>",
        }

    ``user_prompt`` 给出当前轮次的用户输入时，只提取**当前轮次**（最近一次该 prompt 之后）
    产生的 tool/document 消息，不把历史轮次的文档重复纳入。定位不到时
    回退到最近一条用户文本消息之后；仍无则返回全部（保持旧行为）。

    解析失败或无文件读取工具调用时返回空列表，不抛出——provenance 增强是
    best-effort，不能阻断 capture 主流程。
    """

    if not transcript_path:
        return []
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
        return []
    if not entries:
        return []
    turn_entries = _slice_current_turn(entries, user_prompt)
    read_calls = _collect_read_tool_uses(turn_entries)
    if not read_calls:
        return []
    results = _match_tool_results(turn_entries, read_calls)
    messages: list[dict[str, Any]] = []
    for index, (tool_name, file_path, content) in enumerate(results):
        if not content or not content.strip():
            continue
        messages.append(
            {
                "role": "tool",
                "content": content[:_MAX_DOCUMENT_CONTENT_CHARS],
                "tool_name": tool_name,
                "source_type": "document",
                "source_uri": _workspace_relative(file_path, cwd),
                "source_title": _file_name(file_path),
                "message_id": f"document:{index}",
            }
        )
    if messages:
        log_event(
            _LOGGER,
            logging.DEBUG,
            "agent_hook.transcript.document_messages_extracted",
            transcript_path=str(path),
            document_message_count=len(messages),
        )
    return messages


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
    （含本轮 tool_use / tool_result / assistant 回复），避免把历史轮次的文档重复纳入
    。``user_prompt`` 提供时优先按内容匹配定位边界；找不到则回退到
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


def _collect_read_tool_uses(
    entries: Sequence[dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    """收集文件读取类 tool_use 的 id → (tool_name, file_path)。"""

    calls: dict[str, tuple[str, str]] = {}
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_name = block.get("name")
            if tool_name not in _FILE_READ_TOOLS:
                continue
            call_id = block.get("id")
            input_payload = block.get("input")
            if not call_id or not isinstance(input_payload, dict):
                continue
            file_key = _FILE_READ_TOOLS[tool_name]
            file_path = input_payload.get(file_key)
            if isinstance(file_path, str) and file_path:
                calls[call_id] = (tool_name, file_path)
    return calls


def _match_tool_results(
    entries: Sequence[dict[str, Any]],
    read_calls: dict[str, tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """把 tool_result 块与文件读取调用配对，返回 (tool_name, file_path, content)。"""

    matched: list[tuple[str, str, str]] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if entry.get("type") != "user":
            continue
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call_id = block.get("tool_use_id")
            if call_id not in read_calls or call_id in seen_ids:
                continue
            seen_ids.add(call_id)
            tool_name, file_path = read_calls[call_id]
            result_content = block.get("content")
            text = _coerce_tool_result_text(result_content)
            if not text:
                continue
            if block.get("is_error"):
                # 工具调用失败（文件不存在等），仍记录文件路径但内容为空跳过。
                continue
            matched.append((tool_name, file_path, text))
    return matched


def _coerce_tool_result_text(value: Any) -> str:
    """tool_result.content 可能是 str 或 list[{type:text,text:...}]，归一为文本。"""

    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def _file_name(file_path: str) -> str:
    return Path(file_path).name or file_path


def _workspace_relative(
    file_path: str,
    cwd: str | os.PathLike[str] | None,
) -> str:
    """把绝对文件路径转为相对 cwd 的 workspace-relative URI。

    cwd 为空或无法计算相对路径（如跨盘符）时保留原路径，不强行截断。
    """

    if not cwd:
        return file_path
    try:
        rel = os.path.relpath(file_path, str(cwd))
    except (TypeError, ValueError):
        return file_path
    # relpath 在不同驱动器/根下可能返回绝对路径，保留结果。
    return rel.replace(os.sep, "/") if os.sep != "/" else rel
