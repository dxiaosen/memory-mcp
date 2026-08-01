## Why

当前系统能够分别保存投研论点、证据、风险、催化剂和长期事项，但这些记忆之间没有可持久化、可治理的联系；召回只能返回一组独立卡片，无法表达证据如何支持或挑战论点。Core 中预留的两套空关系字段也形成了未实现接口，需要在继续增加 Profile 前收敛为一个真实能力。

## What Changes

- 增加 owner-scoped、同 Profile、面向稳定 `MemoryItem` 的有向关系；关系具备稳定 ID、创建时间、活动/撤销状态和幂等语义。
- 用单一 `relation_policies` 映射声明关系名称、合法起点类型、合法终点类型和说明，由通用 Core 校验，具体词义只存在于 Profile。
- **BREAKING (Profile API)**：删除未被 Core 使用的 `allowed_relations` 与 `relation_rules`，自定义 Profile 改为实现 `relation_policies`；MCP 已有工具和普通 Agent 配置不受影响。
- 为投研 Profile 声明 evidence/thesis、risk/thesis、catalyst/thesis、research task/question 和 decision/question 等关系策略；`general-work` 继续不启用关系。
- 增加 PostgreSQL 关系目录与关系表、owner/profile/端点约束、活动关系唯一约束和 owner-first 索引；不引入图数据库或新运行服务。
- 增加创建关系与撤销关系 MCP 工具；公开参数继续不接受 owner，越权 ID 与不存在保持不可区分。
- 在记忆详情和召回结果中返回活动关系；召回只在已经 owner/profile/effective 过滤后的候选内使用一跳关系增强排序，不进行无界图遍历。
- 补齐进程内契约、PostgreSQL migration/Repository、MCP 权限与隔离、投研规则、召回预算、文档和回归测试。

## Capabilities

### New Capabilities

- `memory-relations`: 定义关系策略、owner-scoped 关系生命周期、关系工具、PostgreSQL 约束及关系感知召回。

### Modified Capabilities

无。当前 `openspec/specs/` 尚无已归档主规范，本变更以新的独立 capability 描述增量合同。

## Impact

- Core：领域模型、Profile port/registry、Repository port、MemoryService 和 RecallService。
- 存储：新增不可修改的 `0006_memory_relations.sql`，更新进程内与 PostgreSQL Repository。
- MCP：新增两个带现有 scope 的工具，扩展 detail/recall DTO；旧请求保持兼容。
- Profile：`general-work` 使用空策略，`investment-research` 启用正式关系词汇。
- Agent：地址、Token 和 Hook 配置不变；Hook 不增加独立模型调用或队列。
- 非目标：自动从模型输出创建关系、跨 owner/跨 Profile 关系、向量数据库、任意深度图遍历、物理删除关系。
