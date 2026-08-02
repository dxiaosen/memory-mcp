## Why

当前系统能在读取时过滤过期记忆，也能在单次 capture 中处理 duplicate、replacement、review 和 revoke，但缺少自动物化到期状态、清理失效 review/关系的服务端维护闭环。召回则先按时间截断候选再做应用层文本排序，长期有效但较早的记忆可能永远无法进入候选集。

本变更在不增加 Agent 配置、不引入消息队列的前提下，把生命周期维护变成幂等、可观测的服务端能力，并把召回升级为数据库词法候选与近期候选的混合检索，为后续投研证据维护和更大数据量提供稳定基础。

## What Changes

- 增加可并发重放的批量维护用例：物化到期 current revision、终止到期 pending review，并让依赖失效端点的活动关系变为 stale。
- Server 在 lifespan 内按固定周期异步触发维护；同步数据库工作在线程中执行，关闭时可干净停止，不要求 Agent、Hook 或外部队列参与。
- 为维护周期提供有界配置，把批次和 pending review 最大保留时间固化为安全策略，并记录不含业务内容的结构化运行日志。
- 将 PostgreSQL 召回候选升级为 owner/Profile/生命周期约束内的“近期 + 词法”并集，使用索引支持的 trigram 相似度找回较早但相关的记忆。
- 保留确定性应用层排序、关系加权、相关性阈值和 token budget；模型不进入正确性关键路径，现有抽取模型故障也不影响维护与召回。
- 增加数据库迁移、Repository 契约、InMemory 等价实现、容量/生命周期测试，以及设计、配置、部署、测试和评测文档。

## Capabilities

### New Capabilities

- `memory-maintenance`: 定义服务端周期维护、过期状态物化、pending review 终止、关系失效、并发幂等和可观测性。
- `hybrid-memory-recall`: 定义 owner-first 的近期/词法混合候选、数据库边界、确定性排序、降级与容量约束。

### Modified Capabilities

无。主规范尚未归档，本变更以独立增量能力描述新增行为。

## Impact

- Core：新增维护结果与应用服务，扩展 Repository 端口和召回候选端口。
- PostgreSQL：新增 `pg_trgm` 扩展、检索索引、review 状态约束和维护索引；migration 必须先于新版本 Server 发布。
- Server：增加内部维护 runner 和少量有界环境变量；公共 MCP 工具、认证模型及 Agent URL/Token 配置保持不变。
- 测试与评测：覆盖到期闭环、并发重放、旧记忆召回、owner/Profile 隔离和候选上限。
- 非目标：本变更不做物理删除、自动事实核验、Embedding/向量库、LLM rerank、消息队列或跨用户维护权限。
