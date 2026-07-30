# Agent Lab Memory MCP 架构概览

## 1. 产品边界

Agent Lab 是一个独立、可公网接入的长期记忆 MCP 服务，不是某个 Agent 进程内
的插件。Codex、LangChain、自研 Agent 或其他兼容 Host 可以直接连接同一个
Streamable HTTP MCP 端点。

阿里云百炼等托管平台只是可选客户端，不是服务端依赖。MCP 连接负责提供工具；
确定性的主动召回和捕获由 Agent Hook 或外层 Runner 保证。

## 2. 运行拓扑

```text
Agent Host A ── BeforeRun/AfterRun Hook ─┐
Agent Host B ── MCP Client / Runner ─────┤
                                        │ HTTPS + Bearer Token
                                        ▼
                          TLS termination / reverse proxy
                                        │
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

默认 Linux 部署使用 `uv + systemd`，不要求 Docker。HTTPS 可以由同机 Nginx、
云负载均衡或其他可信代理终止；如果已有 ALB/CLB，ECS 不需要安装 Nginx。

## 3. 模块和依赖方向

```text
memory_mcp ───────────────> memory.application ─────> memory.domain / ports
      │                            ▲                         ▲
      │                            │                         │
      └── Auth / MCP DTO           └──── PostgreSQL adapter ─┘

memory_hooks ─────> remote MCP only
Agent adapters ───> memory_hooks
ScenarioPolicy ───> memory ports
```

必须保持的边界：

- `memory.domain`、`application` 和 `ports` 不依赖 MCP、HTTP、Agent SDK、
  PostgreSQL 驱动或配置模块；
- `memory_mcp` 只通过应用服务和公开端口使用 Memory Core；
- `memory_hooks` 只访问远程 MCP，不导入 Repository；
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
- 后续 duplicate、replacement 和 history 事务。

阶段一至三的 SQLite adapter 是已验证原型。迁移期间保留它作为行为基线；实际
PostgreSQL Repository、migration 和 MCP 重启测试通过后删除，不长期维护两套
生产持久化实现。`InMemoryMemoryRepository` 保留为快速单元测试替身。

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
| MCP transport | host、port、`/mcp`、timeout | 否 |
| PostgreSQL | database URL、pool、connect timeout | 是 |
| 原型认证 | Token → Principal 映射 | 是 |
| 场景策略 | scenario、类型、policy version | 否 |
| 结构化模型 | provider、model、API key | API key 是 |
| Hook Client | MCP URL、Token、fail-open | Token 是 |

数据库 migration 是独立发布步骤。ECS 默认保持
`MEMORY_MCP_DATABASE_MIGRATE_ON_STARTUP=false`，由一次性 systemd unit 执行。

## 7. 运行与可观测性

服务日志可以记录 request/event/capture 的稳定引用、工具名、client、状态、数量、
耗时和错误码，不得记录：

- Bearer Token、数据库 URL 或模型 API Key；
- 用户输入、Agent 输出、记忆正文或来源原文；
- 被敏感规则拦截的原始内容。

`/health` 验证当前存储连接和 schema，只返回非敏感运行元数据。完整日志规则见
[项目执行日志设计](logging.md)。

## 8. 实施顺序

1. 通用契约、来源、owner 隔离和 SQLite 原型（已完成）；
2. 捕获、四类准入、敏感边界和 pending（已完成）；
3. 远程 MCP、可信认证、管理工具和跨 Agent 隔离（已完成）；
4. PostgreSQL 正式后端、最小生命周期和主动召回；
5. Hook SDK、平台无关的两个 Agent Client 和 Linux ECS 部署；
6. 真实结构化模型、云端验收、脚本、录屏和交付。

完整设计和任务状态以
[`add-general-memory-core`](../openspec/changes/add-general-memory-core/design.md)
为事实源。部署步骤见
[阿里云 ECS 远程 MCP 部署](deployment/aliyun-ecs.md)。
