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
- BeforeRun/AfterRun Hook、Runner 和独立 Agent profiles；
- fixed 与真实 OpenAI-compatible/DeepSeek 结构化抽取；
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

编辑 `.env`，至少替换：

- PostgreSQL 测试/开发库 DSN；
- `MEMORY_MCP_DEMO_TOKENS_JSON` 中的三枚占位 Token；
- 三个 Hook profile 对应的 Bearer Token；
- 使用真实模型时的 `CHAT_MODEL_*`。

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
.venv/bin/python examples/client.py --profile agent-a tools

.venv/bin/python examples/agent_a.py \
  --conversation-id demo-a \
  --turn-id demo-a-1 \
  --subject weekly-report \
  --input '以后项目周报默认用表格'

.venv/bin/python examples/agent_b.py \
  --conversation-id demo-b \
  --turn-id demo-b-1 \
  --subject weekly-report \
  --input '项目周报 表格'
```

默认 `fixed` extractor 不访问模型网络，但 MCP、鉴权、Core、PostgreSQL 和 Hook
仍是真实链路。切换真实模型和三身份隔离步骤见使用文档。

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

日志不记录对话、候选、记忆、Evidence、Bearer Token、DSN 或模型 API Key，只
记录稳定引用、状态、数量、错误码和耗时。
