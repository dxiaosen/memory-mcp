## Why

内置记忆策略已经多次演进，但 Profile 版本仍停留在初始版本，捕获记录也无法证明实际采用的完整策略内容；与此同时，当前幂等键包含 Profile 版本，版本升级后同一事件的延迟重试可能被重复处理。Agent 侧还会默认显式发送 `general-work`，使服务端无法仅凭受信 Token 为不同用户或 Agent 选择合适的默认策略，投研 Profile 因而不能真正做到“只配置地址和 Token 即可使用”。

Recall 评测目前复用了内部相关性函数而非生产召回入口，数据库召回也会一次加载 owner/Profile 下全部有效记忆。现在需要在继续扩展场景前补齐策略审计、透明路由、真实链路评测和最基本的查询边界。

## What Changes

- 为内置 Profile 增加确定性策略指纹，升级其版本，并把 Profile 版本、策略指纹和 Prompt 版本共同记录到捕获审计数据中；启动时拒绝“版本未变但策略内容已漂移”的内置配置。
- 重定义捕获幂等语义：显式 `event_id` 在同一 owner 内跨 Profile 版本保持唯一；无 `event_id` 的兼容路径按 owner、Profile、会话和来源轮次唯一，Profile 升级不再制造重复捕获。
- 在受信认证主体上配置默认 Profile。MCP 工具仍允许高级调用方显式指定 Profile，但轻量 Agent 默认不再发送该字段，仅凭服务地址和 Token 使用服务端策略。
- 让 Recall 评测通过公开的生产召回服务执行，覆盖阈值、生命周期、关系扩展、预算与最终排序，而不是直接调用私有打分函数。
- 为生产召回候选查询增加可配置硬上限并下推到仓储层，避免单个 owner 的记忆增长导致无界读取；该上限是当前原型的保护边界，不替代后续向量或全文索引。
- 增加 PostgreSQL 迁移并同步设计、配置、Agent 接入、测试和评测文档。

## Capabilities

### New Capabilities

- `policy-routing-recall-hardening`: 定义可审计的策略版本、跨版本捕获幂等、认证主体默认 Profile 路由、生产链路 Recall 评测以及有界候选查询。

### Modified Capabilities

无。

## Impact

- 影响 Profile 注册与配置、捕获服务和审计模型、认证主体、MCP capture/recall 工具、Agent hook 客户端、Recall 服务与仓储端口、PostgreSQL schema、评测器和相关文档。
- PostgreSQL 需要执行一次向前迁移：补充策略指纹列并重建捕获幂等约束。迁移不删除记忆数据；若历史库已经存在违反新逻辑唯一性的重复事件，迁移会明确失败并要求先处理数据。
- Agent 环境变量 `MEMORY_HOOK_PROFILE_ID` 保留为高级覆盖项，但不再是默认必填或默认发送项。静态 Token 配置新增可选 `default_profile_id`，未配置时保持 `general-work` 兼容行为。
- 不引入 OAuth、外部任务队列、向量数据库、全文检索扩展，也不改变 owner 必须来自认证上下文的安全边界。
