# Memory MCP 架构概览

## 1. 产品边界

Memory MCP 是一个独立、可公网接入的长期记忆服务，不是某个 Agent 进程内
的插件。Codex、LangChain、自研 Agent 或其他兼容 Host 可以直接连接同一个
Streamable HTTP MCP 端点。

阿里云百炼等托管平台只是可选客户端，不是服务端依赖。MCP 连接负责提供工具；
确定性的主动召回和捕获由 Agent Hook 或外层 Runner 保证。

## 2. 运行拓扑

```text
Agent Host A ── BeforeRun/AfterRun Hook ─┐
Agent Host B ── MCP Client / Runner ─────┤
                                        │ 私网 HTTP 或公网 HTTPS
                                        ▼
                              Memory MCP Server
                       Transport / Auth / Tool / DTO
                                        │
                                        ▼
                       Memory Application / Domain / Ports
                                        │
                                        ▼
                         PostgreSQL Repository + migrations
                                        │
                                        ▼
                         RDS PostgreSQL（VPC 私网）
```

默认 Linux 部署使用 `uv + systemd`，不要求 Docker 或反向代理。同一可信私网
内直接访问服务地址；公网接入时由云负载均衡器终止 HTTPS，并只允许其访问 ECS
私网监听端口。

## 3. 模块和依赖方向

```text
memory_mcp.server ────────> core.application ───────> core.domain / ports
        │                          ▲                         ▲
        │                          │                         │
        ├── Auth / MCP DTO         ├──── PostgreSQL adapter ─┘
        └── extraction factory ────┘

memory_hooks ─────> remote MCP only
Agent runners ─────> memory_hooks
ScenarioPolicy ───> core ports
```

必须保持的边界：

- `memory_mcp.core.domain`、`application` 和 `ports` 不依赖 MCP、HTTP、Agent SDK、
  PostgreSQL 驱动或配置模块；
- `memory_mcp.server` 只通过应用服务和公开端口使用 Memory Core；
- `memory_hooks` 只访问远程 MCP，不导入 Repository；
- `server` 和 adapter package 初始化不做完整 app/driver 的便利 re-export；
- 数据库维护入口位于顶层 `database_cli.py`，Core adapter 不反向读取 Server
  Settings；
- Agent Client 不直接访问 PostgreSQL；
- `owner_id` 不属于 MCP 工具入参，只能由服务端认证上下文构造；
- 场景策略实现依赖 `ScenarioPolicy`，Core 不反向导入正式场景；
- secret 只在组合根和基础设施边界读取，不进入领域对象或日志。

## 4. 存储边界

PostgreSQL 是部署环境唯一权威存储，负责：

- owner-scoped 记忆、revision 和 Evidence；
- capture event 幂等与无正文 outcome；
- pending review 及原子确认/拒绝；
- registered scenario/type、单一 current revision 和跨表 owner 约束；
- duplicate Evidence、同一 MemoryItem 的 replacement revision 和 history 事务；
- owner-first active/current recall 候选查询。

阶段一至三曾用 SQLite 原型验证行为。真实 PostgreSQL Repository、migration
和 MCP 重启测试通过后，该 adapter、migration 与专项测试已经删除；运行时只有
PostgreSQL 一个权威 Repository。`InMemoryMemoryRepository` 仅保留为快速单元
测试替身。

本期不实现 Embedding、向量数据库或独立搜索引擎。如果未来增加二级检索索引，
它只能提出候选；返回 Agent 前必须回 PostgreSQL 重新验证 owner、current
revision 和 lifecycle status。

## 5. 身份和隔离

```text
Authorization credential
        │
        ▼
tenant_id + subject_id ──> owner scope
client_id + agent_id ─────> calling actor / audit
scopes ───────────────────> read / write / review
```

同一用户的不同 Agent 可以映射到相同 owner；同一 Agent 服务不同用户时必须映射
到不同 owner。共享服务级 Token 如果无法携带可信终端用户身份，只能被描述为
单 owner 原型，不能宣称多用户隔离。

当前环境变量 Token 映射仅用于课题原型，不等同于生产 OAuth。

## 6. 配置边界

| 配置组 | 内容 | secret |
| --- | --- | --- |
| MCP transport | host、port、`/mcp` | 否 |
| PostgreSQL | database URL、pool、connect timeout | 是 |
| 原型认证 | Token → Principal 映射 | 是 |
| 场景策略 | 代码注册的 `GeneralWorkPolicy` | 否 |
| 候选抽取 | fixed JSON 或 provider、model、API key | JSON/API key 是 |
| Hook Client | MCP URL、Token、fail-open | Token 是 |

数据库 migration 是独立发布步骤。ECS 默认保持
`MEMORY_MCP_DATABASE_MIGRATE_ON_STARTUP=false`，由一次性 systemd unit 执行。

## 7. 运行与可观测性

服务日志可以记录 request/event/capture 的稳定引用、工具名、client、状态、数量、
耗时和错误码，不得记录：

- Bearer Token、数据库 URL 或模型 API Key；
- 用户输入、Agent 输出、记忆正文或来源原文；
- backend 异常消息；
- 被敏感规则拦截的原始内容。

同步 Core、模型和 Repository 调用在 worker thread 执行，不直接阻塞 MCP 事件
循环。`/health` 验证连接、必需表、migration 和 checksum，只返回非敏感运行
元数据。完整日志规则见
[项目执行日志设计](logging.md)。

## 8. 实施顺序

1. 通用契约、来源、owner 隔离和 SQLite 原型（已完成）；
2. 捕获、四类准入、敏感边界和 pending（已完成）；
3. 远程 MCP、可信认证、管理工具和跨 Agent 隔离（已完成）；
4. PostgreSQL 正式后端、最小生命周期和主动召回（已完成）；
5. Hook SDK、两个 Agent profile、真实/固定结构化抽取和本地 PostgreSQL
   闭环（已完成）；
6. 公网 HTTPS、真实远端网络、压测、现场脚本、录屏和交付。

完整设计和任务状态以
[`add-general-memory-core`](../openspec/changes/add-general-memory-core/design.md)
为事实源。部署步骤见
[阿里云 ECS 远程 MCP 部署](deployment/aliyun-ecs.md)。
阶段五整体说明见[整体设计](design.md)、[配置参考](configuration.md)、
[测试说明](testing.md)和[端到端使用](usage.md)。
