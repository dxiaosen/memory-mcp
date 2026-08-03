# Memory MCP 测试与验收

本文说明测试层级、运行命令和外部资源安全边界。质量基准的案例与结果见
[投研记忆评测](evaluation.md)，运行配置见[配置参考](config.md)。

## 1. 测试分层

| 层级 | 验证范围 | 外部依赖 |
| --- | --- | --- |
| Core 单元/契约 | owner 隔离、准入、维护闭环、混合召回、关系、事务 | 无 |
| Extraction | 严格 schema、Prompt 边界、共享 ChatModel | 无 |
| MCP transport | 认证、scope、DTO、HTTP/MCP 错误合同 | 无 |
| Agent Hook | BeforeRun/AfterRun、宿主 JSON、状态与重试 | 无 |
| PostgreSQL contract/E2E | migration、SQL 约束、重启、真实远程链路 | 专用测试库 |
| Evaluation | 投研候选、关系、召回和安全指标 | 默认无；live 模式使用模型 API |
| Distribution | Server/Agent wheel 依赖和命令隔离 | 临时虚拟环境 |

测试替身只放在 `tests/support/` 或具体测试文件中，通过端口注入。它们不属于 Server
生产包，也不能由运行配置启用。确定性 PostgreSQL E2E 只替代候选生成，MCP、认证、
Core、数据库和 Hook 仍走真实链路。

## 2. 日常检查

```bash
uv sync --all-packages --frozen
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python -m evals.runner
git diff --check
```

默认评测只计算确定性的 Recall@K 和安全通过率，不调用模型或 PostgreSQL；Recall
通过公开生产 Application Service 和 InMemory Repository 执行，不复制私有排序。
需要验证某个
OpenSpec 变更时执行：

```bash
openspec-cn validate <change-name> --strict
```

测试数量随实现调整，以命令输出为准，不把 skip 描述成已经验证。当前 PostgreSQL
外部用例未显式获得专用数据库时应安全 skip。

## 3. 测试归属

| 路径 | 主要职责 |
| --- | --- |
| `tests/core/` | 领域不变量、Repository 合同、事务和 PostgreSQL |
| `tests/extraction/` | 模型配置、结构化输出和关系提取 |
| `tests/server/` | 应用组合、MCP transport、认证和 PostgreSQL E2E |
| `tests/agent/` | 轻量 Client、Bridge、宿主适配和状态文件 |
| `tests/evaluation/` | 数据集、runner 输出和敏感字段边界 |
| `tests/support/` | 跨测试共享的最小 Fake 与测试 Profile |
| `evals/cases.json` | 52 个中文投研质量案例，不是 pytest 重复用例 |

高风险边界必须保留回归覆盖：可信 Principal 派生、Token 默认 Profile、跨 owner
不可见、scope、跨 Profile 版本幂等与
冲突、事务回滚、pending review、记忆/关系撤销、PostgreSQL migration 与重启、MCP
错误合同、Hook 顶层轮次生命周期，以及候选/关系 schema。只验证私有实现细节或与
更强契约重复的测试可以删除或合并。

## 4. PostgreSQL 安全边界

外部测试会迁移并清空 Memory 表。只允许使用满足以下条件的数据库：

1. database 名称包含 `test`；
2. 是可清空的专用数据库，不与开发或生产共享；
3. 账号拥有 migration、DDL/DML 和 truncate 权限；
4. 操作者显式设置 `MEMORY_MCP_TEST_DATABASE_URL`。

```bash
MEMORY_MCP_TEST_DATABASE_URL='<专用测试库 DSN>' \
  .venv/bin/python -m pytest \
  tests/core/test_postgresql_contract.py \
  tests/server/test_postgresql_transport.py \
  -q
```

如果未满足名称或显式变量检查，测试必须拒绝执行，不能自动读取生产
`MEMORY_MCP_DATABASE_URL`。普通服务的非破坏性检查使用：

```bash
.venv/bin/memory-mcp-db migrate
.venv/bin/memory-mcp-db health
```

`PostgreSQL schema is up to date` 表示全部 migration 已应用且 checksum 一致，不是
“没有数据库表”。

`0001_memory_schema.sql` 安装 `pg_trgm` 扩展、创建全部表和索引（含召回/维护索引）。
开发阶段只有一个 schema 文件，改 schema 直接修改该文件并用 `migrate --rebuild` 重建。
`health` 会同时检查 migration checksum、扩展和四个必需索引。
现有 disposable PostgreSQL contract 还会实际执行混合候选、最终 revision 的批量
Evidence 水合、owner 隔离和重复维护；它不是 SQL 文本断言。未配置专用测试库时这些
用例明确 skip，不能写成”已通过真实 PostgreSQL”。
共享开发/生产库只运行非破坏性的 `migrate/health`；会 truncate 的 contract/E2E 仍
必须使用名称含 `test` 的专用库。

## 5. 投研评测

离线运行：

```bash
.venv/bin/python -m evals.runner
```

真实模型运行只使用进程内 Repository，不写 PostgreSQL：

```bash
.venv/bin/python -m evals.runner \
  --live-model \
  --output evals/results/<safe-result-name>.json
```

live 模式需要有效的 `MEMORY_MCP_MODEL_*` 配置。结果文件只能包含模型/数据集版本、
聚合指标、分类指标和失败 case ID，不保存输入正文、Token 或 API Key。模型结果与
失败分析只在[投研记忆评测](evaluation.md)维护，避免多份快照数字冲突。

当前 v4 离线 Recall 为 15/15，包含零命中、同义改写、报告期/近名实体强干扰、
720 天旧记忆和 101 条大窗口。结果未暴露需要模型参与召回热路径的失败证据，因此
BeforeRun 继续使用 PostgreSQL 混合候选和确定性排序。

## 6. 发布检查

```bash
uv build --package memory-mcp --wheel
uv build --package memory-mcp-agent --wheel
```

发布物需要确认：

- Server wheel 不包含 `tests/`、`evals/` 或测试提取实现；
- Agent wheel 只包含 `memory_mcp_agent` 和 `memory-mcp-hook`；
- Agent 运行依赖不包含 PostgreSQL、LangChain、模型 Provider 或 Server 包；
- Server 与 Agent console script 不相互泄漏。

公网 HTTPS、ECS/RDS 网络、安全组、systemd 权限、滚动升级和回滚属于部署验收，见
[部署指南](deploy.md)。
