# Agent Lab Memory MCP

一个面向不同 Agent 的独立长期记忆服务。Agent 通过标准 MCP
Streamable HTTP 接入，身份、用户隔离、记忆准入和持久化都由服务端统一负责，
而不是绑定到某个 Agent 框架。

当前已完成阶段一至阶段三：

- owner-scoped Memory Core、版本化 SQLite 原型迁移和完整性检查；
- 结构化候选、自动保存/待确认/丢弃/敏感拦截四类准入；
- pending 确认与拒绝、失败重处理和 event 级幂等；
- 带 Bearer Token 鉴权的远程 MCP 服务；
- 六个 MCP 工具、稳定错误码、严格输入 DTO 和无正文审计日志；
- 真实 MCP Client、MCP Inspector 和跨用户隔离验收。

云化调整已经增加 PostgreSQL schema、Repository、连接池、独立 migration 命令
以及 Linux systemd/Nginx 部署样例。阶段四将在真实 PostgreSQL 上完成契约验收，
再增加生命周期合并和 `recall_memory`；阶段五增加平台无关的跨 Agent Hook SDK
和 ECS 公网部署；阶段六接入真实结构化模型并固化现场演示。

## 架构

```text
Codex / LangChain / self-hosted Agent / MCP client
              │  HTTPS MCP Streamable HTTP + Bearer Token
              ▼
       memory_mcp（身份与工具边界）
              │  trusted PrincipalContext
              ▼
       Memory Application Service
              │
      Domain / Ports / Policy
              │
              ▼
       PostgreSQL Repository
```

服务端不依赖阿里云百炼或其他 Agent 平台。任一支持远程 Streamable HTTP MCP
的 Host 都可以直接配置 URL 和认证信息；不支持生命周期 Hook 的 Host 可以通过
阶段五的 Bridge/Runner 接入。

调用方不能提交 `owner_id`。服务端只根据 token 映射生成
`owner_key + tenant_id + subject_id + client_id + scopes`，因此同一用户的不同
Agent 可以共享记忆，不同用户即使猜中 memory/review identifier 也无法读取。

## 当前 MCP 工具

| 工具 | Scope | 作用 |
| --- | --- | --- |
| `capture_completed_turn` | `memory:write` | 提交一个成功完成的对话轮次 |
| `list_memories` | `memory:read` | 列出当前用户的活动记忆 |
| `get_memory` | `memory:read` | 读取一条当前用户记忆 |
| `list_pending_reviews` | `memory:review` | 查看需要用户确认的候选 |
| `confirm_pending_memory` | `memory:review` | 确认并保存 pending 候选 |
| `reject_pending_memory` | `memory:review` | 拒绝 pending 候选 |

`capture_completed_turn` 使用 `contract_version=1` 和稳定 `event_id`。相同
event 与相同 payload 重试会返回 replay；相同 event 携带不同 payload 会返回
`idempotency_conflict`。

## 本地快速启动

项目要求 Python 3.14，使用 [uv](https://docs.astral.sh/uv/) 管理环境。

```powershell
Copy-Item .env.example .env
# 本地过渡测试可以把 .env 中的 STORAGE_BACKEND 改为 sqlite。
# ECS 正式环境使用 postgresql。
uv sync
uv run memory-mcp
```

把 `.env` 中的演示 token 替换为本地随机值后，服务默认提供：

- MCP：`http://127.0.0.1:8765/mcp`
- 健康检查：`http://127.0.0.1:8765/health`

另一个终端可使用最小真实客户端验证：

```powershell
uv run python examples/memory_mcp_client.py `
  --token replace-user-a-agent-a-token tools

uv run python examples/memory_mcp_client.py `
  --token replace-user-a-agent-a-token memories
```

阶段三的默认命令行组合根暂不绑定某一家模型，因此服务可以启动、鉴权并使用
管理工具；捕获工具在未注入 `CandidateExtractor` 时会明确返回
`capture_not_configured`。阶段三的端到端测试通过固定离线 extractor 重放所有
准入分支，阶段六再把真实模型 backend 接到默认启动入口。

## PostgreSQL 与 ECS 部署

PostgreSQL migration 必须在启动服务前显式执行：

```bash
uv run memory-db migrate
uv run memory-db health
uv run memory-mcp
```

ECS 默认使用 `uv + systemd`，不要求 Docker。HTTPS 可以由同机 Nginx、
阿里云 ALB/CLB 或其他可信代理终止；如果已经使用云负载均衡，服务器不需要
安装 Nginx。完整步骤见
[阿里云 ECS 远程 MCP 部署](docs/deployment/aliyun-ecs.md)。

## 配置

所有服务配置使用 `MEMORY_MCP_` 前缀。关键字段：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_STORAGE_BACKEND` | `sqlite` | 过渡默认；ECS 设置为 `postgresql` |
| `MEMORY_MCP_DATABASE_URL` | 无 | PostgreSQL DSN，按 secret 处理 |
| `MEMORY_MCP_DATABASE_POOL_MIN_SIZE` | `1` | PostgreSQL 最小连接数 |
| `MEMORY_MCP_DATABASE_POOL_MAX_SIZE` | `5` | PostgreSQL 最大连接数 |
| `MEMORY_MCP_DATABASE_MIGRATE_ON_STARTUP` | `false` | ECS 应保持关闭并独立迁移 |
| `MEMORY_MCP_DATABASE_PATH` | `.agent-lab/memory.db` | 仅用于过渡 SQLite 测试 |
| `MEMORY_MCP_HOST` | `127.0.0.1` | 监听地址 |
| `MEMORY_MCP_PORT` | `8765` | 监听端口 |
| `MEMORY_MCP_MCP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `MEMORY_MCP_DEMO_TOKENS_JSON` | `{}` | token 到可信 principal 的映射；为空时拒绝启动 |
| `MEMORY_MCP_SCENARIO_ID` | `project-work` | 当前注册场景 |
| `MEMORY_MCP_MAX_CAPTURE_CHARACTERS` | `100000` | 单轮最大字符数 |

演示 token 映射仅用于课题原型。公网演示必须使用 HTTPS、高熵随机 Token 和受限
安全组，但仍不得把它描述为生产 OAuth。生产化时应替换为标准授权服务器和正式
TokenVerifier。

## 开发与验收

```powershell
uv run pytest
uv run ruff format --check .
uv run ruff check .
openspec-cn validate add-general-memory-core --strict
```

阶段三架构、身份、工具和数据流见
[阶段三详细设计](docs/memory/phase-three-design.md)，实测结果见
[阶段三验收记录](docs/memory/phase-three-acceptance.md)。完整实施顺序以
[OpenSpec tasks](openspec/changes/add-general-memory-core/tasks.md) 为准。

日志不会记录对话正文、候选正文、记忆正文、Bearer Token 或 API Key，只记录
request id、工具名、耗时、结果数量和不可逆稳定引用。
