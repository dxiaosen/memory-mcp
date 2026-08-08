"""transcript 解析器从 Claude Code 会话记录还原文件/文档来源。

验证：
- 解析 JSONL 还原 Read 工具调用 + tool_result，产出 document 来源消息；
- 缺失/损坏 transcript 时 best-effort 返回空，不阻断 capture；
- 工具失败（is_error）的文件读取被跳过；
- 单条内容超长被截断。
"""

from __future__ import annotations

import json
from pathlib import Path

from memory_mcp_agent.transcript import extract_document_messages


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n",
        encoding="utf-8",
    )


def _assistant_with_read(call_id: str, file_path: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": "Read",
                    "input": {"file_path": file_path},
                }
            ]
        },
    }


def _user_with_tool_result(call_id: str, content: str, is_error: bool = False) -> dict:
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": content,
                    "is_error": is_error,
                }
            ]
        },
    }


def test_extracts_document_messages_from_read_tool_calls(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            _assistant_with_read("call-1", "/work/materials/04_纪要.md"),
            _user_with_tool_result("call-1", "收入同比增长 35%"),
        ],
    )

    messages = extract_document_messages(str(transcript))

    assert len(messages) == 1
    msg = messages[0]
    assert msg["role"] == "tool"
    assert msg["tool_name"] == "Read"
    assert msg["source_type"] == "document"
    assert msg["source_uri"] == "/work/materials/04_纪要.md"
    assert msg["source_title"] == "04_纪要.md"
    assert msg["content"] == "收入同比增长 35%"


def test_missing_transcript_returns_empty() -> None:
    assert extract_document_messages(None) == []
    assert extract_document_messages("") == []
    assert extract_document_messages("/nonexistent/path/transcript.jsonl") == []


def test_malformed_jsonl_returns_empty_best_effort(tmp_path: Path) -> None:
    transcript = tmp_path / "bad.jsonl"
    transcript.write_text("{not valid json\n", encoding="utf-8")

    assert extract_document_messages(str(transcript)) == []


def test_failed_read_is_error_is_skipped(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            _assistant_with_read("call-1", "/work/missing.md"),
            _user_with_tool_result("call-1", "File does not exist.", is_error=True),
            _assistant_with_read("call-2", "/work/present.md"),
            _user_with_tool_result("call-2", "有效内容"),
        ],
    )

    messages = extract_document_messages(str(transcript))

    assert len(messages) == 1
    assert messages[0]["source_uri"] == "/work/present.md"


def test_long_document_content_is_truncated(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    long_content = "A" * 20_000
    _write_jsonl(
        transcript,
        [
            _assistant_with_read("call-1", "/work/big.md"),
            _user_with_tool_result("call-1", long_content),
        ],
    )

    messages = extract_document_messages(str(transcript))

    assert len(messages) == 1
    assert len(messages[0]["content"]) <= 8000


def test_no_read_tool_calls_returns_empty(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "user", "message": {"content": "普通问题"}},
            {"type": "assistant", "message": {"content": "普通回复"}},
        ],
    )

    assert extract_document_messages(str(transcript)) == []
