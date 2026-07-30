# Agent Lab Memory MCP

一个面向不同 Agent 的独立长期记忆服务。Agent 通过标准 MCP
Streamable HTTP 接入，身份、用户隔离、记忆准入和持久化都由服务端统一负责，
而不是绑定到某个 Agent 框架。

当前已完成阶段一至阶段三：

- owner-scoped Memory Core、版本化 SQLite 迁移和完整性检查；
- 结构化候选、自动保存/待确认/丢弃/敏感拦截四类准入；
- pending 确认与拒绝、失败重处理和 event 级幂等；
- 带 Bearer Token 鉴权的远程 MCP 服务；
- 六个 MCP 工具、稳定错误码、严格输入 DTO 和无正文审计日志；
- 真实 MCP Client、MCP Inspector 和跨用户隔离验收。

阶段四将增加生命周期合并和 `recall_memory`，阶段五增加跨 Agent Hook
SDK，阶段六接入真实结构化模型并固化现场演示。

## 架构

```text
Codex / other Agent / demo client
              │  MCP Streamable HTTP + Bearer Token
              ▼
       memory_mcp（身份与工具边界）
              │  trusted PrincipalContext
              ▼
       Memory Application Service
              │
      Domain / Ports / Policy
              │
              ▼
       SQLite Repository
```

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

## 快速启动

项目要求 Python 3.14，使用 [uv](https://docs.astral.sh/uv/) 管理环境。

```powershell
Copy-Item .env.example .env
uv sync
uv run memory-mcp
```

把 `.env` 中的演示 token 替换为本地随机值后，服务默认提供：

- MCP：`http://127.0.0.1:8765/mcp`
- 健康检查：`http://127.0.0.1:8765/health`

另一个终端可使用最小真实客户端验证：

```powershell
uv run python examples/memory_mcp_client.py `
  --token replace-with-a-long-random-token tools

uv run python examples/memory_mcp_client.py `
  --token replace-with-a-long-random-token memories
```

阶段三的默认命令行组合根暂不绑定某一家模型，因此服务可以启动、鉴权并使用
管理工具；捕获工具在未注入 `CandidateExtractor` 时会明确返回
`capture_not_configured`。阶段三的端到端测试通过固定离线 extractor 重放所有
准入分支，阶段六再把真实模型 backend 接到默认启动入口。

## 配置

所有服务配置使用 `MEMORY_MCP_` 前缀。关键字段：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_DATABASE_PATH` | `.agent-lab/memory.db` | SQLite 文件 |
| `MEMORY_MCP_HOST` | `127.0.0.1` | 监听地址 |
| `MEMORY_MCP_PORT` | `8765` | 监听端口 |
| `MEMORY_MCP_MCP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `MEMORY_MCP_DEMO_TOKENS_JSON` | `{}` | token 到可信 principal 的映射；为空时拒绝启动 |
| `MEMORY_MCP_SCENARIO_ID` | `project-work` | 当前注册场景 |
| `MEMORY_MCP_MAX_CAPTURE_CHARACTERS` | `100000` | 单轮最大字符数 |

演示 token 映射仅用于原型。对公网部署前必须替换为标准授权服务器和正式
TokenVerifier；本期明确不把演示 token 方案描述为生产鉴权。

## 开发与验收

```powershell
uv run pytest
uv run ruff format --check .
uv run ruff check .
openspec-cn validate add-general-memory-core --strict
```

阶段三设计和实测结果见
[阶段三验收记录](docs/memory/phase-three-acceptance.md)。完整实施顺序以
[OpenSpec tasks](openspec/changes/add-general-memory-core/tasks.md) 为准。

日志不会记录对话正文、候选正文、记忆正文、Bearer Token 或 API Key，只记录
request id、工具名、耗时、结果数量和不可逆稳定引用。
