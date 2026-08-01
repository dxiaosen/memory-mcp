## Context

`add-memory-relations` 和 `automate-memory-relations` 已经提供 owner-scoped 一跳关系、Profile 方向校验、自动关系抽取及 Capture 原子写入。当前 `MemoryRelation` 只包含稳定 MemoryItem 端点、类型、`active/revoked` 和时间；自动建议中的精确来源表达、confidence、expression basis 与模型版本在规划后丢失。

关系指向稳定 MemoryItem 对人工治理关系很合适，但对 `supports/challenges/threatens` 等依赖具体内容的语义边不够精确：端点生成 replacement revision 后，旧边仍可能作用于新内容。`0006_memory_relations.sql` 已进入共享数据库，checksum 不得修改。

当前测试对安全、事务和幂等覆盖充分，但“模型是否抽对、关系是否误建、同义任务能否召回”分散在测试案例中，没有稳定数据集和统一指标。

## Goals / Non-Goals

**Goals:**

- 区分 legacy、人工和自动关系，并区分 item-scoped 与 revision-scoped 语义。
- 保存自动关系的可信 Capture 定位、用户原文证据、confidence 和抽取版本。
- 在 replacement 事务中自动把受影响的 revision-scoped 活动关系转为 stale。
- 让 stale 边停止参与普通详情、召回和活动唯一性，同时保留历史与人工撤销能力。
- 用 `0007` 向前 migration 保持既有关系可读，不伪造历史 provenance。
- 建立不访问网络的确定性评估基线，并提供显式真实模型评估入口。

**Non-Goals:**

- 不自动判断端点内容未变化时一条关系是否语义失效。
- 不把人工关系强制改成 revision-scoped；人工治理边默认跨 revision 保留。
- 不新增关系待确认、关系 revision 表、多跳图、实体消歧、向量检索或队列。
- 不把评估样本、正文、owner、Token 或模型密钥写入 operational log。
- 不修改 `0006`，不物理删除旧关系。

## Decisions

### 1. 关系分为 origin、scope 和 lifecycle 三个正交维度

新增：

```text
origin = legacy | manual | automatic
scope  = item | revision
status = active | stale | revoked
```

`legacy` 专门表示 `0007` 以前无法判断来源的记录，避免把历史自动边伪装成人工边。显式 `link_memories` 创建 `manual/item`；AfterRun 创建 `automatic/revision`。自动关系必须保存两端创建时的 revision ID；新人工关系也保存快照用于审计，但 item scope 不因快照变化失效。

拒绝把所有关系都改成 revision 端点：人工建立的“事项处理问题”等稳定关系通常应跨内容修订存在。也拒绝继续只指向 Item，因为投研支持/挑战关系的真假取决于具体论点与证据版本。

### 2. 自动 provenance 是强类型不可变值

`RelationProvenance` 包含：

- `capture_id`、`conversation_id`、`source_turn_id`；
- 脱敏轮次中的精确 `source_expression`；
- `confidence`、`expression_basis`；
- relation extractor 的 `model_id/prompt_version/schema_version`。

`automatic` 必须携带完整 provenance；`manual/legacy` 不得携带伪造模型 provenance。owner/profile/端点仍只来自可信应用上下文，模型只能引用本次端点目录。来源表达已经经过 SensitiveContentGuard；默认日志只输出稳定 ID、数量、origin/scope/status，不输出表达正文。

选择在关系表中保存单份 provenance，而不是立即增加关系证据多值表：当前一次自动建边只有一个准入表达，字段均参与校验和审计。若以后同一边需要积累多来源，再以关系 evidence 表向前扩展。

### 3. replacement 在同一事务物化 stale

每当 Capture 或 Review confirmation 写入 replacement revision，Repository 在同一事务中将连接该 MemoryItem、`scope=revision`、`status=active` 且 revision 快照不再匹配的关系更新为：

```text
status = stale
stale_at = replacement.created_at
stale_reason = endpoint_revision_changed
```

随后才插入本轮自动关系，所以针对新 revision 的新边保持 active。事务失败时 replacement、stale 更新和新关系共同回滚。普通关系读取只接受 active；`include_history=true` 返回 stale/revoked。活动唯一索引继续只覆盖 active，因此 stale 后可以为相同端点和类型建立新边。

拒绝仅在查询时隐式比较 revision：隐式过滤虽然简单，但无法审计何时、为什么停止生效，也无法区分数据错误与版本演化。

### 4. Repository 是 revision 快照的最终可信校验边界

应用层从 `MemoryRecord.current_revision` 构造快照。Repository 写入前重新锁定 current endpoints，并要求 revision-scoped 快照等于当前 revision；自动关系还必须具有完整 provenance。PostgreSQL 增加 revision 到 `(revision_id, memory_id, owner_id)` 的复合外键，防止快照引用其他 owner 或错误端点。

Core 继续只依赖领域类型和 Repository port；SQL、Psycopg、MCP DTO、模型 backend 保持在既有边界。没有新增运行配置或外部依赖。

### 5. `0007` 一次性迁移关系来源与生命周期

Migration 新增 nullable revision/provenance/stale 字段，以及带默认值的 `origin=legacy`、`scope=item`。旧记录保持原有 active/revoked 语义，不回填无法证明的模型证据或 revision 快照。更新 status/check constraints 后保留原活动唯一索引和 owner/Profile 外键。

PostgreSQL `CHECK` 会把包含 `NULL` 的 unknown 当作通过，因此 `0007` 的 automatic
provenance 文本/confidence 和 stale reason 约束都显式写出 `IS NOT NULL`。本变更仍处于
正式交付前，关系扩展收敛为单个最终版 `0007`；当前开发数据库直接同步该最终结构和
checksum，不为了预发布修正额外叠加 migration。`0006` 及更早已交付 migration 仍保持
不可变。

新代码读取 legacy；新写入只允许 manual/automatic。旧 Server 不认识新增 `stale` 状态，因此数据库 migration 与新 Server 同批发布，应用回滚只能回到理解 `stale` 的兼容版本，不能直接回滚到 `0006` 时代二进制。

### 6. 评估数据与 runner 留在开发边界

顶层 `evals/` 保存无 Secret 的 JSON 案例和 runner，不进入轻量 Agent 包。案例覆盖：

- 候选是否应保存及期望 memory type；
- 关系是否应建立及期望方向；
- 查询应命中的 memory 标签；
- 越权、低置信、Assistant-only 等安全负例。

默认 runner 只运行生产确定性的 Recall@K 和安全负例通过率，candidate/relation 标记为未评测；显式 `--live-model` 才读取现有 `MEMORY_MCP_MODEL_*`、运行候选/关系并输出 precision/recall。普通 `pytest` 校验数据 schema、离线隔离、安全输出和确定性产品行为，不用金标 baseline 模拟模型质量。阈值不满足返回非零退出码；评估结果只写安全汇总，不回写样本文本或生产数据库。当前细化合同与结果见后续 `benchmark-investment-memory-quality` 变更和 `docs/evaluation.md`。

## Risks / Trade-offs

- **[关系 DTO 返回 source expression 会增加内容暴露]** → 只有已认证 owner 的显式详情/history 能看到；普通 recall 渲染不包含 provenance 正文，日志继续禁止正文。
- **[replacement 会让高价值边暂时消失]** → 这是保守正确性选择；新轮次可自动重建，新边不会继承旧证据。
- **[旧二进制无法读取 stale]** → 发布前迁移并保留上一版兼容构建；不对关系 schema 做破坏性回滚。
- **[评估集与真实分布偏离]** → 案例带稳定 ID/类别并可追加；指标按版本记录，不把一次真实模型结果写成单元测试金标。
- **[provenance 字段增加表宽]** → 关系数量远小于消息量，且只保存准入表达而非完整轮次；后续规模数据再决定拆表。

## Migration Plan

1. 定稿 `0007_relation_provenance.sql`，不改 `0006` 及更早 migration。
2. 发布理解 legacy/manual/automatic、item/revision 和 stale 的 Server，并执行 migration。
3. 重启 Server；Agent/Hook 配置不变。
4. 验证旧关系读取、新自动关系 provenance、replacement stale 和新 revision 重建关系。
5. 若需要回滚业务逻辑，回滚到仍理解 `0007` 字段和 stale 状态的兼容 Server；数据库不降级、不删除 provenance。

## Open Questions

无阻塞问题。多份关系 Evidence、可信来源自动核验、关系待确认和实体归一化属于后续独立变更。
