# Agent Lab 架构概览

## 1. 当前系统由什么组成

项目目前包含两条可以独立运行的能力线：

- 知识库 Agent：完成文档索引、Chroma 检索和多轮问答；
- 通用 Memory Core：完成场景契约、可信记忆卡片、owner 隔离和 SQLite
  持久化，阶段一尚未接入 Agent Runtime。

二者共享配置与日志基础设施，但 Memory Core 不依赖 LangChain、聊天模型或
Embedding 服务。

## 2. 模块和依赖方向

```text
index/chat CLI
    │
    ▼
Bootstrap ───────> Agents / Knowledge ───────> Integrations
                                              (模型、Embedding、Chroma)

Memory composition
    │
    ▼
Memory application ──────> Memory domain / ports <────── Adapters
                                  ▲                        (SQLite)
                                  │
                         ScenarioPolicy implementations

各可执行入口 ──────> Config
Application / Adapters ─> Observability
```

必须长期保持的边界：

- 顶层 `agent_lab` 包不主动导入任何功能模块；
- `memory.domain` 和 `memory.ports` 不依赖 Agent、配置、日志或第三方基础设施；
- 场景插件依赖 Memory Core 定义的 `ScenarioPolicy`，Core 不反向导入场景；
- 环境变量只在可执行入口通过 Settings 读取，业务模块接收已经构造好的对象；
- SDK Client 只在 `integrations` 和 `bootstrap` 中创建；
- SQLite 只能通过 Repository adapter 访问，Agent 和场景插件不能直接写 SQL。

## 3. 配置边界

配置按实际运行入口拆分，避免无关凭据阻塞本地任务：

| 入口 | 配置类型 | 必需能力 |
| --- | --- | --- |
| `agent-lab index` | `KnowledgeSettings` | Embedding、Chroma、日志 |
| `agent-lab chat` | `AgentSettings` | Chat、Embedding、Chroma、日志 |
| Memory migrate/health | `MemorySettings` | SQLite 路径、日志 |
| Memory 离线演示 | `LoggingSettings` | 日志 |

`Settings` 仍是 `AgentSettings` 的兼容名称，但新入口应选择需求最小的配置类型。

## 4. 数据存储

- Chroma 是知识文档向量索引，不是通用记忆的权威存储；
- SQLite 是当前 Memory Core 原型的权威存储，默认路径为
  `.agent-lab/memory.db`；
- Memory Repository port 保持存储无关。只有出现多人高并发、独立服务部署或
  生产级数据库授权需求时，才评估迁移 PostgreSQL。

SQLite 的详细表结构、迁移和安全边界见
[Memory Core 阶段一详细设计](memory/phase-one-design.md)。

## 5. 运行与可观测性

所有可执行入口使用同一日志模块，输出终端日志和滚动文件日志。业务事件使用
稳定事件名和结构化字段；问题正文、回答正文、记忆内容、来源原文与 API Key
不得写入日志。详情见[项目执行日志设计](logging.md)。

## 6. 演进顺序

Memory Core 按 OpenSpec 五个阶段演进：

1. 通用契约、可信记忆卡片和用户隔离（已完成）；
2. 通用捕获、准入和待确认流程；
3. 通用生命周期、主动召回和用户治理；
4. 投资假设场景插件；
5. 调研问题场景、扩展性验证和交付评测。

阶段三通过后才算通用能力闭环完成；阶段四、五用于实现具体场景并验证扩展性。
具体任务与阶段停止条件以
[`add-general-memory-core`](../openspec/changes/add-general-memory-core/design.md)
变更为准。
