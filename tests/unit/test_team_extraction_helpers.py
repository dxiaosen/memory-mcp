"""团队提取簇内字段聚合纯函数的单元测试。

覆盖：
- subject/content 选择确定性（频次优先 + 字典序兜底，跨进程可复现）；
- 分歧摘要：主成员不误标为分歧、少数视角保留、无分歧时原样返回；
- 弱方向校验：对立 business_progress 检测、单侧/空时不拦截；
- 平均 embedding 计算；
- 全链接层次聚类（防传递漂移）+ 实体一致补聚（中间地带漏聚）。
"""

from __future__ import annotations

from memory_mcp.core.domain import (
    average_embedding,
    format_divergence_rationale,
    has_conflicting_business_progress,
    hierarchical_cluster_complete,
    merge_by_entity_overlap,
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


# ===== 全链接聚类 + 实体一致补聚 =====

# 两段语义正交的 embedding：传递漂移测试里 A-B、B-C 相似度达阈值但 A-C 不足，
# 验证全链接（complete linkage）不让 C 并入 A-B 簇，而单链贪心会把三者并一簇。
_EMB_X = (1.0, 0.0, 0.0)  # A
_EMB_X_DRIFT = (0.71, 0.71, 0.0)  # B：与 A 余弦约 0.71（距离 0.29 < 阈值 0.30）
_EMB_Y = (0.0, 1.0, 0.0)  # C：与 A 余弦 0.0，与 B 余弦约 0.71


def _vec_member(*, mid: str, emb: tuple[float, ...], subject: str) -> dict[str, object]:
    return {"memory_id": mid, "embedding": emb, "subject": subject}


def test_complete_linkage_prevents_chaining_drift() -> None:
    """全链接：A-B、B-C 相似达阈值但 A-C 不达，C 不并入 A-B 簇。

    单链贪心会因传递性把 A、B、C 并一簇（链式效应拉长簇）；全链接要求新点与
    簇内**所有**点距离都在阈值内，A-C 距离 1.0 > 阈值 0.30，C 进不来。
    """

    memories = [
        _vec_member(mid="A", emb=_EMB_X, subject="s1"),
        _vec_member(mid="B", emb=_EMB_X_DRIFT, subject="s1"),
        _vec_member(mid="C", emb=_EMB_Y, subject="s2"),
    ]
    clusters = hierarchical_cluster_complete(memories, similarity_threshold=0.70)
    cluster_ids = [{m["memory_id"] for m in c} for c in clusters]
    assert {frozenset(c) for c in cluster_ids} == {
        frozenset({"A", "B"}),
        frozenset({"C"}),
    }


def test_complete_linkage_merges_high_similarity() -> None:
    """三条两两相似度都达阈值时并成一簇。"""

    # 三条向量两两余弦都 ≥ 0.70
    emb_a = (1.0, 0.0, 0.0)
    emb_b = (0.85, 0.53, 0.0)  # cos(a,b)≈0.85
    emb_c = (0.85, 0.0, 0.53)  # cos(a,c)≈0.85, cos(b,c)≈0.72
    memories = [
        _vec_member(mid="A", emb=emb_a, subject="s"),
        _vec_member(mid="B", emb=emb_b, subject="s"),
        _vec_member(mid="C", emb=emb_c, subject="s"),
    ]
    clusters = hierarchical_cluster_complete(memories, similarity_threshold=0.70)
    assert len(clusters) == 1
    assert {m["memory_id"] for m in clusters[0]} == {"A", "B", "C"}


def test_complete_linkage_empty_and_single() -> None:
    """空输入返回空，单元素返回单簇。"""

    assert hierarchical_cluster_complete([], 0.70) == []
    single = [_vec_member(mid="A", emb=_EMB_X, subject="s")]
    result = hierarchical_cluster_complete(single, 0.70)
    assert len(result) == 1 and result[0][0]["memory_id"] == "A"


def test_complete_linkage_identical_vectors_one_cluster() -> None:
    """完全相同的向量并一簇（距离全零的兜底路径）。"""

    memories = [
        _vec_member(mid="A", emb=(1.0, 0.0), subject="s"),
        _vec_member(mid="B", emb=(1.0, 0.0), subject="s"),
    ]
    clusters = hierarchical_cluster_complete(memories, 0.70)
    assert len(clusters) == 1
    assert {m["memory_id"] for m in clusters[0]} == {"A", "B"}


def test_merge_by_entity_overlap_captures_middle_ground() -> None:
    """同标的措辞差、向量在 0.50~0.70 中间地带漏聚 → 实体补聚并入。

    两条记忆 subject 都含"泡泡玛特"实体，向量相似度 0.55（低于聚类阈值 0.70
    被全链接分到两簇，但 ≥ 实体补聚的向量底线 0.50）。补聚应把它们并回同簇。
    用 SimpleTokenizer 兜底（CJK 单字切分）——实体"泡泡玛特"四字全重合，
    Dice 系数足以过阈。
    """

    # cos(A,B) = 0.55
    emb_a = (1.0, 0.0)
    emb_b = (0.55, (1.0 - 0.55 * 0.55) ** 0.5)
    clusters = [
        [_vec_member(mid="A", emb=emb_a, subject="泡泡玛特海外增长")],
        [_vec_member(mid="B", emb=emb_b, subject="泡泡玛特出海持续性")],
    ]
    merged = merge_by_entity_overlap(clusters)
    assert len(merged) == 1
    assert {m["memory_id"] for m in merged[0]} == {"A", "B"}


def test_merge_by_entity_overlap_vector_floor_blocks_different_dimensions() -> None:
    """同标的不同维度、向量 <0.50 底线 → 不并，挡住维度信息丢失。

    "泡泡玛特Q3超预期"与"泡泡玛特毛利率"都含实体名（subject Dice 过阈），但
    向量正交（相似度 0.0 < 底线 0.50）——属同标的不同维度判断，不应并。
    """

    clusters = [
        [_vec_member(mid="C", emb=(1.0, 0.0, 0.0), subject="泡泡玛特Q3超预期")],
        [_vec_member(mid="D", emb=(0.0, 1.0, 0.0), subject="泡泡玛特毛利率")],
    ]
    merged = merge_by_entity_overlap(clusters)
    assert len(merged) == 2  # 不并，保持两簇


def test_merge_by_entity_overlap_different_entities_not_merged() -> None:
    """不同标的（subject 无实体重合）即使向量近也不通过实体补聚误并。

    不同标的的 Dice 系数 0 < 阈值 0.5，实体补聚不触发。
    """

    clusters = [
        [_vec_member(mid="E", emb=(1.0, 0.0), subject="贵州茅台高端酒")],
        [_vec_member(mid="F", emb=(0.0, 1.0), subject="宁德时代电池")],
    ]
    merged = merge_by_entity_overlap(clusters)
    assert len(merged) == 2


def test_merge_by_entity_overlap_empty_clusters() -> None:
    """空簇列表原样返回。"""

    assert merge_by_entity_overlap([]) == []


def test_clustering_deterministic_across_runs() -> None:
    """同一输入（固定顺序）多次聚类结果完全一致（跨进程可复现）。

    确定性承诺是"跨进程可复现"——调用方（PG 的 ORDER BY / in_memory 的 sort）
    已固定输入顺序，所以同一输入多次跑结果一致即可，不要求对输入顺序无关
    （等价最优划分在顺序变化时可能选不同解，这是层次聚类算法本质，非缺陷）。
    """

    memories = [
        _vec_member(mid="A", emb=_EMB_X, subject="s1"),
        _vec_member(mid="B", emb=_EMB_X_DRIFT, subject="s1"),
        _vec_member(mid="C", emb=_EMB_Y, subject="s2"),
        _vec_member(mid="D", emb=(0.0, 0.0, 1.0), subject="s3"),
    ]
    first = hierarchical_cluster_complete(memories, 0.70)
    second = hierarchical_cluster_complete(list(memories), 0.70)
    first_ids = [[m["memory_id"] for m in c] for c in first]
    second_ids = [[m["memory_id"] for m in c] for c in second]
    assert first_ids == second_ids
