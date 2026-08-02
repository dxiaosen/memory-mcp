## Context

当前 Core 已经区分 `active`、`superseded`、`expired`、`revoked`，并通过 `valid_until` 在读取时排除过期 revision；replacement、review、duplicate Evidence 和 relation stale 也已具备事务语义。但 `expired` 没有运行时写入路径，过期 pending review 可长期滞留，失效端点关系只能在读取时被动隐藏。召回则由 Repository 取 owner/Profile 内最近 N 条，再由 Application 计算可解释文本分数，因此候选上限同时构成“旧记忆不可见”的时间窗口。

本变更跨越 Core、PostgreSQL、Server lifespan、配置和部署。约束是 Agent 仍只配置 URL/Token，公共工具不获得系统级维护权限，应用层不依赖 psycopg/MCP/runtime settings，维护失败不能使 MCP 停服，在线召回不能依赖模型可用性。

## Goals / Non-Goals

**Goals:**

- 将到期 revision、到期/超龄 pending review 和依赖失效端点的 relation 物化为明确终态。
- 维护操作批量、有界、幂等，并允许多个 Server worker 安全重放。
- 在 Server lifespan 内自动运行维护，优雅停止并输出无内容日志。
- 在数据库 owner/Profile/有效性边界内组合 trigram 词法候选和近期候选，找回较早但相关的记忆。
- 保留现有确定性排序、安全渲染、token budget 和 InMemory/PostgreSQL 契约等价性。

**Non-Goals:**

- 不物理删除记忆或 Evidence，不新增 suppression 语义。
- 不自动核验外部事实，不根据模型输出撤销或改写记忆。
- 不引入队列、独立 worker、Embedding、向量数据库或 LLM rerank。
- 不新增面向 Agent 的维护工具，不改变 Token、owner 或 Profile 路由契约。

## Decisions

### 1. 维护是独立应用用例，不伪装成读取副作用

Core 新增 `MemoryMaintenanceService`，通过扩展后的 `MemoryRepository` 执行一次系统级批量维护并返回只含计数和 `has_more` 的结果。它不接受 `PrincipalContext`，也不由 MCP tools 导出；系统级权限只存在于组合根创建的内部 runner。

Repository 在一个事务内锁定最多固定批次的目标：

1. `active/current` 且 `valid_until <= effective_at` 的 revision 变为 `expired`；
2. `pending` 且候选 `valid_until <= effective_at`，或创建时间早于固定保留窗口的 review 变为 `expired` 并记录 `decided_at`；
3. 连接本批失效 revision 的活动 relation 变为 `stale/endpoint_expired`。

PostgreSQL 使用 `FOR UPDATE SKIP LOCKED` 和带原状态条件的 UPDATE；InMemory 使用相同状态转换。重复执行计数为零，多个进程可以分批消费且不会覆盖 replacement/revoke/review confirm 的并发结果。

备选方案是在每次 list/recall 时顺便写回。该方案会让读取产生跨聚合副作用、增加尾延迟并难以区分权限，故拒绝。只提供手工 CLI 也无法形成用户无感的主动闭环。

### 2. Server lifespan 托管轻量周期 runner

Server 在 lifespan 启动一个 asyncio task；每轮通过 `asyncio.to_thread` 调用同步维护用例，随后等待 `MEMORY_MCP_MAINTENANCE_INTERVAL_SECONDS`。值为 `0` 时显式禁用，默认启用。runner 每轮只处理一个固定批次；`has_more=true` 时短间隔继续下一批，避免积压必须等待完整周期。关闭时设置 stop event、等待当前数据库调用结束，再关闭连接池。

维护异常记录结构化错误后进入下一周期，不改变 `/health` 和 MCP 服务可用性。迁移或数据库整体不可用仍由现有 health 检查反映。

备选方案是增加 systemd timer/独立 worker。当前负载没有隔离进程的证据，而且会增加部署制品、包和配置，故暂不采用。Repository 的并发语义保留未来拆分 runner 的可能性。

### 3. Pending review 增加明确的 `expired` 终态

`ReviewStatus.EXPIRED` 与 `rejected` 一样不产生 memory，必须具有 `decided_at` 且没有 `resolved_memory_id`。列表默认只读取 `pending`，确认/拒绝 expired review 统一返回不可用。保留原 review 和候选来源用于审计，不复用 `rejected`，因为“用户拒绝”和“系统超时”是不同事实。

### 4. Repository 负责混合候选，Application 负责最终相关性

Repository 新增专用 `find_recall_candidates`，输入可信 principal、Profile、query/task intent 合成的检索文本、可选 subject、effective time 和总上限。PostgreSQL 在同一 eligible CTE 中首先约束：

```text
owner → profile → current/active → valid window → optional subject
```

然后取两组互斥候选：约 70% 上限给 trigram lexical top-K，剩余容量由最新 revision 补齐。词法组使用 `pg_trgm` 的 subject/content GIN 索引和稳定的 similarity 排序；近期组按 `observed_at DESC, memory_id DESC`。最终返回不超过原 `MEMORY_MCP_RECALL_CANDIDATE_LIMIT`。

Application 仍计算短语包含、word overlap、中文字符二元组、subject、Profile priority/hints 和一跳 relation boost，并执行基础阈值、`max_items` 与 token budget。数据库 lexical score只负责候选生成，不成为最终可信分数，避免把 PostgreSQL 实现细节泄漏到领域 DTO。

InMemory adapter 使用等价 trigram 集合相似度与相同配额，保证单元测试表达行为而非退化成“返回全部记录”。

备选方案包括：

- 只提高最近候选上限：延迟和内存随数据增长，仍会遗漏更旧数据；
- PostgreSQL FTS：`simple` 配置对中文连续文本分词不足；
- Embedding/LLM rerank：需要新的模型、索引一致性、成本和故障处理，且当前评测尚未证明确定性混合候选不足。

### 5. 模型不是维护或召回的正确性依赖

现有模型继续负责 AfterRun candidate/relation extraction。维护只依据可信时间和当前持久化状态，召回只依据索引与确定性打分。本期不复用 chat model 做维护判断或 rerank；若后续投研评测证明语义缺口，再通过独立 `RecallReranker` port 增加可选、超时可降级的模型阶段，而不是直接耦合 `chat_models`。

### 6. 日志、指标和敏感边界

每轮维护只记录 duration、各状态转换计数、`has_more` 和 failure class；召回记录候选总数以及 lexical/recent 来源计数，但不记录 owner 明文、query、content、Evidence、Token 或数据库参数。内容日志仍受现有显式开关控制。

## Risks / Trade-offs

- [`pg_trgm` 创建权限不足] → migration 前置验证扩展；失败时旧版本 Server 可继续运行，不启动依赖新 schema 的版本。
- [长查询使整体 trigram similarity 稀释] → subject/content 分开比较、低而固定的候选阈值，并保留近期配额和应用层中文二元组排序。
- [多个 worker 同时维护] → `SKIP LOCKED`、条件 UPDATE、短事务和幂等终态；不使用进程内 leader 假设。
- [维护积压] → 固定批次、`has_more` 快速续批和计数日志；不允许一次事务扫描/更新无界集合。
- [状态物化与并发 replacement 冲突] → 锁定 current revision 后再次使用 active/current 条件更新；replacement/revoke 同样锁行，最终只允许一个合法状态转换。
- [模型提升机会被推迟] → 先用投研评测和真实容量数据确认失败样本，再引入可降级端口，避免当前生产链路不必要复杂化。

## Migration Plan

1. 发布并执行新 migration：安装 `pg_trgm`、扩展 review 状态约束、创建维护与 trigram 索引。
2. 运行 migration health 和 PostgreSQL 合同测试，确认扩展与索引存在。
3. 发布新 Server；默认 runner 开始分批物化历史到期数据，Agent 配置不变。
4. 观察维护计数、错误和 recall 候选日志；使用离线评测比较旧/新 Recall@K 与安全指标。
5. 新 runner 尚未写入 `expired` review 前可以短暂回滚应用；一旦物化该新终态，旧版枚举不再保证可读取这些 review，应关闭流量并前向修复。已物化的 expired revision 不回滚；若必须移除扩展，应先删除依赖索引，作为单独运维变更执行。

## Open Questions

无阻塞问题。是否增加 Embedding 或模型 rerank 由后续真实投研失败样本和延迟预算决定。
