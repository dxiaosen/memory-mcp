"""BeforeRun Recall 查询确定性归一化的单元测试（recommend.md §5）。

验证 ``_normalize_recall_query``：
- 剔除**纯操作指令**子句与文件列表行，保留实体/主题/研究任务关键词（不过度裁剪）；
- 带实体的子句（如「请基于此前研究判断」）保留，不再因「请/列出」误删实体；
- 全部子句被剔除时返回空串（operational-only），由 recall 跳过 semantic recall；
- 不调用 LLM、不改 owner/profile/lifecycle 过滤。
"""

from __future__ import annotations

from memory_mcp.core.application.recall_service import _normalize_recall_query


def test_strips_pure_operational_clauses_and_file_list_keeps_entities() -> None:
    raw = (
        "我要研究启明先进材料的财报跟踪。\n"
        "不需要使用内部的skill或者其他工具\n"
        "请阅读以下文件：\n"
        "- materials/01_公司与业务概览.md\n"
        "- data/annual_financials.csv\n"
        "输出一份简短的公司跟踪框架，先不要替我形成最终投资结论。"
    )

    cleaned = _normalize_recall_query(raw)

    # 实体子句保留；纯操作指令子句与文件列表行被剔除。
    assert "启明先进材料" in cleaned
    assert "财报跟踪" in cleaned
    for dropped in (
        "不需要使用",
        "请阅读",
        "materials/01",
        "data/annual_financials",
        "输出一份",
    ):
        assert dropped not in cleaned


def test_case_b_natural_prompt_preserves_research_entities() -> None:
    """带实体的请求子句保留，不因「请基于/列出」误删研究判断等实体（§5 过度裁剪修复）。"""

    raw = "我要准备启明先进材料下一次财报跟踪，请基于此前研究判断列出最值得验证的问题。"

    cleaned = _normalize_recall_query(raw)

    # 实体/研究任务关键词保留。
    assert "启明先进材料" in cleaned
    assert "财报跟踪" in cleaned
    assert "研究判断" in cleaned


def test_pure_entity_query_unchanged() -> None:
    raw = "南美铜矿公司 矿山寿命"

    assert _normalize_recall_query(raw) == raw


def test_all_instruction_clauses_return_empty() -> None:
    """全部子句都是纯操作指令 -> 返回空串（operational-only），由 recall 跳过。"""

    assert _normalize_recall_query("请阅读以下文件\n不要使用内置工具\n按表格格式输出") == ""


def test_operational_only_single_clause_returns_empty() -> None:
    assert _normalize_recall_query("不要使用任何内置工具") == ""


def test_empty_or_whitespace_input_returns_empty() -> None:
    assert _normalize_recall_query("") == ""
    assert _normalize_recall_query("   ") == ""
