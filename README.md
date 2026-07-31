# Memory MCP

一个面向不同 Agent 的独立长期记忆服务。Agent 通过标准 MCP
Streamable HTTP 接入，身份、用户隔离、记忆准入和持久化都由服务端统一负责，
而不是绑定到某个 Agent 框架。

当前已完成阶段一至阶段五的本地代码实现：

- owner-scoped Memory Core、版本化 PostgreSQL migration 和完整性检查；
- 结构化候选、自动保存/待确认/丢弃/敏感拦截四类准入；
- pending 确认与拒绝、失败重处理和 event 级幂等；
- 带 Bearer Token 鉴权的远程 MCP 服务；
- 七个 MCP 工具、稳定错误码、严格输入 DTO 和无正文审计日志；
- `GeneralWorkPolicy`、duplicate Evidence、同一 MemoryItem 的 replacement revision；
- owner-first `recall_memory`、安全 `rendered_context` 和显式 history；
- 真实 MCP Client、MCP Inspector 和跨用户隔离验收；
- 框架无关的 BeforeRun/AfterRun Hook、Bridge 和 Runner；
- 可配置的真实 OpenAI-compatible 结构化抽取与固定离线 backend；
- 两套独立 Agent profile、真实 HTTP + PostgreSQL 跨 Agent 端到端测试。

PostgreSQL schema、Repository、连接池、独立 migration 命令和 Linux systemd
直部署样例已经完成。Repository contract 与 MCP 重启套件已在隔离的真实 RDS
测试库通过，SQLite 原型运行路径、adapter、migration 和专项测试已删除。
公网 HTTPS、真实远端网络、压测和现场演示仍属于阶段六部署验收。

## 架构

```text
Codex / LangChain / self-hosted Agent / MCP client
              │  私网 HTTP / 公网 HTTPS + Bearer Token
              ▼
       memory_mcp.server（身份与工具边界）
              │  trusted PrincipalContext
              ▼
       memory_mcp.core.application
              │
      Domain / Ports / Policy
              │
              ▼
       PostgreSQL Repository
```

服务端不依赖阿里云百炼或其他 Agent 平台。任一支持远程 Streamable HTTP MCP
的 Host 都可以直接配置 URL 和认证信息；不支持生命周期 Hook 的 Host 可以通过
阶段五的 Bridge/Runner 接入。

调用方不能提交 `owner_id`。服务端只根据可信 token 映射得到
`owner_key + tenant_id + subject_id + client_id + scopes`，因此同一用户的不同
Agent 可以共享记忆，不同用户即使猜中 memory/review identifier 也无法读取。

## 当前 MCP 工具

| 工具 | Scope | 作用 |
| --- | --- | --- |
| `capture_completed_turn` | `memory:write` | 提交一个成功完成的对话轮次 |
| `list_memories` | `memory:read` | 列出当前用户的活动记忆 |
| `get_memory` | `memory:read` | 读取当前记忆；显式请求可包含 revision history |
| `recall_memory` | `memory:read` | owner-first 召回 active/current 记忆 |
| `list_pending_reviews` | `memory:review` | 查看需要用户确认的候选 |
| `confirm_pending_memory` | `memory:review` | 确认并保存 pending 候选 |
| `reject_pending_memory` | `memory:review` | 拒绝 pending 候选 |

`capture_completed_turn` 使用 `contract_version=1` 和稳定 `event_id`。相同
event 与相同 payload 重试会返回 replay；相同 event 携带不同 payload 会返回
`idempotency_conflict`。

## 本地快速启动

项目要求 Python 3.14，使用 [uv](https://docs.astral.sh/uv/) 管理环境。

```bash
cp .env.example .env
# 配置 PostgreSQL DSN 和本地随机演示 token，然后先执行 migration。
uv sync
uv run memory-mcp-db migrate
uv run memory-mcp
```

把 `.env` 中的演示 token 替换为本地随机值后，服务默认提供：

- MCP：`http://127.0.0.1:8765/mcp`
- 健康检查：`http://127.0.0.1:8765/health`

另一个终端可使用不在命令行暴露 Token 的最小真实客户端验证：

```bash
uv run python examples/memory_mcp_client.py --profile agent-a tools

uv run python examples/memory_mcp_client.py --profile agent-a memories

uv run python examples/memory_mcp_client.py \
  --profile agent-b recall \
  --scenario general-work --query '项目周报偏好' --subject weekly-report
```

默认组合根使用固定离线 extractor，因此服务启动后捕获链路可直接运行；设置
`MEMORY_MCP_EXTRACTOR_BACKEND=openai-compatible` 后使用 `CHAT_MODEL_*`
配置真实结构化模型。配置缺失会在启动时安全失败，不会以
`capture_not_configured` 半配置运行。完整的三身份手工闭环见
[端到端使用文档](docs/usage.md)，所有默认值和 fixed/test/production 边界见
[配置参考](docs/configuration.md)。

## PostgreSQL 与 ECS 部署

PostgreSQL migration 必须在启动服务前显式执行：

```bash
uv run memory-mcp-db migrate
uv run memory-mcp-db health
uv run memory-mcp
```

ECS 默认使用 `uv + systemd`，不要求 Docker 或 Nginx。同一 VPC/VPN 内的 Agent
可以直接访问 ECS 私网地址和 MCP 端口；需要公网接入时，由 ALB/CLB 等云负载
均衡器提供 HTTPS，再转发到该私网端口。完整步骤见
[阿里云 ECS 远程 MCP 部署](docs/deployment/aliyun-ecs.md)。

## 配置

所有服务配置使用 `MEMORY_MCP_` 前缀。关键字段：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_DATABASE_URL` | 无 | 必需的 PostgreSQL DSN，按 secret 处理 |
| `MEMORY_MCP_DATABASE_POOL_MIN_SIZE` | `1` | PostgreSQL 最小连接数 |
| `MEMORY_MCP_DATABASE_POOL_MAX_SIZE` | `5` | PostgreSQL 最大连接数 |
| `MEMORY_MCP_DATABASE_MIGRATE_ON_STARTUP` | `false` | ECS 应保持关闭并独立迁移 |
| `MEMORY_MCP_HOST` | `127.0.0.1` | 本地默认；ECS 远程接入设置为 `0.0.0.0` 并限制安全组 |
| `MEMORY_MCP_PORT` | `8765` | 监听端口 |
| `MEMORY_MCP_MCP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `MEMORY_MCP_DEMO_TOKENS_JSON` | `{}` | token 到可信 principal 的映射；为空时拒绝启动 |
| `MEMORY_MCP_MAX_CAPTURE_CHARACTERS` | `100000` | 单轮最大字符数 |
| `MEMORY_MCP_RECALL_MAX_ITEMS` | `10` | 单次召回条数硬上限 |
| `MEMORY_MCP_RECALL_MAX_TOKEN_BUDGET` | `1200` | 渲染上下文 token 估算硬上限 |
| `MEMORY_MCP_EXTRACTOR_BACKEND` | `fixed` | `fixed` 或 `openai-compatible` |
| `MEMORY_MCP_FIXED_CANDIDATES_JSON` | `[]` | 固定后端精确证据夹具 |

默认正式场景为 `general-work`，允许 `preference`、`stable_context`、
`ongoing_item` 和 `decision`。场景词义由
`memory_mcp.scenarios.GeneralWorkPolicy` 声明，不再通过服务环境变量拼装。

演示 token 映射仅用于课题原型。公网演示必须使用 HTTPS、高熵随机 Token 和受限
安全组，但仍不得把它描述为生产 OAuth。生产化时应替换为标准授权服务器和正式
TokenVerifier。

## 开发与验收

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
openspec-cn validate add-general-memory-core --strict
```

阶段一至四架构、身份、捕获、生命周期、召回和历史验收见
[实现基线](docs/memory/implementation-baseline.md)。完整实施顺序以
[OpenSpec tasks](openspec/changes/add-general-memory-core/tasks.md) 为准。

阶段五文档：

- [整体设计](docs/design.md)
- [配置参考](docs/configuration.md)
- [测试说明](docs/testing.md)
- [端到端使用](docs/usage.md)

真实 PostgreSQL 验收使用专用、可清空且数据库名包含 `test` 的数据库：

```bash
MEMORY_MCP_TEST_DATABASE_URL='postgresql://.../memory_mcp_test' \
  uv run pytest tests/core/test_postgresql_contract.py \
    tests/server/test_postgresql_transport.py
```

日志不会记录对话正文、候选正文、记忆正文、Bearer Token 或 API Key，只记录
request id、工具名、耗时、结果数量和稳定假名引用。
