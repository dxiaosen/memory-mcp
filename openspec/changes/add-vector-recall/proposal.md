## Why

当前召回用 trigram + jieba 词法匹配，只能抓字面相似，抓不了语义相似。投研场景中"看好新能源"和"锂电池前景"语义相关但字面不重叠，trigram 召不到。真实用户使用时查询措辞和记忆内容措辞不会整齐，词法召回必然有漏。加向量召回作为第三路候选源，与 trigram 互补。

## What Changes

- 新增 `EmbeddingProvider` 端口和 Qwen `text-embedding-v3` 实现（DashScope 兼容 OpenAI 接口）。
- `memory_revisions` 表新增 `embedding vector(1024)` 列，使用 pgvector 扩展和 ivfflat 索引。
- capture 流程在 auto-save 后计算 embedding，同一事务写入；失败时 embedding=NULL，记忆照常写入。
- 召回查询从两路（lexical + recent）改为三路（lexical + vector + recent），配额 40%/30%/30%。
- 上层打分逻辑不变——向量只负责候选生成，jieba 分词打分和 Profile 加权照常。
- embedding API 不可用时降级为现有两路召回，不阻断服务。

## Capabilities

### New Capabilities

- `vector-recall`: 规定 embedding 写入、向量召回查询、三路配额分配和降级策略。

### Modified Capabilities

无。主规范尚未归档。

## Impact

- DB：pgvector 扩展 + `memory_revisions` 加 `embedding` 列 + ivfflat 索引。
- Core：新增 `EmbeddingProvider` 端口；`MemoryRevision` 新增 `embedding` 字段。
- Extraction：新增 Qwen embedding adapter + embedding 配置项。
- PostgreSQL：`recall.py` 三路 CTE 查询；`repository.py` insert 时写入 embedding。
- Composition：注入 embedding provider。
- Settings：新增 embedding 配置（model/base_url/api_key/dimensions）。
- 评测：新增语义召回 case。
- 非目标：不替换 trigram；不引入 pgvector ANN 参数调优；不在 Agent 端做 embedding。
