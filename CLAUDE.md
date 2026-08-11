# CLAUDE.md

本项目指南面向在本仓库工作的开发者和 AI 编程助手。系统设计见 [docs/design.md](docs/design.md)，
使用文档见 [docs/README.md](docs/README.md)，变更与规范见 [openspec/README.md](openspec/README.md)。
本文只放"在这里干活必须知道的事"。

## 项目是什么

Memory MCP 是一个 owner-scoped 长期记忆服务，Agent 通过标准 MCP Streamable HTTP 接入。
身份隔离、候选抽取、准入、生命周期、召回和 PostgreSQL 持久化由服务端统一负责。两个发行包：

- `memory-mcp`（Server，Python 3.14，`server/`）
- `memory-mcp-agent`（轻量 Agent Client，Python 3.11+，`agent/`）

## 常用命令

```bash
# 开发环境（同步 Server + Agent 到同一 .venv）
uv sync --all-packages --frozen

# 静态检查与测试
uv run ruff check .
uv run pytest -q            # 12 skipped 属正常（真实 PostgreSQL 契约测试需显式 DB）
uv run pytest -m unit -q    # 仅 unit 层（秒级）
uv run pytest -m contract -q  # 仅 contract 层
uv run pytest -m integration -q  # 仅 integration 层

# 真实 PostgreSQL 契约测试（可选，需显式设置，库名必须含 "test"）
MEMORY_MCP_TEST_DATABASE_URL=postgres://... uv run pytest tests/contract/test_postgresql_contract.py

# OpenSpec 规范校验
openspec-cn validate <change-name> --strict

# 质量评测
uv run python -m evals.runner --mode deterministic    # CI 门禁，确定性
uv run python -m evals.runner --mode live-extraction  # 真实模型抽取
uv run python -m evals.runner --mode live-embedding   # 真实向量召回

# 本地启动 Server
cp server/.env.example .env && chmod 600 .env   # 编辑 DSN/Token/模型
.venv/bin/memory-mcp-db migrate
.venv/bin/memory-mcp-db health
.venv/bin/memory-mcp
```

Pyright 已安装（`uv run pyright`）；类型检查依赖 ruff（E/F/UP/B/RUF 规则集）+ pyright + Pydantic 运行期校验。

## 架构铁律（不可违反）

1. **Core 自包含**：`server/src/memory_mcp/core/{domain,application,ports}` 不得导入
   `mcp`、`httpx`、`starlette`、`psycopg`、`memory_mcp.extraction`、`memory_mcp.settings`、
   `memory_mcp.auth` 或根包非 core 模块。日志与异常基类在 `core/support/` 下，Core 通过它
   获取这些能力，不回引根包。`tests/core/test_dependency_boundaries.py` 用 AST 扫描强制
   该不变量——改 Core 导入后先跑该测试。
2. **owner 只来自认证上下文**：工具参数不接受 owner；`PrincipalContext` 由 `auth.py`
   从已验证 Token 派生，`owner_key = tenant_id:subject_id`，团队 owner 为 `tenant_id:team:team_id`。
3. **Profile 是场景边界**：通用 Core 不含投研词义；`profiles/` 通过 `MemoryProfile` 协议
   声明 memory_type、关系策略、召回优先级。新增场景 = 新 Profile，不改 Core。
4. **PostgreSQL 是唯一权威存储**：`InMemoryMemoryRepository` 仅用于离线契约测试。无 SQLite。
5. **不虚构能力**：文档只描述已实现内容；OpenSpec `tasks.md` 勾选状态必须证据化，未实现
   的不得勾选。

## 关键模块速查

| 关注点 | 入口 |
| --- | --- |
| 组合根 | `server/src/memory_mcp/app.py` `create_memory_mcp_server` |
| 应用门面 | `core/application/service.py` `MemoryService` |
| 捕获编排 | `core/application/capture_service.py` |
| 候选处理（准入/去重/替代） | `core/application/candidate_processing.py` |
| 召回排序 | `core/application/recall_service.py` |
| 准入策略 | `core/application/admission.py` |
| 自动关系 | `core/application/automatic_relations.py` |
| 维护循环 | `core/application/maintenance_service.py` + `app.py` `_run_maintenance_loop` |
| 团队提取 | `core/application/team_extraction_service.py` + `app.py` `_run_team_extraction_loop` |
| PostgreSQL 适配 | `core/adapters/postgresql/`（repository/recall/maintenance/mapping/schema） |
| MCP 工具 | `tools/`（capture/memory/recall/review/shared） |
| 认证 | `auth.py` `StaticTokenVerifier` / `current_request_principal` |
| Agent Hook | `agent/src/memory_mcp_agent/`（bridge=生命周期，client=HTTP，hosts=宿主适配） |

## 改动时的检查清单

- 改了 Core 层导入 → `uv run pytest tests/contract/test_dependency_boundaries.py`（必须过）。
- 改了 schema → 编辑 `0001_memory_schema.sql` 并 `migrate --rebuild`；更新 `schema.py`
  的 `_REQUIRED_INDEXES`/`_REQUIRED_EXTENSIONS` 与 `testing.md` 索引数。
- 改了召回打分常量 → 同步 `design.md` §9.3 常量表与 evals 阈值。
- 加了 MCP 工具 → 更新 `design.md` §6.2、README 工具表、`enforce_strict_tool_arguments`。
- 加了环境变量 → 同步 `settings.py`、`.env.example`、`docs/config.md`。
- 改了记忆领域字段 → 同步 `0001_memory_schema.sql` CHECK 约束、`mapping.py`、`schemas.py` DTO。
- 改了日志事件/字段 → 同步 `docs/logging.md` 事件表、`tests/unit/test_logging_events.py`。
  日志约束（**当前为开发阶段，已临时放开**）：Token/Secret 仍脱敏；正文字段
  （prompt/query/answer/content/source_expression）在开发阶段不脱敏，失败日志
  直接记录实际输入以便排障。上线前需恢复完整脱敏集并重置 `_SENSITIVE_FIELD_NAMES`。
  同一错误只记一次；`log_event` 的 `error_type`/`error_message` 应尽量带上异常类型与消息。

## OpenSpec 工作流

变更走 OpenSpec：`openspec/changes/<name>/` 下 `proposal.md`（为什么）、`specs/`（规范增量）、
`design.md`（设计决策）、`tasks.md`（可执行任务，勾选需证据化）。`docs/design.md` 是当前
系统事实来源，OpenSpec 只管变更历史。详见 `.claude/commands/opsx/` 与 `openspec/README.md`。

## Memory MCP 测试使用边界（当作为 Memory MCP 客户端测试时）

当本仓库被用作 Memory MCP 的测试客户端时，Claude 须遵守：

- 长期记忆只走 Memory MCP；不使用 Claude 内置 `MEMORY.md` 或项目 memory 做长期记忆。
- 召回由 BeforeRun Hook 自动处理；**捕获由模型自主决定是否调用 `capture_completed_turn`**——
  仅在一轮对话产生值得跨会话记住的持久事实/偏好/决策/判断时调用，不在仅查看/查询/管理记忆的轮次
  或闲聊中调用。身份与幂等字段（event_id/observed_at/contract_version）由服务器组装，
  模型只传 conversation_id/turn_id/user_input/final_output。
- **业务更新不是记忆管理命令**：用户改变/修正/替换某个研究判断、或说某事实支持/挑战/威胁另一判断，
  都是普通对话语义，由 capture + 服务端候选抽取自动处理（replacement / supersede / 生命周期 / 自动关系）。
  **严禁**因此主动调用 `revoke_memory`、`confirm_pending_memory`、`link_memories`、
  `revoke_memory_relation` 等 mutation 工具。业务修正 -> capture 自动 replacement；显式记忆管理 -> review tools。
- mutation 工具仅在用户**显式要求管理已存储的 Memory MCP 记录**时调用（如「撤销 memory_id=xxx」
  「确认这条 Pending」「删除这条关系」「手动把 A 和 B 建成 challenges」）。
