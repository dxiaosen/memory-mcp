# Memory Core

`memory_mcp.core` 是独立于 Agent 框架、MCP transport 和具体业务场景的通用
记忆核心。

依赖方向固定为：

```text
application → domain / ports
adapters    → domain / ports
composition → application / adapters / ports
场景插件    → ports
application / adapters → 共享 logging
```

Core 不得导入投资假设、调研问题等正式场景模块。场景只能通过
`ScenarioPolicy` 声明类型、业务进展、允许关系、捕获提示和召回优先级，
不能改变所有权、来源、版本和通用有效状态。

`domain` 和 `ports` 不读取环境变量，也不依赖 Agent、LangChain、MCP 或数据库
驱动。部署组合根通过 `MemoryServerSettings` 读取 PostgreSQL secret，应用层和
适配器只依赖共享的结构化日志接口。真实数据库契约通过后，SQLite 原型 adapter
已经删除；PostgreSQL 是远程 MCP 的唯一运行后端。顶层 `memory_mcp` 包保持轻量，
因此导入本模块不会主动连接数据库或加载 Agent 平台。

当前已提供结构化候选抽取、敏感预检、四类准入、source turn 幂等、pending
确认/拒绝、确定性 duplicate/replacement/history 和 owner-first 文本召回。
当前仍不包含复杂关系演进、Embedding/向量检索或 Agent Hook 集成。

详细说明：

- `docs/memory/implementation-baseline.md`
- `docs/architecture.md`
- `docs/deployment/aliyun-ecs.md`
