## 1. DB schema

- [x] 1.1 `0001_memory_schema.sql` 加 `CREATE EXTENSION IF NOT EXISTS vector`。
- [x] 1.2 `memory_revisions` 加 `embedding vector(1024)` 列 + ivfflat 索引。
- [x] 1.3 重建 schema，确认 pgvector 可用。

## 2. EmbeddingProvider 端口 + Qwen 实现

- [x] 2.1 `core/ports/embedding.py` 定义 `EmbeddingProvider` 协议（含共享辅助 `embed_single`）。
- [x] 2.2 `extraction/embedding.py` 实现 `QwenEmbeddingProvider`（DashScope /v1/embeddings）。
- [x] 2.3 `extraction/settings.py` 新增 embedding 配置项。
- [ ] 2.4 导出新类型到 `core.ports.__init__` 和 `core.__init__`。已导出至 `core.ports.__init__`，但 `core/__init__.py` 与 `core.__all__` 尚未导出 `EmbeddingProvider`/`embed_single`——与其余 ports 导出风格不一致，待补。

## 3. capture 写入 embedding

- [x] 3.1 `MemoryRevision` domain model 加 `embedding: tuple[float, ...] | None` 字段。
- [x] 3.2 `candidate_processing.py` materializer 构造 revision 时调 `embedding_provider.embed()`（经 `embed_single`）。
- [x] 3.3 `repository.py` `_insert_revision` SQL 加 embedding 列。
- [x] 3.4 `mapping.py` `to_revision` 读取 embedding。
- [x] 3.5 embedding 计算失败时 `embedding=None`，不阻断 capture。

## 4. 召回三路查询

- [x] 4.1 `recall.py` `find_recall_candidates` 改为三路 CTE（lexical + vector + recent）。
- [x] 4.2 `RecallCandidateSet` 加 `vector_count` 字段。
- [x] 4.3 `recall_service.py` 调 `embedding_provider.embed()` 算 query 向量，传入 Repository（经 `embed_single`）。
- [x] 4.4 query embedding 失败时跳过 vector 路。
- [x] 4.5 in_memory adapter 同步加 vector 路模拟。

## 5. 组合与配置

- [x] 5.1 `composition.py` 注入 `embedding_provider`。
- [x] 5.2 `app.py` 从 settings 构造 embedding provider 注入。
- [x] 5.3 `.env.example` 加 embedding 配置示例。

## 6. 测试与评测

- [ ] 6.1 单元测试：embedding provider mock + 三路召回逻辑（代码已实现向量路，但测试套件尚无向量/embedding 专项用例）。
- [ ] 6.2 评测：加一个语义召回 case（"看好新能源"↔"锂电池前景"）。
- [ ] 6.3 降级测试：embedding API 不可用时退化为两路（无专项测试，仅靠 provider 返回 None 的隐式路径）。
- [ ] 6.4 真实 DB 契约测试：pgvector 查询执行。
- [x] 6.5 跑全量 pytest + evals 确认不回退。

## 7. 文档

- [x] 7.1 `design.md` 召回章节加向量路描述。
- [x] 7.2 `config.md` 加 embedding 配置项。
