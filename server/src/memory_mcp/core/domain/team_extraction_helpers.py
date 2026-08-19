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
# 实体一致补聚的 subject token Dice 阈值。subject 是精确的项目名/标的
# （design.md §9.4：模型把 subject 从 hint 归纳为项目名），token 重合一半以上
# 视为同标的。用 Dice（2*交集/两者之和）而非 Jaccard：subject 是短文本，Jaccard
# 对短文本偏严（交集小被并集放大惩罚），Dice 更温和，让同标的措辞差异过阈。
# 区分"同标的 vs 不同标的"靠 Dice；区分"同标的 vs 同标的不同维度"靠向量底线。
DEFAULT_ENTITY_OVERLAP_THRESHOLD = 0.5
# 实体一致补聚叠加的向量相似度底线。单凭 subject 重合不并簇（防同标的不同
# 维度判断如"Q3 超预期"与"毛利率"被硬并丢失维度信息），需同时向量相似度
# ≥ 此底线。比聚类阈值 0.70 宽，只补措辞差、向量在 0.50~0.70 中间地带的漏聚。
DEFAULT_ENTITY_MERGE_VECTOR_FLOOR = 0.50


def _embedding_cosine(a: object, b: object) -> float:
    """两个 embedding 的余弦相似度，纯 Python 实现（适配器两侧共用）。

    入参是适配器已解析的 embedding（in_memory tuple / PG list 或 str）。
    与 PostgreSQL ``_cosine_similarity`` 语义一致，但放在 domain 层供聚类复用，
    避免 domain 纯函数反向依赖 adapter。
    """

    vec_a = _coerce_floats(a)
    vec_b = _coerce_floats(b)
    if not vec_a or not vec_b:
        return 0.0
    dim = min(len(vec_a), len(vec_b))
    if dim == 0:
        return 0.0
    dot = sum(vec_a[i] * vec_b[i] for i in range(dim))
    norm_a = sum(x * x for x in vec_a[:dim]) ** 0.5
    norm_b = sum(x * x for x in vec_b[:dim]) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _subject_token_set(subject: object, tokenizer: MemoryTokenizer | None) -> frozenset[str]:
    """subject 的归一化 token 集合，供 Jaccard 重合度计算。"""

    return frozenset(tokenize_memory_text(str(subject), tokenizer))


def hierarchical_cluster_complete(
    memories: Sequence[Mapping[str, object]],
    similarity_threshold: float,
) -> list[list[Mapping[str, object]]]:
    """全链接（complete linkage）层次聚类，按 embedding 余弦相似度归簇。

    替换原单链贪心 ``_greedy_cluster``：单链有传递漂移（A-B 0.75、B-C 0.75
    并入但 A-C 可能 0.55），簇被拉长使簇中心代表性下降。全链接要求新点与簇内
    **所有**点距离都在阈值内才并入，簇内最大距离收敛，代表性稳定。

    用 scipy ``linkage(method='complete')`` + ``fcluster(criterion='distance')``。
    对固定输入顺序确定性可复现（scipy 层次聚类无随机性），与现有"确定性是幂等
    基础"哲学一致。调用方已按 ``(memory_type, owner_id, memory_id)`` 排序，保证
    跨进程簇标签一致。返回簇列表，保持输入顺序稳定。

    距离 = 1 - 余弦相似度；fcluster 阈值 = 1 - similarity_threshold。
    """

    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage

    n = len(memories)
    if n <= 1:
        return [list(memories)] if n == 1 else []
    # condensed 距离向量（上三角一维数组，scipy linkage 原生接受，无需 squareform）。
    condensed: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            similarity = _embedding_cosine(
                memories[i]["embedding"], memories[j]["embedding"]
            )
            # 余弦相似度 [-1,1]，距离 [0,2]；负相关距离>1，截断到 0。
            condensed.append(max(0.0, 1.0 - similarity))
    distance_vector = np.array(condensed, dtype=float)
    # 全零距离（所有向量完全相同）时 linkage 会报错，兜底返回单簇。
    if not distance_vector.any():
        return [list(memories)]
    z = linkage(distance_vector, method="complete")
    labels = fcluster(z, t=1.0 - similarity_threshold, criterion="distance")
    # 按 label 分簇，保持原始索引顺序（fcluster 标签值与首次出现顺序无关，
    # 用 index 顺序而非 label 值排序保证稳定）。
    clusters_by_label: dict[int, list[Mapping[str, object]]] = {}
    order: list[int] = []
    for idx, label in enumerate(labels):
        if label not in clusters_by_label:
            clusters_by_label[label] = []
            order.append(label)
        clusters_by_label[label].append(memories[idx])
    return [clusters_by_label[label] for label in order]


def merge_by_entity_overlap(
    clusters: list[list[Mapping[str, object]]],
    *,
    entity_overlap_threshold: float = DEFAULT_ENTITY_OVERLAP_THRESHOLD,
    vector_floor: float = DEFAULT_ENTITY_MERGE_VECTOR_FLOOR,
    tokenizer: MemoryTokenizer | None = None,
) -> list[list[Mapping[str, object]]]:
    """实体一致补聚：把向量相似度在 0.50~0.70 中间地带漏聚的同标的记忆并回簇。

    全链接聚类用 0.70 严格阈值归簇（防漂移），但同标的、措辞不同的判断
    （"泡泡玛特海外增长"vs"泡泡玛特出海持续性强"）向量可能落在 0.50~0.70，
    被严格阈值漏聚。这步用 subject 实体 token Dice 系数补聚：某小簇/单点
    成员的 subject token 与另一簇 subject token 并集的 Dice ≥ 阈值 **且**
    与该簇至少一条成员向量相似度 ≥ 底线，则并入该簇。

    用 Dice 而非 Jaccard：subject 是短文本（项目名+维度），单字/词级 token 的
    Jaccard 对短文本偏严（交集小被并集放大惩罚），Dice（2*交集/两者之和）更
    温和，能让同标的的不同措辞过阈。区分"同标的 vs 不同标的"靠 Dice，区分
    "同标的 vs 同标的不同维度"靠向量底线——两道信号各管一头。

    实体一致不单独成簇（向量 <0.50 的同标的不同维度判断不并），叠加底线挡住
    维度信息丢失。这是 OR 关系的一腿——向量 0.70 OR (实体重合 AND 向量 0.50)。

    不跨 memory_type（调用方已按 type 分组，每组独立调用本函数）。确定性：
    按簇顺序、成员顺序遍历，同一输入跨进程可复现。
    """

    if not clusters:
        return clusters
    merged = [list(c) for c in clusters]
    # 每个簇的 subject token 并集，作为实体代表。
    cluster_tokens = [
        frozenset(
            t
            for member in cluster
            for t in _subject_token_set(member.get("subject"), tokenizer)
        )
        for cluster in merged
    ]

    # 反复扫描，把可并入的成员从小簇移到大簇，直到无变化。
    changed = True
    while changed:
        changed = False
        for target_idx in range(len(merged)):
            target = merged[target_idx]
            if not target:
                continue
            target_tokens = cluster_tokens[target_idx]
            if not target_tokens:
                continue
            for source_idx in range(len(merged)):
                if source_idx == target_idx:
                    continue
                source = merged[source_idx]
                if not source:
                    continue
                for member in list(source):
                    member_tokens = _subject_token_set(
                        member.get("subject"), tokenizer
                    )
                    if not member_tokens:
                        continue
                    if _dice(member_tokens, target_tokens) < entity_overlap_threshold:
                        continue
                    # 叠加向量底线：与目标簇至少一条成员向量相似度 ≥ floor。
                    if not any(
                        _embedding_cosine(
                            member["embedding"], existing["embedding"]
                        )
                        >= vector_floor
                        for existing in target
                    ):
                        continue
                    target.append(member)
                    source.remove(member)
                    cluster_tokens[target_idx] = target_tokens | member_tokens
                    changed = True
    # 丢弃被掏空的簇，保持顺序。
    return [c for c in merged if c]



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


def _dice(left: set[str], right: set[str]) -> float:
    """Dice 系数 = 2|交集|/(|left|+|right|)，对短文本比 Jaccard 更温和。

    Jaccard = |交集|/|并集|，交集小被并集放大惩罚；Dice 分母是两者之和
    （=并集+交集），对短 subject 的措辞差异更宽容，让同标的的不同措辞过阈。
    """

    if not left or not right:
        return 0.0
    intersection = len(left & right)
    total = len(left) + len(right)
    if total == 0:
        return 0.0
    return 2.0 * intersection / total


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
