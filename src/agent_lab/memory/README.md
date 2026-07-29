# Memory Core

`agent_lab.memory` 是独立于 Agent 框架和具体业务场景的通用记忆核心。

依赖方向固定为：

```text
application → domain / ports
adapters    → domain / ports
composition → application / adapters / ports
场景插件    → ports
application / adapters → 共享 observability
```

Core 不得导入投资假设、调研问题等正式场景模块。场景只能通过
`ScenarioPolicy` 声明类型、业务进展、允许关系、捕获提示和召回优先级，
不能改变所有权、来源、版本和通用有效状态。

`domain` 和 `ports` 不读取环境变量，也不依赖 Agent、LangChain 或日志实现。
SQLite 可执行入口通过 `MemorySettings` 读取 `.env`，应用层和适配器只依赖
共享的结构化日志接口。顶层 `agent_lab` 包保持轻量，因此导入本模块不会加载
其他功能模块。

阶段一只提供手动创建、列表和详情读取，不包含模型抽取、关系演进、
语义召回或 Agent 集成。
