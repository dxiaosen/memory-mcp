# 记忆体方案对比（答辩素材）

> 调研日期 2026-08-12。基于官方文档/GitHub/arXiv 论文实测，不含虚构。
> 每个方案附来源 URL。Memory MCP 为本项目。

## 一、一句话定位

| 方案 | 定位 | 来源 |
| --- | --- | --- |
| **Memory MCP（本项目）** | owner-scoped 长期记忆服务，服务端统一负责抽取/准入/生命周期/召回，MCP 协议接入 | 本仓库 |
| Mem0 | "AI Agent 的记忆层"，三存储（SQL+向量+实体），ADD-only 累积，MCP 接入 | mem0.ai |
| Zep / Graphiti | 时序知识图谱，双时态边（事实失效不删，可查任意时间点），图库存储 | getzep.com |
| Letta（原 MemGPT） | "LLM 即操作系统"，模型自主管理多级记忆（core/archival/recall），模型自己写记忆 | letta.com |
| A-Mem | 学术方案（arXiv:2502.12110），Zettelkasten 式自组织记忆，模型自主抽关键词/标签/链 | arxiv.org/abs/2502.12110 |
| Memary | 知识图谱记忆（Neo4j/FalkorDB），Memory Stream + Entity Store，被动抽取实体 | github.com/kingjulio8238/Memary |
| Claude Code memory | CLAUDE.md（手写文件）+ auto memory（模型用 Write/Edit 写 markdown），单机文件，非服务 | code.claude.com/docs/en/memory |
| LangChain/LangMem | 经典对话 buffer（已废弃）+ LangGraph checkpoint/store + LangMem 后台抽取，库非服务 | docs.langchain.com |

## 二、横向能力对比

| 维度 | Memory MCP | Mem0 | Zep | Letta | A-Mem | Memary | Claude Code | LangChain/LangMem |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **身份隔离** | ✅ `tenant:subject` 从认证 token 派生 | △ user_id + app_id（非组合 owner 键） | △ user_id 字符串 + API key 级 | △ Organization 级 | ❌ 无 | △ user_id + 文件路径 | ❌ 无（官方建议用文件系统隔离） | △ thread_id/namespace 手动映射 |
| **多团队记忆** | ✅ `team:team_id` + 服务端聚类自动提取 | ❌ 无（group chat 需手动逐人 add） | ❌ groups 页 404 | △ Org 级共享 git 仓 | ❌ | ❌ | ❌ | ❌ |
| **服务端抽取** | ✅ Stop hook 强制入队 + 队列异步抽取 | ✅ LLM 抽取（ADD-only） | ✅ LLM 抽实体/事实/边 | ❌ 模型自主工具调用 | ✅ 模型自主抽 | ✅ 被动抽实体 | ❌ 模型直接 Write/Edit | △ LangMem 后台 consolidation |
| **准入门** | ✅ auto_save/pending/discard/blocked | ❌ 仅 PENDING→SUCCEEDED | ❌ 仅 Entity/Fact Resolution | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无（仅 200 行/25KB 大小上限） | ❌ 无（checkpoint 无界增长，官方让用户自己 prune） |
| **幂等去重** | ✅ event_id + payload_fingerprint | ❌ 无（重投产生重复） | ❌ 无（靠 resolution 事后兜底） | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 不适用 | ❌ store upsert 按 key |
| **生命周期** | ✅ revoke + 到期物化 + replacement | △ expiration_date（隐藏非删）+ Dream supersede | ✅ 双时态边（失效不删） | ❌ 无自动生命周期 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无（官方建议手动 prune） |
| **结构化候选** | ✅ 类型化 DTO（memory_type/assertion_kind/confidence/source_role） | ✅ 结构化事实（infer=True） | ✅ 实体/事实/边 + 时态 | ❌ 裸文本 + embedding | △ 半结构（关键词/标签） | △ 图实体 | ❌ 裸 markdown | ❌ 裸图状态/JSON 文档 |
| **协议** | ✅ MCP Streamable HTTP | ✅ MCP（11 工具）+ REST | ✅ MCP（13 工具）+ REST | ✅ MCP 客户端 + REST | ❌ Python 库 | ❌ Python SDK + Streamlit | ❌ 文件约定 | ❌ 库/SDK（可消费 MCP 但不作为 MCP 服务暴露） |
| **权威存储** | ✅ PostgreSQL（唯一） | ✅ SQL + 向量 + 实体 | ❌ 图库（Neo4j/FalkorDB） | ✅ PG + pgvector / SQLite | ❌ ChromaDB | ❌ Neo4j + JSON 文件 | ❌ 本地 markdown 文件 | △ InMemory/SQLite/PG（默认 InMemory） |
| **召回** | ✅ trigram + 向量 + 近期 + 时间线 | ✅ 语义 + BM25 + 实体融合 | ✅ 向量 + BM25 + 图遍历 | ✅ 向量 + 混合 | ✅ 余弦相似 | ✅ 图遍历 + 多跳 | ❌ 无（全量注入） | △ 向量检索（配置时） |
| **文档来源溯源** | ▷ 预留表（evidence_documents），待激活 | ❌ | ✅ Episode provenance | ❌ | ❌ | ❌ | ❌ | ❌ |

图例：✅ 完整实现 ｜ △ 部分实现/有但弱 ｜ ▷ 预留未激活 ｜ ❌ 无

## 三、Memory MCP 的差异化特点（答辩主轴）

基于上表，本项目相比市面方案的独特贡献集中在四个维度：

### 1. owner-scoped 身份隔离（最硬差异）
`owner_key = tenant_id:subject_id` 从认证 token 派生，服务端强制，工具参数不接受 owner。
- Mem0 用 `user_id` 字符串 + `app_id`，无组合 owner 键
- Zep 用 `user_id` + API key 级隔离，无 `tenant:subject`
- Letta 用 Organization 级，Identity 是软关联
- Claude Code 官方文档明确警告"Do not rely on default `query()` options for multi-tenant isolation"，建议用户自己用文件系统隔离

### 2. 服务端准入质量门（对比 A-Mem/Letta/Mem0 的核心差异）
候选抽取后经 `ConservativeAdmissionPolicy` 分四类：`auto_save`（用户高置信持久判断直接入库）/ `pending`（非用户来源降级待审）/ `discard`（临时/操作指令丢弃）/ `blocked`（敏感阻断）。
- Letta/A-Mem：模型自主写，无门，assistant 说什么就记什么
- Mem0：ADD-only 累积，只有 `PENDING→SUCCEEDED` 处理状态，无准入分类
- Zep：只有 Entity/Fact Resolution（去重/合并），无 pending/discard/blocked
- Claude Code：无任何门，模型直接 Write/Edit

**答辩话术**：非用户来源（assistant 复述/推断）降级为 pending，是"owner 可控的记忆质量门"——市面方案把质量交给模型，本项目把质量交给服务端 + 用户审核。

### 3. 幂等去重（对比 Mem0 的核心差异）
`event_id = memory-agent:{sha256(owner|conversation|turn)}`，同 event_id 不同 payload 报冲突，重放返回原结果。
- Mem0 官方文档原话："Retried POST calls will create duplicate memories since 'nothing is overwritten' and the pipeline is purely additive"
- Zep/Letta/A-Mem/Memary：无 event_id/幂等键机制

### 4. 多团队公共记忆（全方案独有）
`tenant_id:team:team_id` owner + 服务端 embedding 聚类自动提取团队公共记忆（簇中心 + 弱方向校验 + 主题归并）。
- Mem0：无 team 概念，group chat 不自动按说话人拆
- Zep：groups 文档页 404
- Letta：Org 级共享 git 仓，非跨租户团队
- 其余方案均无

## 四、诚实承认的劣势（答辩时不要回避）

| 维度 | 市面更强方案 | 本项目现状 |
| --- | --- | --- |
| 时序事实演进 | Zep 双时态边（可查任意时间点真值） | revision + replacement，不支持任意时间点查询 |
| 抽取 benchmark | Mem0 LoCoMo 92.5 / Zep 94.7 | 无公开 benchmark，抽取质量仍在迭代（见下方"已知问题"） |
| 图结构记忆 | Zep 实体/事实/边图 + 图遍历召回 | 无图结构，靠 relation + 三路召回 |
| 文档来源激活 | Zep Episode provenance | evidence_documents 表预留未激活 |
| 成熟度/生态 | Mem0 20+ 向量库 / Letta ADE UI | 单一 PG，无管理 UI |

## 五、已知问题与迭代方向（如实陈述）

1. **assistant 内容抽取过度**：当前抽取 prompt 鼓励每轮 5-10 条，投研 dense turn 会抽满；准入门把 assistant 来源降级 pending（不污染 active），但 pending 堆积增加审核负担。迭代方向：收紧 prompt 引导 + assistant 提问/复述不抽取。
2. **document provenance 退化**：PENDING 行存合并 content，worker 反解 [user, assistant] 重建 messages；tool/document/web 来源的 TurnMessage 字段（source_uri/content_hash）未激活，evidence_documents 表待 transcript provenance 恢复后激活。
3. **无公开 benchmark**：有 evals 框架（deterministic/live-extraction/live-embedding），但未跑公开数据集（LoCoMo/LongMemEval）。

## 六、来源 URL

### Mem0
- https://docs.mem0.ai/
- https://github.com/mem0ai/mem0
- https://docs.mem0.ai/core-concepts/how-it-works
- https://docs.mem0.ai/platform/mem0-mcp
- https://docs.mem0.ai/api-reference/memory/add-memories

### Zep / Graphiti
- https://www.getzep.com
- https://github.com/getzep/graphiti
- https://arxiv.org/abs/2501.13956
- https://github.com/getzep/graphiti/blob/main/mcp_server/README.md

### Letta / MemGPT
- https://arxiv.org/abs/2310.08560
- https://github.com/letta-ai/letta
- https://docs.letta.com/
- https://docs.letta.com/concepts/memfs

### A-Mem
- https://arxiv.org/abs/2502.12110
- https://github.com/WujiangXu/A-mem-sys

### Memary
- https://github.com/kingjulio8238/Memary
- https://pypi.org/project/memary/

### Claude Code memory
- https://code.claude.com/docs/en/memory
- https://code.claude.com/docs/en/agent-sdk/claude-code-features.md
- https://code.claude.com/docs/en/mcp.md

### LangChain / LangMem
- https://docs.langchain.com/oss/python/langgraph/memory
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/stores
- https://github.com/langchain-ai/langmem
- https://docs.langchain.com/oss/python/langchain/mcp
