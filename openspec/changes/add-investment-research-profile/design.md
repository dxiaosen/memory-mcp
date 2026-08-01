## Context

Memory Core 已经通过 `MemoryProfile` 把业务词义隔离在 `memory_mcp.profiles`，并统一负责可信 owner、候选准入、Evidence、冲突/替代、待确认、有效期和召回。当前唯一正式配置 `general-work` 只有通用工作类型，模型无法可靠表达一条内容是研究论点、外部证据、风险还是催化剂。

投研信息比普通事项更容易过时，也更容易把用户观点、模型推断和外部事实混为一谈。本变更依赖 `enhance-memory-metadata` 提供的 verification、sensitivity、validity 和 citation 字段，但不改变 Core 的依赖方向或数据库结构。

## Goals / Non-Goals

**Goals:**

- 用一个正式 `InvestmentResearchProfile` 表达投研原子记忆类型和抽取边界。
- 在不改 Core 分支逻辑的前提下为不同投研类型声明召回优先级、敏感级别和默认有效期。
- 让相同 owner、Profile、subject、type 的重复、冲突和明确替代继续走统一生命周期。
- 让 Server 默认注册通用工作与投研两套配置，使 MCP 调用和专用 Agent 集成可以显式选择。
- 用测试证明投研扩展没有向 Core、Agent Host 或 PostgreSQL Repository 泄漏领域词义。

**Non-Goals:**

- 不根据自然语言自动猜测或切换 `profile_id`。
- 不提供买卖建议、下单、组合管理或真实持仓记忆；现有敏感守卫继续阻断这些内容。
- 不抓取行情、研报或网页，不把 Memory MCP 变成文档知识库。
- 不新增关系表、知识图谱、embedding、向量数据库或异步任务队列。
- 不在本阶段实现 Profile 管理 API、用户自定义 Profile DSL 或逐条有效期编辑工具。

## Decisions

### 1. 使用单个投研 Profile，而不是按研究阶段拆多个 Profile

Profile 标识固定为 `investment-research`，包含以下八种原子类型：

| memory type | 含义 | 推荐 subject 粒度 |
| --- | --- | --- |
| `research_preference` | 用户长期研究方法、输出格式、来源偏好 | 方法或输出维度 |
| `research_question` | 需要跨会话继续回答的关键问题 | 实体/主题 + 问题焦点 |
| `thesis` | 用户明确提出的可证伪研究论点 | 实体/主题 + 论点焦点 |
| `evidence_claim` | 带时间和来源边界的外部证据主张 | 实体 + 指标/事件 + 期间 |
| `risk` | 可能推翻论点或改变判断的重要风险 | 实体/主题 + 风险因子 |
| `catalyst` | 值得持续观察的未来事件或触发因素 | 实体/主题 + 事件 |
| `ongoing_research` | 跨会话研究任务、缺口和后续动作 | 实体/主题 + 工作项 |
| `research_decision` | 研究范围、口径或结论选择，不是交易指令 | 实体/主题 + 决策焦点 |

选择单一 Profile 可以在同一 owner 的一次研究任务中统一召回论点、证据和风险，同时仍以 memory type 做排序和生命周期分组。拆成“假设 Profile”“问题 Profile”会迫使客户端跨 Profile 聚合，而当前 Core 有意保持单 Profile 查询。

### 2. 用类型语义区分观点和外部证据，不新增投研专属字段

`thesis` 通常使用 `user_view`；`evidence_claim` 使用 `external_fact`，并通过通用 Evidence 保存文档/网页/工具来源。模型抽取的 `confidence` 只表示抽取质量，不能把 `evidence_claim` 自动标为 `source_verified`。来自非用户消息的候选继续进入 pending，用户确认后成为 `user_confirmed`，而不是被错误表示为来源已核验。

选择复用 `AssertionKind`、verification 和 Evidence，是为了让事实边界对所有后续垂直 Profile 都一致。新增 `ticker`、`price`、`target_price` 等字段会把 Core 变成投研数据模型，也无法覆盖宏观、行业和非上市实体研究。

### 3. 通过 capture guidance 约束原子性和 subject，不在 Core 按类型分支

Profile guidance 要求候选一次只表达一个可独立替代的命题，并为 subject 使用稳定的实体/主题与焦点组合。同一家公司不同指标或期间的证据不得共用过粗 subject，否则统一冲突算法会把并存事实误判为替代。

这些规则通过 Profile 传给结构化模型，并由现有 allowed type、原文 Evidence、schema 和准入检查共同约束。Core 不出现 `investment-research`、`thesis` 或 ticker 特判。

### 4. 对易陈旧类型使用保守的读取时有效期

默认策略为：

| memory type | sensitivity | validity |
| --- | --- | --- |
| `research_preference` | `confidential` | 不自动过期 |
| `research_question` | `confidential` | 365 天 |
| `thesis` | `confidential` | 180 天 |
| `evidence_claim` | `internal` | 90 天 |
| `risk` | `confidential` | 180 天 |
| `catalyst` | `internal` | 90 天 |
| `ongoing_research` | `confidential` | 365 天 |
| `research_decision` | `confidential` | 不自动过期 |

到期只影响普通 list/recall，不修改 revision，不删除 Evidence，因此审计和显式详情仍可访问。固定时长是当前 Profile 契约能稳定表达的最小策略；按财报期、事件日期或来源类别动态计算期限留待有失败案例后扩展。

### 5. 业务进展使用有限公共词表

Profile 允许 `open`、`monitoring`、`resolved`、`invalidated`、`archived`。它主要服务研究问题、论点、风险、催化剂和研究事项；抽取 guidance 要求无明确进展时保持 null。关系声明保持为空，因为当前 Core 尚未持久化或执行关系规则，预先声明未生效关系会误导调用方。

### 6. Server 注册两个 Profile，选择仍由可信集成边界显式完成

默认组合根注册 `GeneralWorkProfile` 和 `InvestmentResearchProfile`。公开工具保留 `general-work` 默认值和显式 `profile_id`；通用 Hook 不从正文判断场景。投研产品集成在构建 Hook 上下文时固定选择 `investment-research`，终端使用者仍只需要服务地址和 Token。

选择显式集成而不是正文分类，可以避免一次普通对话意外切换存储语义，也保持 capture/recall 使用同一个 Profile。未来若需要单服务按 Token 绑定默认 Profile，应作为认证合同的独立变更实现。

## Risks / Trade-offs

- **[八种类型对模型过细]** → guidance 给出互斥定义并要求宁可不抽取；用固定离线候选和真实模型契约测试观察混淆率。
- **[固定有效期与真实事件周期不一致]** → 到期不删除数据，详情和历史仍可审计；后续基于实际失败案例增加重新验证或显式期限。
- **[subject 过粗导致错误冲突]** → guidance 和文档给出稳定粒度，测试同实体不同指标可以并存、同一原子命题冲突进入 pending。
- **[用户观点被误当事实]** → 保留 `AssertionKind`、verification 和来源元数据，召回渲染继续标注验证状态。
- **[投研内容含真实持仓或交易指令]** → Profile 不降低敏感守卫优先级，命中禁止规则时仍在持久化前 blocked。
- **[默认注册增加数据库类型行]** → 注册使用幂等 upsert；回滚代码不会删除已登记 Profile 或历史记忆，避免破坏数据。

## Migration Plan

1. 先部署并执行 `enhance-memory-metadata` 的 `0004` migration。
2. 发布包含 `InvestmentResearchProfile` 的 Server；启动时幂等登记 Profile 和八种类型。
3. 先用显式 MCP 参数或投研集成固定值做灰度捕获/召回，观察 pending、blocked、类型混淆和到期行为。
4. 回滚时恢复旧 Server 即可；已有投研记忆留在 PostgreSQL，但旧进程不会注册或查询该 Profile。再次升级后可恢复访问。

## Open Questions

- 真实投研样本是否证明需要把公司、行业、宏观主题拆为结构化 subject 字段？当前先使用规范化字符串。
- 是否需要独立的 `source_verified` 人工/自动核验工具？当前不因存在引用而自动核验。
- 是否需要把 Profile 默认值绑定到 Token 或租户？当前由投研集成显式选择，避免扩大认证合同。
- 关系图、定期重新验证和自动过期状态写回，留到有检索质量与运营需求后再设计。
