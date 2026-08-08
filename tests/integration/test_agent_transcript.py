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


def _user_prompt_entry(text: str) -> dict:
    """Claude Code 用户文本输入条目（非 tool_result）。"""

    return {"type": "user", "message": {"content": text}}


def _assistant_text_entry(text: str) -> dict:
    return {"type": "assistant", "message": {"content": text}}


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


def test_source_uri_converted_to_workspace_relative(tmp_path: Path) -> None:
    """cwd 提供时 source_uri 转为 workspace-relative，避免绑定绝对路径（§4）。"""

    materials = tmp_path / "materials"
    materials.mkdir()
    file_path = str(materials / "02_2025年报摘要.md")
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            _assistant_with_read("call-1", file_path),
            _user_with_tool_result("call-1", "收入同比增长 35%"),
        ],
    )

    messages = extract_document_messages(str(transcript), cwd=str(tmp_path))

    assert len(messages) == 1
    assert messages[0]["source_uri"] == "materials/02_2025年报摘要.md"
    assert messages[0]["source_title"] == "02_2025年报摘要.md"


def test_source_uri_keeps_absolute_when_no_cwd(tmp_path: Path) -> None:
    """无 cwd 时保留原始路径，不强行转换。"""

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
    assert messages[0]["source_uri"] == "/work/materials/04_纪要.md"


def test_current_turn_excludes_previous_turn_documents(tmp_path: Path) -> None:
    """第二轮只提取当前 prompt 之后的文档，不重复包含第一轮 tool message（§1）。

    transcript 含两轮：turn1 读 fileA，turn2 读 fileB。传 turn2 的 prompt 时应只返回
    fileB（不含 turn1 的 fileA）--证明 turn 边界由当前 prompt 定位，历史轮次文档被排除。
    """

    file_a = str(tmp_path / "01_概览.md")
    file_b = str(tmp_path / "02_年报.md")
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            _user_prompt_entry("请阅读第一份材料"),
            _assistant_with_read("call-1", file_a),
            _user_with_tool_result("call-1", "第一份材料内容"),
            _assistant_text_entry("第一轮回复"),
            _user_prompt_entry("请阅读第二份材料"),
            _assistant_with_read("call-2", file_b),
            _user_with_tool_result("call-2", "第二份材料内容"),
            _assistant_text_entry("第二轮回复"),
        ],
    )

    turn2 = extract_document_messages(
        str(transcript),
        user_prompt="请阅读第二份材料",
    )
    assert [m["source_uri"] for m in turn2] == [file_b]
    assert turn2[0]["content"] == "第二份材料内容"

    # turn1 独立 transcript（模拟 turn1 Stop 时只见本轮），返回 fileA。
    turn1_transcript = tmp_path / "turn1.jsonl"
    _write_jsonl(
        turn1_transcript,
        [
            _user_prompt_entry("请阅读第一份材料"),
            _assistant_with_read("call-1", file_a),
            _user_with_tool_result("call-1", "第一份材料内容"),
            _assistant_text_entry("第一轮回复"),
        ],
    )
    turn1 = extract_document_messages(
        str(turn1_transcript),
        user_prompt="请阅读第一份材料",
    )
    assert [m["source_uri"] for m in turn1] == [file_a]
    assert turn1[0]["content"] == "第一份材料内容"


def test_current_turn_without_read_calls_returns_empty(tmp_path: Path) -> None:
    """第二轮无工具调用时文档为空（等价 message_count=2，§1 验收）。"""

    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            _user_prompt_entry("请阅读第一份材料"),
            _assistant_with_read("call-1", str(tmp_path / "01_概览.md")),
            _user_with_tool_result("call-1", "第一份材料内容"),
            _assistant_text_entry("第一轮回复"),
            _user_prompt_entry("这是我的长期研究判断"),
            _assistant_text_entry("第二轮回复，无工具调用"),
        ],
    )

    messages = extract_document_messages(
        str(transcript),
        user_prompt="这是我的长期研究判断",
    )

    assert messages == []


def test_no_user_prompt_falls_back_to_last_user_text_turn(tmp_path: Path) -> None:
    """未传 user_prompt 时回退到最后一条用户文本消息之后的条目（不返回全部历史）。"""

    file_a = str(tmp_path / "01_概览.md")
    file_b = str(tmp_path / "02_年报.md")
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            _user_prompt_entry("请阅读第一份材料"),
            _assistant_with_read("call-1", file_a),
            _user_with_tool_result("call-1", "第一份材料内容"),
            _user_prompt_entry("请阅读第二份材料"),
            _assistant_with_read("call-2", file_b),
            _user_with_tool_result("call-2", "第二份材料内容"),
        ],
    )

    messages = extract_document_messages(str(transcript))

    assert [m["source_uri"] for m in messages] == [file_b]
