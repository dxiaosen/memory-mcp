"""团队提取簇内字段聚合的确定性纯函数。

两个适配器（PostgreSQL / in_memory）的 ``extract_team_common_memories`` 共用本模块，
保证簇内 subject/content 选择的语义对齐且跨进程可复现。不导入适配器类型——入参是
适配器构建的 ``list[dict]`` 簇（键为字符串），用 ``Mapping[str, object]`` 表达以保持 Core 自包含。

设计取舍：提取阶段不做 LLM 合成，只做确定性选择 + 在 save_rationale 保留分歧摘要。
原文留在个人记忆里、分歧摘要在 rationale 给人审阅；人决定是否接受与改写。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from memory_mcp.core.domain.lifecycle import (
    MemoryTokenizer,
    tokenize_memory_text,
)

# 分歧摘要里每个成员 content 的引用长度上限。引用是原文前缀，不脱敏（开发阶段日志已放开
# 正文字段记录以便排障；上线前随整体脱敏集一并收紧）。
_DIVERGENCE_QUOTE_LIMIT = 40
# 簇内两条 content 经分词后 Jaccard 相似度低于该阈值视为语义分叉（保留分歧摘要）。
_CONTENT_DIVERGENCE_JACCARD = 0.6
# business_progress 的对立状态对：簇内同时出现这两值说明成员立场相反，不应并成团队共性候选。
_CONFLICTING_PROGRESS_PAIR: tuple[frozenset[str], frozenset[str]] = (
    frozenset({"resolved"}),
    frozenset({"invalidated"}),
)


def select_cluster_subject(cluster: Sequence[Mapping[str, object]]) -> str:
    """按 (频次 desc, subject 字典序 asc) 稳定排序后取首。

    平局时字典序最小的胜出，跨进程可复现，不依赖 set 哈希顺序。
    """

    subjects = [str(m["subject"]) for m in cluster]
    if not subjects:
        return ""
    counts: dict[str, int] = {}
    for subject in subjects:
        counts[subject] = counts.get(subject, 0) + 1
    return min(counts, key=lambda s: (-counts[s], s))


def select_cluster_content(cluster: Sequence[Mapping[str, object]]) -> str:
    """按 (频次 desc, 长度 desc, content 字典序 asc) 稳定排序后取首。

    频次优先于长度——"两个成员写了几乎一样的措辞"比"一个成员写了很长"更能代表共性；
    长度次之保留信息量；字典序末位兜底使结果可复现。
    """

    contents = [str(m["content"]) for m in cluster]
    if not contents:
        return ""
    counts: dict[str, int] = {}
    for content in contents:
        counts[content] = counts.get(content, 0) + 1
    return min(
        contents,
        key=lambda c: (-counts[c], -len(c), c),
    )


def format_divergence_rationale(
    cluster: Sequence[Mapping[str, object]],
    *,
    base: str,
    subject: str,
    content: str,
    tokenizer: MemoryTokenizer | None = None,
) -> str:
    """在 base rationale 后追加分歧成员的简短引用摘要。

    主成员 = 贡献了主 content 的成员（content 在簇内唯一，唯一标识来源）。其余成员
    若其 content 与主 content 的 Jaccard 相似度 < 阈值，视为少数视角并引用其 content
    前 40 字符。相似度高（仅措辞微调）不视为分歧。无分歧时原样返回 base。

    主 content 而非主 subject 作为来源标识，是因为 content 选择优先考虑信息量与共性，
    subject 可能因字典序兜底而来自另一成员；以 content 为锚能精确锁定主表达来源。
    """

    main_content_tokens = set(tokenize_memory_text(content, tokenizer))
    # 按 content 前 N 字符分组去重，同组多个成员合并 owner 标识（保序），避免
    # 多个成员写了相同分歧措辞时摘要重复且丢失共识信息。
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for member in cluster:
        member_content = str(member["content"])
        # 贡献了主 content 的成员就是主表达来源，不视为分歧。
        if member_content == content:
            continue
        member_tokens = set(tokenize_memory_text(member_content, tokenizer))
        similarity = _jaccard(main_content_tokens, member_tokens)
        if similarity < _CONTENT_DIVERGENCE_JACCARD:
            key = member_content[:_DIVERGENCE_QUOTE_LIMIT]
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(str(member.get("owner_id", "")))
    if not order:
        return base
    quotes = [f"{key}（{', '.join(grouped[key])}）" for key in order]
    return f"{base}；分歧视角：{' | '.join(quotes)}"


def has_conflicting_business_progress(
    cluster: Sequence[Mapping[str, object]],
) -> bool:
    """簇内是否同时存在对立的 business_progress（resolved vs invalidated）。

    这是弱方向校验：投研场景下 ``business_progress`` 多数为空（capture_guidance 要求
    仅显式标立场时才填），故只在成员显式标注了 resolved/invalidated 且互斥时拒绝并簇。
    都为空或只有单侧立场时不拦截——覆盖有限但零误判，不引入 LLM 判断。
    对立语义跨场景通用（状态机级对立，非投研专属），符合通用 Core 自包含约束。
    """

    present: set[str] = set()
    for member in cluster:
        value = member.get("business_progress")
        if value is not None:
            value = str(value).strip()
            if value:
                present.add(value)
    side_a, side_b = _CONFLICTING_PROGRESS_PAIR
    return bool(present & side_a) and bool(present & side_b)


def average_embedding(embeddings: Sequence[object]) -> tuple[float, ...]:
    """计算多个 embedding 的逐分量算术平均，用作 embedding 簇的代表向量（簇中心）。

    用簇中心而非 ``cluster[0]`` 的原始 embedding：代表性更强，且不随成员写新东西/
    排序变化而漂移，使候选级幂等的 embedding 比对稳定。入参是适配器已解析的 float
    序列（in_memory tuple / PG list）；不等长时按最短对齐，全为空时返回空 tuple。
    """

    vectors: list[list[float]] = []
    for value in embeddings:
        parsed = _coerce_floats(value)
        if parsed:
            vectors.append(parsed)
    if not vectors:
        return ()
    dimension = min(len(v) for v in vectors)
    if dimension == 0:
        return ()
    summed = [0.0] * dimension
    for vector in vectors:
        for index in range(dimension):
            summed[index] += vector[index]
    return tuple(value / len(vectors) for value in summed)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    if union == 0:
        return 0.0
    return intersection / union


def _coerce_floats(value: object) -> list[float]:
    """把适配器的 embedding 表示（tuple/list/str）统一为 float 列表。"""

    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [float(v) for v in value if _is_finite(v)]
    if isinstance(value, str):
        parts = value.strip("[]").split(",")
        return [float(p) for p in parts if p.strip() and _is_finite(p)]
    return []


def _is_finite(value: object) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True
