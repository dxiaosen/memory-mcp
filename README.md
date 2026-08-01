# Memory MCP

Memory MCP 是一个面向不同 Agent 的独立长期记忆服务。Agent 通过标准 MCP
Streamable HTTP 接入；身份、用户隔离、候选抽取、记忆准入、生命周期、召回和
PostgreSQL 持久化由服务端统一负责。

当前已经跑通：

- owner-scoped Memory Core 和 PostgreSQL 权威存储；
- auto-save、pending、discard、blocked 四类准入；
- duplicate Evidence、replacement revision 和 history；
- 十个带认证和 scope 的 MCP 工具，包括幂等记忆/关系撤销；
- revision 的抽取置信度、验证状态、敏感级别、有效期，以及可引用的来源元数据；
- owner-first recall 和安全 rendered context；
- 通用 Agent 生命周期合同、Codex/Claude Code 配置模板和主动召回/捕获；
- 独立轻量 `memory-mcp-agent` 发行包，Agent Host 不安装数据库、模型或 Server；
- 真实 OpenAI-compatible/DeepSeek 结构化候选与关系抽取，以及测试注入的确定性替身；
- `general-work` 与 `investment-research` 两套正式 MemoryProfile；
- owner-scoped 一跳记忆关系、投研关系策略、AfterRun 自动建边和关系感知召回；
- 用户 A / Agent A 写入、用户 A / Agent B 召回、用户 B 不可见的完整闭环。

公网 HTTPS、目标 ECS 安全组、现场脚本和录屏仍属于最后部署交付阶段。核心交付与
主动记忆的实施进度分别见[核心 Tasks](openspec/changes/add-general-memory-core/tasks.md)
和[主动记忆 Tasks](openspec/changes/add-agent-active-memory/tasks.md)。

## 快速开始

Server 开发环境要求 Python 3.14 和 [uv](https://docs.astral.sh/uv/)；独立 Agent
发行包只要求 Python 3.11+。

```bash
uv sync --all-packages --frozen
cp server/.env.example .env
chmod 600 .env
```

`--all-packages` 只用于仓库开发和完整测试，会把 Server 与 Agent 两个 workspace
member 同步到同一个 `.venv`。生产 Server 与远端 Agent 应分别安装各自的发行物。

编辑服务端 `.env`，至少替换：

- PostgreSQL 数据库 DSN；
- `MEMORY_MCP_AUTH_TOKENS` 中长度不少于 32 字符的随机 Token；
- `MEMORY_MCP_MODEL_*` 模型名称、API Key 和可选 Base URL。

再为一个 Agent Host 建立独立配置：

```bash
cp agent/.env.example examples/agent.env
chmod 600 examples/agent.env
```

Agent Host 只填写 `MEMORY_MCP_URL` 和 `MEMORY_MCP_TOKEN`；Token 必须与服务端
Token 映射中的一枚 key 完全相同。`profile_id`、owner、超时、预算和重试无需用户
配置。生产部署应通过 Secret Manager、systemd
`EnvironmentFile` 或编排平台注入，而不是长期保留在项目目录。

Agent 不与 Server 同机时，只需安装轻量 Agent wheel：

```bash
uv tool install /path/to/memory_mcp_agent-0.1.0-py3-none-any.whl
command -v memory-mcp-hook
```

该环境只有 Hook Client 及 HTTP/配置依赖，不包含 `memory-mcp`、
`memory-mcp-db`、PostgreSQL、LangChain 或模型 Provider。构建 wheel 和各宿主配置
见[Agent 主动记忆接入](docs/agents.md)。

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

要让 Agent 每轮自动召回和捕获，而不是等待模型自行选择 MCP 工具，请继续阅读
[Agent 主动记忆接入](docs/agents.md)。`memory-mcp-hook` 接受通用
BeforeRun/AfterRun 合同，并内置兼容 Codex 与 Claude Code；首批配置示例位于
`examples/agents/`。MCP Server 不能远程安装宿主 Hook；Agent Host 仍要一次性安装
轻量命令并注册 Hook，但运行配置始终只有地址和 Token。

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
| `revoke_memory` | `memory:review` | 幂等撤销当前记忆并保留历史和来源 |
| `link_memories` | `memory:write` | 按当前 Profile 策略幂等建立有向关系 |
| `revoke_memory_relation` | `memory:review` | 幂等撤销关系并保留审计历史 |

工具参数不接受 owner。服务端只从可信 Token 映射构造 owner；同一用户的不同 Agent
可以共享记忆，不同用户即使猜中 memory/review ID 也不能读取。

## 文档

从 [文档导航](docs/README.md) 开始。

- [详细总设计](docs/design.md)
- [配置参考](docs/config.md)
- [Agent 主动记忆](docs/agents.md)
- [端到端使用](docs/usage.md)
- [测试与验收](docs/testing.md)
- [日志规范](docs/logging.md)
- [部署指南](docs/deploy.md)

OpenSpec 只承担规范和变更管理：

- [通用记忆核心变更](openspec/changes/add-general-memory-core/)
- [Agent 主动记忆变更](openspec/changes/add-agent-active-memory/)
- [通用元数据增强](openspec/changes/enhance-memory-metadata/)
- [投研记忆配置](openspec/changes/add-investment-research-profile/)
- [通用记忆关系](openspec/changes/add-memory-relations/)
- [自动记忆关系](openspec/changes/automate-memory-relations/)
- [关系证据链与质量评估](openspec/changes/harden-memory-relations/)

## 验证

```bash
.venv/bin/python -m pytest -q
uv run ruff format --check .
uv run ruff check .
openspec-cn validate add-general-memory-core --strict
openspec-cn validate add-agent-active-memory --strict
openspec-cn validate enhance-memory-metadata --strict
openspec-cn validate add-investment-research-profile --strict
openspec-cn validate add-memory-relations --strict
openspec-cn validate automate-memory-relations --strict
openspec-cn validate harden-memory-relations --strict
.venv/bin/python -m evals.runner
```

真实 PostgreSQL 测试必须显式设置
`MEMORY_MCP_TEST_DATABASE_URL`，并且数据库名必须包含 `test`；测试会清空其中的
Memory 表。详细安全步骤见[测试文档](docs/testing.md)。

日志默认只记录稳定引用、状态、数量、错误码和耗时。手工联调时可显式开启内容
日志观察捕获与召回正文；Bearer Token、DSN、模型 API Key 和敏感规则拦截的原文
在任何模式下都不会进入日志。详见[日志规范](docs/logging.md)。
