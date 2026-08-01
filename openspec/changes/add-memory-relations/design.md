## Context

Memory Core 当前以稳定 `MemoryItem` 承载逻辑身份，以不可变 `MemoryRevision` 表达内容替代，以 `Evidence` 保存来源。`InvestmentResearchProfile` 已能区分论点、证据、风险、催化剂、问题、长期研究事项和研究决策，但这些记录只能独立召回。`MemoryProfile` 同时暴露了未被消费的 `allowed_relations` 和 `relation_rules`，两个正式 Profile 都只能返回空值。

关系能力跨越领域、Profile、Repository、PostgreSQL、应用服务、MCP DTO 和召回排序。它必须延续三个现有边界：owner 只来自认证上下文；业务词汇只由 Profile 声明；同步 Repository 事务通过 MCP 的 `asyncio.to_thread` 调用，不引入队列或新服务。

## Goals / Non-Goals

**Goals:**

- 用一个通用模型保存、撤销和读取同 owner、同 Profile 的有向记忆关系。
- 让 Profile 精确约束每种关系允许的起点与终点 memory type。
- 让关系跟随稳定 MemoryItem，在内容 revision 被替代时仍然成立；端点失效后不参与普通读取和召回。
- 在 PostgreSQL 约束、应用校验和 MCP 认证三个层次维持 owner 隔离。
- 让投研召回能利用一跳关系增强排序并返回可解释关系，同时继续遵守数量和 token budget。
- 保持 `general-work`、普通 MCP 客户端和 Agent Hook 配置兼容。

**Non-Goals:**

- 本变更不让候选抽取模型自动生成关系，也不增加第二次模型调用。
- 不支持跨 owner、跨 Profile、自环或任意字符串关系。
- 不做图数据库、向量数据库、递归图遍历、实体消歧或关系推理。
- 不物理删除关系；撤销记录继续可审计。
- 不把关系正文、owner 或 Secret 写入运行日志。

## Decisions

### 1. 关系指向 MemoryItem，而不是 MemoryRevision

`MemoryRelation` 保存 `source_memory_id` 和 `target_memory_id`。内容 replacement 只产生新 revision，不需要重建关系；普通关系查询会联结两个端点的 current revision，并要求它们在查询时刻是 active 且有效。关系自身使用 `active/revoked` 状态和 `revoked_at`，端点撤销或过期不会改写关系历史，只会让它暂时不出现在活动结果中。

替代方案是指向 revision。它能精确描述某条证据支持某一版论点，但每次论点修订都会制造关系迁移和悬挂边，超出本原型需要。revision 级出处仍由 Evidence/history 负责。

### 2. 用单一强类型 relation policy 取代两套空字段

`MemoryProfile.relation_policies` 是 `relation_type -> MemoryRelationPolicy` 映射。每项 policy 包含合法 `source_memory_types`、`target_memory_types` 和供开发者阅读的稳定说明。`ProfileRegistry` 要求 key 规范化、policy 类型正确、端点集合非空且都是本 Profile memory type；同时补齐 `recall_priorities` 必须逐一覆盖 memory type 且值为非负整数的校验。

`allowed_relations` 与 `relation_rules` 被删除，避免“允许列表”和“规则说明”发生漂移。这是自定义 Profile 的内部扩展 API 变更，不改变 MCP 请求。

投研 v1 关系词汇为：

| relation type | source | target | 含义 |
| --- | --- | --- | --- |
| `supports` | `evidence_claim` | `thesis` | 外部证据支持论点 |
| `challenges` | `evidence_claim` | `thesis` | 外部证据挑战论点 |
| `threatens` | `risk` | `thesis` | 风险可能破坏论点 |
| `could_catalyze` | `catalyst` | `thesis` | 事件可能推动论点演化 |
| `addresses` | `ongoing_research` | `research_question` | 长期研究事项处理某个问题 |
| `resolves` | `research_decision` | `research_question` | 研究结论解决某个问题 |

方向是规范的一部分；读取时 DTO 用 `outgoing/incoming` 表示当前记忆位于哪一端。`general-work` 使用空映射，不暴露虚假的关系能力。

### 3. 创建与撤销都在 Repository 事务内重新验证

应用服务先 owner-scoped 读取两个端点，以便给出统一的 unavailable 语义，再由 ProfileRegistry 校验类型方向。Repository 的 `link_relation` 在单一事务中重新锁定端点 current revision，验证 owner、同 Profile、active 和有效期，并执行活动关系唯一写入；相同 `(owner, source, target, type)` 重放返回既有活动关系。并发创建由部分唯一索引收敛。

`revoke_relation` 按 owner 和 relation ID 锁定记录。活动关系写入 `revoked` 与 `revoked_at`；再次撤销返回同一记录。其他 owner 猜测 relation ID 与不存在都返回 `RELATION_UNAVAILABLE`。

创建使用 `memory:write`，撤销使用 `memory:review`。`link_memories` 和 `revoke_memory_relation` 都不接受 owner、profile 或 client selector，profile 从端点可信记录派生。

### 4. PostgreSQL 同时约束 owner、Profile 和关系词汇

不可修改的 `0006_memory_relations.sql`：

- 为 `memory_items(memory_id, owner_id, profile_id)` 增加唯一身份约束；
- 增加 `memory_profile_relations(profile_id, relation_type)` 目录；
- 增加 `memory_relations`，两个复合外键都引用同一个 `(owner_id, profile_id)` 下的 MemoryItem；
- 增加非空、自环、状态/时间一致性约束；
- 增加活动关系部分唯一索引，以及 owner/profile/source/target 查询索引。

注册 Profile 时只补充其当前 relation type。已用过但随后从代码 Profile 移除的目录项不物理删除，以保留历史外键；新写入仍由当前 ProfileRegistry 拒绝。关系表不依赖扩展插件，兼容当前 RDS。

### 5. 详情返回一跳关系，召回只在安全候选集内加权

`get_memory` 默认返回该记忆的活动关系；`include_history=true` 时同时返回已撤销关系。关系摘要包含稳定 ID、类型、方向、另一端 memory ID/subject/type、状态和时间，不复制另一端正文。

RecallService 先沿用 Repository 的 owner/profile/active/current/effective 过滤，再一次性读取这些候选之间的活动关系。每条记忆先计算现有文本/subject/type 分数；若另一端达到相关阈值，本端最多获得 `0.12` 的一跳关系加分。它不把过滤集合之外的记录带入结果，不递归，不因关系绕过 `max_items`。结构化结果返回活动关系摘要；rendered context 只渲染已选中端点之间的关系，且与正文一起计入 token budget。

替代方案是关系一律扩展召回。它可能把弱相关或敏感上下文仅凭一条边注入当前任务，也会让预算不可预测，因此拒绝。

### 6. 关系不进入主动捕获事务

AfterRun 继续只提交完成轮次和候选记忆。关系需要两个已存在、可精确标识的 memory ID，本轮不从自由文本猜测目标。支持原生 MCP 工具选择的 Agent 可以显式调用 `link_memories`；使用通用 Hook 的产品可在后续受控集成层决定何时调用，而用户侧仍只配置 URL 和 Token。

## Risks / Trade-offs

- [手工或 Agent 工具调用才能建立关系] → 本轮优先保证关系正确、可治理；自动关系抽取作为后续独立变更评估。
- [关系指向 item，论点修订后语义可能变化] → history 保留修订来源；对语义已不成立的关系显式撤销，不自动猜测。
- [关系加权可能改变旧召回顺序] → 只在另一端本身相关时提供有上限的小幅加分，并增加确定性排序测试。
- [Profile 删除关系类型后数据库目录仍保留] → 当前运行时 Registry 仍是新写入权威；保留目录是历史兼容策略，不表示可继续创建。
- [Repository 代码继续增长] → 映射保持在现有 adapter 内，关系 SQL 使用独立私有常量/辅助函数；不为单一表增加重复架构层。

## Migration Plan

1. 部署新代码前运行 `memory-mcp-db migrate` 应用 `0006`；migration 对已有数据只增加空关系表和一个唯一约束，不回填内容。
2. 新 Server 注册两个内置 Profile，并把投研关系类型登记到目录。
3. 先验证 health、Profile 注册和只读旧工具，再验证关系创建/撤销/召回。
4. 回滚到旧 Server 时新增表会被忽略；旧代码不访问关系表，已有记忆和工具仍工作。migration 不向后删除。
5. migration 一经进入共享数据库不得编辑 checksum；修复使用后续 migration。

## Open Questions

无。本轮不自动抽取关系、不做实体消歧，避免把这些未决策略混入通用 Core。
