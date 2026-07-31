# Memory MCP

Memory MCP 是一个面向不同 Agent 的独立长期记忆服务。Agent 通过标准 MCP
Streamable HTTP 接入；身份、用户隔离、候选抽取、记忆准入、生命周期、召回和
PostgreSQL 持久化由服务端统一负责。

当前已经跑通：

- owner-scoped Memory Core 和 PostgreSQL 权威存储；
- auto-save、pending、discard、blocked 四类准入；
- duplicate Evidence、replacement revision 和 history；
- 七个带认证和 scope 的 MCP 工具；
- owner-first recall 和安全 rendered context；
- BeforeRun/AfterRun Hook、Runner 和每个 Agent 独立的运行配置；
- 真实 OpenAI-compatible/DeepSeek 结构化抽取，以及测试注入的确定性候选；
- 用户 A / Agent A 写入、用户 A / Agent B 召回、用户 B 不可见的完整闭环。

公网 HTTPS、目标 ECS 安全组、现场脚本和录屏仍属于最后部署交付阶段。完整进度只
看 [OpenSpec tasks](openspec/changes/add-general-memory-core/tasks.md)。

## 快速开始

项目要求 Python 3.14 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --frozen
cp .env.example .env
chmod 600 .env
```

编辑服务端 `.env`，至少替换：

- PostgreSQL 数据库 DSN；
- `MEMORY_MCP_AUTH_TOKENS` 中长度不少于 32 字符的随机 Token；
- `MEMORY_MCP_MODEL_*` 模型名称、API Key 和可选 Base URL。

再为一个 Agent Host 建立独立配置：

```bash
cp examples/.env.example examples/agent.env
chmod 600 examples/agent.env
```

`examples/agent.env` 中的 `MEMORY_HOOK_BEARER_TOKEN` 必须与服务端 Token 映射中的
一枚 Token 完全相同。生产部署应通过 Secret Manager、systemd
`EnvironmentFile` 或编排平台注入，而不是长期保留在项目目录。

然后：

```bash
.venv/bin/memory-mcp-db migrate
.venv/bin/memory-mcp-db health
.venv/bin/memory-mcp
```

默认地址：

```text
MCP:    http://127.0.0.1:8765/mcp
Health: http://127.0.0.1:8765/health
```

另一个终端：

```bash
.venv/bin/python examples/client.py \
  --env-file examples/agent.env \
  tools

.venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id run-a \
  --turn-id run-a-1 \
  --subject weekly-report \
  --input '以后项目周报默认用表格'

.venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id run-b \
  --turn-id run-b-1 \
  --subject weekly-report \
  --input '项目周报 表格'
```

生产进程始终使用真实模型抽取，不提供 fixed 运行时开关。无需模型网络的确定性
验证由 PostgreSQL MCP 端到端测试在代码中注入固定候选；MCP、鉴权、Core、
PostgreSQL 和 Hook 仍走真实链路。真实模型、确定性测试和多身份隔离步骤见使用
文档。

## MCP 工具

| 工具 | Scope | 作用 |
| --- | --- | --- |
| `capture_completed_turn` | `memory:write` | 提交成功完成的顶层轮次 |
| `recall_memory` | `memory:read` | BeforeRun 主动召回 |
| `list_memories` | `memory:read` | 列出当前活动记忆 |
| `get_memory` | `memory:read` | 查看当前详情和可选 history |
| `list_pending_reviews` | `memory:review` | 查看待确认候选 |
| `confirm_pending_memory` | `memory:review` | 确认 pending |
| `reject_pending_memory` | `memory:review` | 拒绝 pending |

工具参数不接受 owner。服务端只从可信 Token 映射构造 owner；同一用户的不同 Agent
可以共享记忆，不同用户即使猜中 memory/review ID 也不能读取。

## 文档

从 [文档导航](docs/README.md) 开始。

- [详细总设计](docs/design.md)
- [配置参考](docs/config.md)
- [端到端使用](docs/usage.md)
- [测试与验收](docs/testing.md)
- [日志规范](docs/logging.md)
- [部署指南](docs/deploy.md)

OpenSpec 只承担规范和变更管理：

- [Proposal](openspec/changes/add-general-memory-core/proposal.md)
- [Technical Decisions](openspec/changes/add-general-memory-core/design.md)
- [Tasks](openspec/changes/add-general-memory-core/tasks.md)
- [Capability Specs](openspec/changes/add-general-memory-core/specs/)

## 验证

```bash
.venv/bin/python -m pytest -q
uv run ruff format --check .
uv run ruff check .
openspec-cn validate add-general-memory-core --strict
```

真实 PostgreSQL 测试必须显式设置
`MEMORY_MCP_TEST_DATABASE_URL`，并且数据库名必须包含 `test`；测试会清空其中的
Memory 表。详细安全步骤见[测试文档](docs/testing.md)。

日志默认只记录稳定引用、状态、数量、错误码和耗时。手工联调时可显式开启内容
日志观察捕获与召回正文；Bearer Token、DSN、模型 API Key 和敏感规则拦截的原文
在任何模式下都不会进入日志。详见[日志规范](docs/logging.md)。
