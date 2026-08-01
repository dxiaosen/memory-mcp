## Why

当前通用记忆闭环可以保存偏好、稳定上下文、长期事项和决定，但无法稳定区分投研中的研究问题、论点、证据、风险和催化剂。把这些词义写入 Memory Core 会破坏通用边界，因此需要以正式 MemoryProfile 扩展首个垂直领域，同时复用现有的 owner 隔离、来源追踪、冲突替代和生命周期能力。

## What Changes

- 新增正式 `investment-research` Profile，声明投研原子记忆类型、捕获说明、业务进展、召回优先级和逐类型元数据策略。
- 将投研“观点”和“外部事实”明确分开；模型抽取置信度不作为事实真实性，外部材料仍需结构化 Evidence 和验证状态。
- 对易过时的证据、风险、催化剂、论点和研究事项配置保守有效期；研究偏好与明确研究决定默认不自动过期。
- 服务启动时同时注册通用工作和投研 Profile；现有默认仍是 `general-work`，专用投研集成在内部显式选择 `investment-research`，不根据会话正文猜测或切换 Profile。
- 为投研捕获、冲突待确认、来源保存、到期召回和通用 Core 边界增加测试与使用示例。
- 不新增交易执行、投资建议、真实持仓记忆、行情/研报采集、文档知识库、关系图或向量数据库。

## Capabilities

### New Capabilities

- `investment-research-memory`: 定义投研 Profile 的合法记忆类型、语义边界、有效期策略、捕获与召回行为。

### Modified Capabilities

无。现有捕获、准入、生命周期和召回合同保持不变；本变更只提供一套新的正式 Profile 配置。

## Impact

- 新增 `memory_mcp.profiles.investment_research`，并调整 Server 默认 Profile 注册组合。
- 更新通用结构化模型提示，但不改变 Candidate schema、公开 MCP 工具名称、认证模型或 PostgreSQL 表结构。
- 新 Profile 会通过现有 `memory_profiles` / `memory_profile_types` 注册，不需要新的数据库 migration。
- Agent 与直接 MCP 调用方继续只配置同一个服务地址和 Token；只有投研专用集成代码负责选择 Profile，普通用户无需从文本中指定场景。
