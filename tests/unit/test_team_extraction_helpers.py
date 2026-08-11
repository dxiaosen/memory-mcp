"""团队提取簇内字段聚合纯函数的单元测试。

覆盖：
- subject/content 选择确定性（频次优先 + 字典序兜底，跨进程可复现）；
- 分歧摘要：主成员不误标为分歧、少数视角保留、无分歧时原样返回；
- 弱方向校验：对立 business_progress 检测、单侧/空时不拦截；
- 平均 embedding 计算。
"""

from __future__ import annotations

from memory_mcp.core.domain import (
    average_embedding,
    format_divergence_rationale,
    has_conflicting_business_progress,
    select_cluster_content,
    select_cluster_subject,
)


def _member(*, subject: str, content: str, owner: str) -> dict[str, str]:
    return {"subject": subject, "content": content, "owner_id": owner}


def test_subject_tie_break_is_deterministic_lexicographic() -> None:
    """两成员不同 subject 各一次，平局时字典序最小的胜出且可复现。"""

    cluster = [
        _member(subject="青禾 经营质量框架", content="A", owner="m1"),
        _member(subject="青禾 经营质量", content="B", owner="m2"),
    ]
    # 字典序："青禾 经营质量" < "青禾 经营质量框架"（前缀更短者小）。
    assert select_cluster_subject(cluster) == "青禾 经营质量"
    # 交换顺序不影响结果（确定性）。
    assert select_cluster_subject(list(reversed(cluster))) == "青禾 经营质量"


def test_subject_frequency_beats_lexicographic() -> None:
    """频次优先于字典序：高频 subject 即使字典序更大也胜出。"""

    cluster = [
        _member(subject="周报格式", content="x", owner="m1"),
        _member(subject="会议纪要", content="y", owner="m2"),
        _member(subject="会议纪要", content="z", owner="m3"),
    ]
    assert select_cluster_subject(cluster) == "会议纪要"


def test_content_frequency_then_length_then_lexicographic() -> None:
    """content 选择按频次 → 长度 → 字典序稳定排序。"""

    # 同频次时取最长。
    cluster = [
        _member(subject="s", content="短", owner="m1"),
        _member(subject="s", content="更长的内容", owner="m2"),
    ]
    assert select_cluster_content(cluster) == "更长的内容"
    # 频次优先于长度：高频短内容胜过低频长内容。
    cluster_freq = [
        _member(subject="s", content="短", owner="m1"),
        _member(subject="s", content="短", owner="m2"),
        _member(subject="s", content="一个很长的内容", owner="m3"),
    ]
    assert select_cluster_content(cluster_freq) == "短"


def test_divergence_rationale_preserves_minority_view() -> None:
    """主成员不误标为分歧；少数视角被引用。"""

    cluster = [
        _member(subject="偏好", content="用中文回答", owner="m1"),
        _member(subject="偏好", content="用中文回答", owner="m2"),
        _member(subject="偏好", content="优先按增长盈利库存三个维度", owner="m3"),
    ]
    rationale = format_divergence_rationale(
        cluster,
        base="团队共性提取：2 个成员写了相似内容",
        subject="偏好",
        content="用中文回答",
    )
    assert "分歧视角" in rationale
    assert "优先按增长盈利库存三个维度（m3）" in rationale
    # 主内容来源 m1 不被引用为分歧。
    assert "用中文回答（m1）" not in rationale


def test_divergence_rationale_empty_when_all_identical() -> None:
    """所有成员 content 相同时无分歧，原样返回 base。"""

    cluster = [
        _member(subject="周报格式", content="项目周报用表格", owner="m1"),
        _member(subject="周报格式", content="项目周报用表格", owner="m2"),
    ]
    rationale = format_divergence_rationale(
        cluster,
        base="base",
        subject="周报格式",
        content="项目周报用表格",
    )
    assert rationale == "base"


def test_divergence_rationale_dedups_identical_minority() -> None:
    """多个成员写了相同分歧措辞时摘要去重，只引用一次。"""

    cluster = [
        _member(subject="s", content="主内容", owner="m1"),
        _member(subject="s", content="少数视角", owner="m2"),
        _member(subject="s", content="少数视角", owner="m3"),
    ]
    rationale = format_divergence_rationale(
        cluster,
        base="base",
        subject="s",
        content="主内容",
    )
    assert rationale.count("少数视角") == 1


def test_average_embedding_means_componentwise() -> None:
    """平均 embedding 是逐分量算术平均。"""

    result = average_embedding([(1.0, 0.0, 0.0), (0.5, 0.5, 0.0)])
    assert result == (0.75, 0.25, 0.0)


def test_average_embedding_empty_returns_empty_tuple() -> None:
    """无有效 embedding 时返回空 tuple。"""

    assert average_embedding([]) == ()
    assert average_embedding([None, None]) == ()


def test_conflicting_business_progress_detected() -> None:
    """簇内同时出现 resolved 与 invalidatated 视为对立，应拦截。"""

    cluster = [
        {"owner_id": "m1", "business_progress": "resolved"},
        {"owner_id": "m2", "business_progress": "invalidated"},
    ]
    assert has_conflicting_business_progress(cluster) is True


def test_single_side_progress_not_conflicting() -> None:
    """只有单侧立场（或其它非对立值）时不拦截。"""

    assert (
        has_conflicting_business_progress(
            [{"business_progress": "resolved"}, {"business_progress": "monitoring"}]
        )
        is False
    )
    assert (
        has_conflicting_business_progress(
            [{"business_progress": "invalidated"}, {"business_progress": None}]
        )
        is False
    )


def test_empty_progress_not_conflicting() -> None:
    """business_progress 都为空（投研常态）时不拦截，弱校验放行。"""

    assert (
        has_conflicting_business_progress(
            [{"business_progress": None}, {"business_progress": None}]
        )
        is False
    )
    assert has_conflicting_business_progress([{}, {}]) is False
