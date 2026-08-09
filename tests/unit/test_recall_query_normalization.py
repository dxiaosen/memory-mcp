"""BeforeRun Recall 查询确定性归一化的单元测试（recommend.md §7）。

验证 ``_normalize_recall_query``：
- 剔除操作/工具/格式指令子句与文件列表行，保留实体/主题/研究任务关键词；
- 纯实体查询不变；
- 全部子句被剔除时回退原文；
- 不调用 LLM、不改 owner/profile/lifecycle 过滤。
"""

from __future__ import annotations

from memory_mcp.core.application.recall_service import _normalize_recall_query


def test_strips_instruction_clauses_and_file_list_keeps_entities() -> None:
    raw = (
        "我要研究启明先进材料的财报跟踪。\n"
        "不需要使用内部的skill或者其他工具\n"
        "请阅读以下文件：\n"
        "- materials/01_公司与业务概览.md\n"
        "- data/annual_financials.csv\n"
        "输出一份简短的公司跟踪框架，先不要替我形成最终投资结论。"
    )

    cleaned = _normalize_recall_query(raw)

    # 实体子句保留，指令子句与文件列表行被剔除。
    assert "启明先进材料" in cleaned
    assert "财报跟踪" in cleaned
    for dropped in (
        "不需要使用",
        "请阅读",
        "materials/01",
        "data/annual_financials",
        "输出一份",
        "不要替我形成",
    ):
        assert dropped not in cleaned


def test_case_b_natural_prompt_keeps_entity_clause_drops_request() -> None:
    raw = "我要准备启明先进材料下一次财报跟踪，请基于此前研究判断列出最值得验证的问题。"

    cleaned = _normalize_recall_query(raw)

    # 实体子句保留，请求子句（请基于...列出...）被剔除。
    assert "启明先进材料" in cleaned
    assert "财报跟踪" in cleaned
    assert "请基于" not in cleaned
    assert "列出" not in cleaned


def test_pure_entity_query_unchanged() -> None:
    raw = "南美铜矿公司 矿山寿命"

    assert _normalize_recall_query(raw) == raw


def test_all_instruction_clauses_fall_back_to_raw() -> None:
    raw = "请阅读以下文件\n不要使用内置工具\n按表格格式输出"

    # 全部子句都是指令 -> 回退原文，避免空查询。
    assert _normalize_recall_query(raw) == raw


def test_empty_input_returned_as_is() -> None:
    assert _normalize_recall_query("") == ""
    assert _normalize_recall_query("   ") == "   "
