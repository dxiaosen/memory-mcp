## Context

当前召回用 trigram + jieba 两路，只能抓字面相似。投研场景需要语义匹配——"看好新能源"↔"锂电池前景"字面不重叠但语义相关。pgvector 在阿里云 RDS 上可用（已验证 `CREATE EXTENSION vector` 成功）。DeepSeek 不支持 embedding API（返回 404），Qwen DashScope 兼容 OpenAI 接口可用。

约束：不改上层打分逻辑；不引入本地模型依赖；embedding API 不可用时降级；PostgreSQL 是唯一权威；core.domain 不依赖 HTTP/DB。

## Goals / Non-Goals

**Goals:**

- capture 时计算 embedding 存入 pgvector。
- 召回时算 query embedding，做三路混合查询。
- embedding API 不可用时不阻断服务。
- 现有 52 个 case 评测不回退。

**Non-Goals:**

- 不替换 trigram，只增加第三路。
- 不引入 sentence-transformers 等本地模型。
- 不做 pgvector ivfflat 参数调优（用默认参数）。
- 不在 Agent 端做 embedding。

## Decisions

### 1. EmbeddingProvider 端口注入

`core.ports` 新增 `EmbeddingProvider` 协议：

```python
class EmbeddingProvider(Protocol):
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...
    @property
    def model_id(self) -> str: ...
    @property
    def dimensions(self) -> int: ...
```

`extraction/embedding.py` 提供 `QwenEmbeddingProvider` 实现，调 DashScope `/v1/embeddings`。`composition.py` 注入。`core.application` 和 `core.domain` 不依赖具体实现。

### 2. pgvector 存储

`memory_revisions` 新增 `embedding vector(1024)` 列。ivfflat 索引：

```sql
CREATE INDEX memory_revisions_embedding_idx
    ON memory_revisions USING ivfflat (embedding vector_cosine_ops)
    WHERE is_current AND lifecycle_status = 'active';
```

只索引 active + current 的 revision（历史版本不参与召回）。

### 3. capture 写入 embedding

auto-save 的候选在 materializer 构造 `MemoryRevision` 时调 `embedding_provider.embed(content)`。如果失败（网络/API 不可用），`embedding=NULL`，记忆照常写入。后续维护任务可补算（本变更不做补算，留作后续）。

`pending` 候选不计算 embedding（还没确认，不浪费 API 调用）。confirm 时如果指定了 team owner，也计算 embedding。

### 4. 三路召回查询

```sql
WITH lexical AS (
    SELECT ..., similarity(subject, query) AS score
    WHERE subject %% query OR content %% query
    LIMIT ceil(limit * 0.4)
),
vector AS (
    SELECT ..., 1 - (embedding <=> query_embedding) AS score
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> query_embedding
    LIMIT ceil(limit * 0.3)
),
recent AS (
    SELECT ..., 0.0 AS score
    WHERE NOT EXISTS (已出现在 lexical 或 vector 里)
    ORDER BY observed_at DESC
    LIMIT limit - lexical_count - vector_count
)
SELECT * FROM lexical UNION ALL SELECT * FROM vector UNION ALL SELECT * FROM recent
```

`<=>` 是 pgvector 余弦距离。`1 - distance` 转换为相似度分数。

### 5. RecallService 传 query embedding

`RecallService.recall()` 在调 `find_recall_candidates` 前，先调 `embedding_provider.embed(search_text)` 算查询向量。如果失败，不传 query_embedding，vector 路跳过。

### 6. 降级策略

| 情况 | 行为 |
|---|---|
| embedding API 不可用（启动时） | Server 启动成功，recall 降级为两路 |
| 记忆没存 embedding | 该记忆不参与 vector 路 |
| query embedding 计算失败 | 跳过 vector 路，只用 lexical + recent |
| pgvector 扩展未安装 | 启动失败 |

### 7. 配置

```python
# extraction/settings.py 新增
embedding_model: str = "text-embedding-v3"
embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
embedding_api_key: SecretStr | None = None  # 不配则复用 model_api_key
embedding_dimensions: int = 1024
```

### 8. RecallCandidateSet 扩展

```python
@dataclass(frozen=True, slots=True)
class RecallCandidateSet:
    candidates: tuple[MemoryRecallCandidate, ...]
    lexical_count: int
    vector_count: int  # 新增
    recent_count: int
```

## Dependency Direction

- `core.ports.EmbeddingProvider` 不依赖 HTTP/DB。
- `extraction.QwenEmbeddingProvider` 可依赖 httpx。
- `core.application.RecallService` 调 `EmbeddingProvider.embed()`，不依赖具体实现。
- `core.adapters.postgresql.recall.py` 三路 CTE 查询。
- `composition.py` 注入 embedding provider。

## Risks / Trade-offs

- [pgvector ivfflat 索引需要建索引时指定 lists 参数] → 用默认值，当前数据量小。
- [embedding API 延迟] → capture 时增加约 100-200ms；recall 时增加约 100ms（一次 API 调用）。
- [embedding 维度 1024 占存储] → 每条 revision 增约 4KB，1000 条约 4MB，可接受。
- [Qwen API 可能限流] → 批量请求 + 降级策略。

## Migration Plan

1. `0001_memory_schema.sql` 加 pgvector 扩展 + embedding 列 + ivfflat 索引。
2. `migrate --rebuild` 重建 schema。
3. 发布代码；已有记忆没有 embedding，只参与 lexical + recent 路。
4. 新 capture 的记忆自动有 embedding。
5. 评测：加一个语义召回 case 验证 vector 路生效。
