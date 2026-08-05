# Memory MCP 测试与验收

质量基准案例与结果见[投研记忆评测](evaluation.md)，运行配置见[配置参考](config.md)。

## 1. 测试分层与归属

| 层级 | 验证范围 | 外部依赖 | 路径 |
| --- | --- | --- | --- |
| Core 单元/契约 | owner 隔离、准入、维护闭环、混合召回、关系、事务 | 无 | `tests/core/` |
| Extraction | 严格 schema、Prompt 边界、共享 ChatModel | 无 | `tests/extraction/` |
| MCP transport | 认证、scope、DTO、HTTP/MCP 错误合同 | 无 | `tests/server/` |
| Agent Hook | BeforeRun/AfterRun、宿主 JSON、状态与重试 | 无 | `tests/agent/` |
| PostgreSQL contract/E2E | migration、SQL 约束、重启、真实远程链路 | 专用测试库 | `tests/core/`+`tests/server/` |
| Evaluation | 投研候选、关系、召回和安全指标 | 默认无；live 用模型 API | `tests/evaluation/` |
| Distribution | Server/Agent wheel 依赖和命令隔离 | 临时虚拟环境 | — |

补充约束：

| 项 | 说明 |
| --- | --- |
| `evals/cases.json` | 52 个中文投研质量案例（非 pytest 用例） |
| 测试替身 | 只放在 `tests/support/` 或具体测试文件中，通过端口注入，不属于 Server 生产包 |
| PostgreSQL E2E | 只替代候选生成；MCP、认证、Core、数据库和 Hook 仍走真实链路 |

高风险边界必须保留回归覆盖：可信 Principal 派生、Token 默认 Profile、跨 owner 不可见、
scope、跨 Profile 版本幂等与冲突、事务回滚、pending review、记忆/关系撤销、PostgreSQL
migration 与重启、MCP 错误合同、Hook 顶层轮次生命周期，以及候选/关系 schema。

## 2. 日常检查

```bash
uv sync --all-packages --frozen
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python -m evals.runner
git diff --check
openspec-cn validate <change-name> --strict
```

| 项 | 说明 |
| --- | --- |
| 默认评测 | 只计算确定性 Recall@K 和安全通过率，不调用模型或 PostgreSQL |
| Recall 执行 | 通过公开生产 Application Service 和 InMemory Repository，不复制私有排序 |
| PostgreSQL 外部用例 | 未显式获得专用数据库时应安全 skip |
| 测试数量 | 以命令输出为准 |

## 3. PostgreSQL 安全边界

外部测试会迁移并清空 Memory 表。专用测试库必须满足：

| # | 要求 |
| --- | --- |
| 1 | database 名称含 `test` |
| 2 | 可清空的专用库，不与开发/生产共享 |
| 3 | 账号拥有 migration、DDL/DML 和 truncate 权限 |
| 4 | 操作者显式设置 `MEMORY_MCP_TEST_DATABASE_URL` |

```bash
MEMORY_MCP_TEST_DATABASE_URL='<专用测试库 DSN>' .venv/bin/python -m pytest \
  tests/core/test_postgresql_contract.py tests/server/test_postgresql_transport.py -q

# 共享库只运行非破坏性命令
.venv/bin/memory-mcp-db migrate
.venv/bin/memory-mcp-db health
```

未满足检查时测试拒绝执行，不自动读取生产 `MEMORY_MCP_DATABASE_URL`。
`Memory PostgreSQL is healthy` 表示 migration checksum 一致、扩展可用且必需索引存在。

| 项 | 说明 |
| --- | --- |
| `0001_memory_schema.sql` | 安装 `pg_trgm` 与 `vector` 扩展、创建全部表和索引 |
| `health` 检查 | migration checksum、扩展和五个必需索引（含 `memory_revisions_embedding_idx`） |
| 改 schema | 直接修改该文件并用 `migrate --rebuild` 重建 |

## 4. 投研评测

```bash
.venv/bin/python -m evals.runner
.venv/bin/python -m evals.runner --live-model --output evals/results/<safe-result-name>.json
```

| 项 | 说明 |
| --- | --- |
| live 模式前提 | 有效的 `MEMORY_MCP_MODEL_*` 配置，只使用进程内 Repository，不写 PostgreSQL |
| 结果文件 | 只包含模型/数据集版本、聚合指标、分类指标和失败 case ID |
| 不保存 | 输入正文、Token 或 API Key |
| 模型结果与分析 | 只在[投研记忆评测](evaluation.md)维护 |
| 当前 v4 离线 Recall | 15/15 |
| BeforeRun | 继续使用 PostgreSQL 混合候选和确定性排序 |

## 5. 发布检查

```bash
uv build --package memory-mcp --wheel
uv build --package memory-mcp-agent --wheel
```

| 确认项 | 说明 |
| --- | --- |
| Server wheel | 不含 `tests/`、`evals/` 或测试提取实现 |
| Agent wheel | 只含 `memory_mcp_agent` 和 `memory-mcp-hook` |
| Agent 运行依赖 | 不含 PostgreSQL、LangChain、模型 Provider 或 Server 包 |
| console script | Server 与 Agent 不相互泄漏 |

公网 HTTPS、ECS/RDS 网络、安全组、systemd 权限、滚动升级和回滚属于部署验收，
见[部署指南](deploy.md)。
